import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone as dj_timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response

from common.mixins import TenantViewSetMixin
from common.permissions import (
    CRMPermissionMixin, HasCRMPermission, JWTAuthentication, check_object_permission,
)
from meetings import recurrence

from . import services
from .models import Task, TaskChecklistItem, TaskStatusEnum
from .serializers import (
    TaskBulkPatchSerializer,
    TaskChecklistItemSerializer,
    TaskListSerializer,
    TaskReorderSerializer,
    TaskSerializer,
    TaskSnoozeSerializer,
)
from .tasks import cancel_reminders_for_task, materialize_for_task

logger = logging.getLogger(__name__)

#: Fields a bulk PATCH is allowed to touch. Everything else (tenant_id,
#: completed_at, recurrence bookkeeping, the deprecated checklist blob) is
#: refused rather than silently ignored, so a typo in the frontend is loud.
BULK_PATCHABLE_FIELDS = {
    'status', 'priority', 'due_date', 'start_date', 'is_all_day', 'timezone',
    'assignee_user_id', 'reporter_user_id', 'owner_user_id', 'labels',
    'order_index', 'lead', 'related_type', 'related_id',
    'reminder_minutes_before', 'description', 'title',
}

ITEM_ID_PARAM = OpenApiParameter(
    name='item_id',
    location=OpenApiParameter.PATH,
    required=True,
    type=int,
    description='Numeric ID of the checklist item, from GET /api/tasks/{id}/checklist/.',
)

TIMEZONE_PARAM = OpenApiParameter(
    name='timezone',
    location=OpenApiParameter.QUERY,
    required=False,
    type=str,
    description='IANA timezone to group the day in, e.g. "Asia/Kolkata". Falls '
                'back to the X-Timezone header, then UTC.',
)


def _caller_timezone(request):
    """Timezone to bucket 'my day' in: ?timezone= > X-Timezone header > UTC."""
    candidate = (
        request.query_params.get('timezone')
        or request.headers.get('X-Timezone')
        or 'UTC'
    )
    return candidate if recurrence.is_valid_timezone(candidate) else 'UTC'


@extend_schema_view(
    list=extend_schema(description='List all tasks (tenant + permission scoped)'),
    retrieve=extend_schema(description='Retrieve a specific task'),
    create=extend_schema(description='Create a new task. lead is optional; use '
                                     'related_type/related_id to link a project, '
                                     'unit or meeting, or leave both unset for a '
                                     'standalone task.'),
    update=extend_schema(description='Update a task'),
    partial_update=extend_schema(description='Partially update a task'),
    destroy=extend_schema(description='Delete a task'),
)
class TaskViewSet(CRMPermissionMixin, TenantViewSetMixin, viewsets.ModelViewSet):
    """
    Manage follow-up tasks, reminders, assignments and checklists across the CRM.

    A task can hang off a lead, a project, a unit or a meeting -- or off nothing
    at all. Use ``related_type`` + ``related_id`` for the link; ``lead`` remains
    available and is kept in sync whenever ``related_type`` is ``LEAD``.

    Use this endpoint when an agent needs to create work items, assign tasks to
    team members, track status, set due dates and reminders, run recurring
    follow-ups, or find overdue and completed work. Beyond the standard CRUD
    routes there are ``bulk``, ``complete``, ``reorder``, ``my-day``, ``snooze``
    and ``checklist`` actions.

    Query parameters support filtering by lead, related object, status,
    priority, assignee, reporter, labels, due date, completion date and created
    date. The search parameter searches task title and description.

    Required permissions are based on crm.tasks actions.
    """

    queryset = Task.objects.select_related('lead')
    serializer_class = TaskSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [HasCRMPermission]
    permission_resource = 'tasks'
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = {
        'lead': ['exact', 'isnull'],
        'related_type': ['exact', 'in'],
        'related_id': ['exact'],
        'status': ['exact', 'in'],
        'priority': ['exact', 'in'],
        'assignee_user_id': ['exact', 'in'],
        'reporter_user_id': ['exact'],
        'owner_user_id': ['exact', 'in'],
        'due_date': ['gte', 'lte', 'exact', 'isnull'],
        'start_date': ['gte', 'lte', 'isnull'],
        'completed_at': ['gte', 'lte', 'isnull'],
        'created_at': ['gte', 'lte'],
        'recurring_parent': ['exact', 'isnull'],
    }
    search_fields = ['title', 'description']
    ordering_fields = [
        'due_date', 'start_date', 'created_at', 'updated_at', 'completed_at',
        'priority', 'status', 'order_index',
    ]
    ordering = ['-created_at']

    # ``bulk``/``reorder``/``complete``/``snooze`` and checklist writes are edits;
    # ``my-day`` and reading a checklist are views.
    action_permission_map = {
        'bulk': 'edit',
        'complete': 'edit',
        'reorder': 'edit',
        'snooze': 'edit',
        'my_day': 'view',
        'checklist': 'view',
        'checklist_item': 'edit',
    }

    # -- queryset ------------------------------------------------------------
    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.annotate(
            checklist_total_count=Count('checklist_items', distinct=True),
            checklist_done_count=Count(
                'checklist_items',
                filter=Q(checklist_items__is_done=True),
                distinct=True,
            ),
        )

    def get_serializer_class(self):
        """Use lighter serializer for list view"""
        if self.action == 'list':
            return TaskListSerializer
        return TaskSerializer

    def get_serializer(self, *args, **kwargs):
        """Prime the related-label cache for collections.

        ``related_label`` is resolved per (related_type, related_id) pair; without
        this a page of 50 tasks would fire 50 extra queries.
        """
        serializer = super().get_serializer(*args, **kwargs)
        if kwargs.get('many') and args and args[0] is not None:
            rows = list(args[0])
            if rows:
                cache = serializer.context.setdefault('related_label_cache', {})
                cache.update(services.resolve_related_labels(
                    rows[0].tenant_id,
                    [(row.related_type, row.related_id) for row in rows],
                ))
                for row in rows:
                    key = (row.related_type, row.related_id)
                    cache.setdefault(key, None)
        return serializer

    def _permission_key(self, permission_action):
        return f'crm.{self.permission_resource}.{permission_action}'

    def perform_create(self, serializer):
        """Auto-set owner_user_id and tenant_id when creating tasks"""
        tenant_id = getattr(self.request, 'tenant_id', None)
        if not tenant_id:
            raise ValidationError({'tenant_id': 'Tenant ID is required'})

        owner_user_id = serializer.validated_data.get('owner_user_id')
        if not owner_user_id:
            owner_user_id = self.request.user_id
            logger.debug(f"Auto-setting owner_user_id to {owner_user_id}")

        logger.debug(f"Creating task with tenant_id={tenant_id}, owner_user_id={owner_user_id}")
        task = serializer.save(tenant_id=tenant_id, owner_user_id=owner_user_id)
        self._sync_reminders(task)

    def perform_update(self, serializer):
        task = serializer.save()
        self._sync_reminders(task, reset=True)

    def _sync_reminders(self, task, reset=False):
        """Keep the notifications.Reminder rows in step with the task.

        Cheap and synchronous: the beat job still sweeps everything every 15
        minutes, this just means a reminder set on a task due in 10 minutes does
        not miss its window.
        """
        try:
            if reset:
                cancel_reminders_for_task(task)
            materialize_for_task(task)
        except Exception:
            logger.exception('Failed to sync reminders for task %s', task.pk)

    # -- bulk ----------------------------------------------------------------
    @extend_schema(
        request=TaskBulkPatchSerializer,
        description='Apply the same partial update to many tasks at once. Body is '
                    '{"ids": [1,2,3], "patch": {"status": "DONE"}}. Returns '
                    '{"updated": n}. Tasks you cannot edit are skipped, not errored.',
    )
    @action(detail=False, methods=['patch'], url_path='bulk')
    def bulk(self, request):
        body = TaskBulkPatchSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        ids = body.validated_data['ids']
        patch = body.validated_data['patch']

        unknown = set(patch) - BULK_PATCHABLE_FIELDS
        if unknown:
            raise ValidationError({
                'patch': 'Not bulk-patchable: %s' % ', '.join(sorted(unknown))
            })
        if not patch:
            return Response({'updated': 0})

        permission_key = self._permission_key('edit')
        updated = 0
        with transaction.atomic():
            rows = self.get_queryset().filter(pk__in=ids)
            for task in rows:
                if not check_object_permission(request, task, permission_key):
                    continue
                serializer = TaskSerializer(
                    task, data=patch, partial=True,
                    context=self.get_serializer_context(),
                )
                serializer.is_valid(raise_exception=True)
                saved = serializer.save()
                self._sync_reminders(saved, reset=True)
                updated += 1
        return Response({'updated': updated})

    # -- complete ------------------------------------------------------------
    @extend_schema(
        request=None,
        description='Toggle a task between DONE and TODO. Completing stamps '
                    'completed_at; un-completing clears it. Completing a '
                    'recurring task also spawns the next occurrence, returned as '
                    '"next_task".',
    )
    @action(detail=True, methods=['post'], url_path='complete')
    def complete(self, request, pk=None):
        task = self.get_object()
        next_task = None

        with transaction.atomic():
            if task.status == TaskStatusEnum.DONE:
                task.status = TaskStatusEnum.TODO
                task.completed_at = None
                task.save()
                materialize_for_task(task)
            else:
                task.status = TaskStatusEnum.DONE
                task.save()
                cancel_reminders_for_task(task)
                next_task = services.spawn_next_occurrence(task)
                if next_task:
                    materialize_for_task(next_task)

        payload = self.get_serializer(self._reload(task)).data
        payload['next_task'] = (
            TaskSerializer(self._reload(next_task), context=self.get_serializer_context()).data
            if next_task else None
        )
        return Response(payload)

    def _reload(self, task):
        """Re-fetch through the annotated queryset so counts are present."""
        if task is None:
            return None
        return self.get_queryset().filter(pk=task.pk).first() or task

    # -- reorder -------------------------------------------------------------
    @extend_schema(
        request=TaskReorderSerializer,
        description='Persist a manual ordering. Body is {"ids": [3,1,2]}; each '
                    'task\'s order_index becomes its position in that list. '
                    'Returns {"updated": n}.',
    )
    @action(detail=False, methods=['post'], url_path='reorder')
    def reorder(self, request):
        body = TaskReorderSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        ids = body.validated_data['ids']

        permission_key = self._permission_key('edit')
        updated = 0
        with transaction.atomic():
            by_id = {task.pk: task for task in self.get_queryset().filter(pk__in=ids)}
            to_save = []
            for position, task_id in enumerate(ids):
                task = by_id.get(task_id)
                if task is None:
                    continue
                if not check_object_permission(request, task, permission_key):
                    continue
                if task.order_index != position:
                    task.order_index = position
                    to_save.append(task)
                updated += 1
            if to_save:
                # bulk_update deliberately: reordering must not restamp
                # updated_at/completed_at through Task.save() for every row.
                Task.objects.bulk_update(to_save, ['order_index'])
        return Response({'updated': updated})

    # -- my day --------------------------------------------------------------
    @extend_schema(
        parameters=[TIMEZONE_PARAM],
        description="The caller's working day, grouped into overdue / today / "
                    "this_week / later / done_today. Buckets are computed in the "
                    "caller's timezone, not UTC. Only tasks assigned to or owned "
                    "by the caller are included.",
    )
    @action(detail=False, methods=['get'], url_path='my-day')
    def my_day(self, request):
        tz_name = _caller_timezone(request)
        user_id = getattr(request, 'user_id', None)

        queryset = self.get_queryset()
        if user_id:
            queryset = queryset.filter(
                Q(assignee_user_id=user_id) | Q(owner_user_id=user_id)
            )
        # A done task is only interesting if it was finished today; anything
        # older would swamp done_today on a busy tenant.
        horizon = dj_timezone.now() - timedelta(days=2)
        queryset = queryset.exclude(
            Q(status=TaskStatusEnum.DONE) & Q(completed_at__lt=horizon)
        ).exclude(status=TaskStatusEnum.CANCELLED).order_by(
            'order_index', 'due_date', 'id'
        )

        rows = list(queryset)
        groups = services.bucket_for_my_day(rows, tz_name)

        context = self.get_serializer_context()
        cache = context.setdefault('related_label_cache', {})
        if rows:
            cache.update(services.resolve_related_labels(
                rows[0].tenant_id,
                [(row.related_type, row.related_id) for row in rows],
            ))

        payload = {
            name: TaskListSerializer(items, many=True, context=context).data
            for name, items in groups.items()
        }
        payload['timezone'] = tz_name
        return Response(payload)

    # -- snooze --------------------------------------------------------------
    @extend_schema(
        request=TaskSnoozeSerializer,
        description='Push a task out. Body is {"minutes": 30} or '
                    '{"until": "2026-09-01T09:00:00Z"}. Moves due_date, records '
                    'snoozed_until, and re-materialises the reminder.',
    )
    @action(detail=True, methods=['post'], url_path='snooze')
    def snooze(self, request, pk=None):
        task = self.get_object()
        body = TaskSnoozeSerializer(data=request.data)
        body.is_valid(raise_exception=True)

        until = body.validated_data.get('until')
        if until is None:
            base = task.due_date or dj_timezone.now()
            base = max(base, dj_timezone.now())
            until = base + timedelta(minutes=body.validated_data['minutes'])

        with transaction.atomic():
            cancel_reminders_for_task(task)
            task.due_date = until
            task.snoozed_until = until
            task.status = (
                TaskStatusEnum.TODO if task.status == TaskStatusEnum.DONE
                else task.status
            )
            task.save()
            materialize_for_task(task)
        return Response(self.get_serializer(self._reload(task)).data)

    # -- checklist -----------------------------------------------------------
    def _checklist_queryset(self, task):
        return TaskChecklistItem.objects.filter(task=task, tenant_id=task.tenant_id)

    @extend_schema(
        request=TaskChecklistItemSerializer,
        responses=TaskChecklistItemSerializer(many=True),
        description='GET lists a task\'s checklist items in order. POST appends a '
                    'new item; order_index defaults to the end of the list.',
    )
    @action(detail=True, methods=['get', 'post'], url_path='checklist')
    def checklist(self, request, pk=None):
        task = self.get_object()

        if request.method == 'GET':
            items = self._checklist_queryset(task)
            return Response(TaskChecklistItemSerializer(items, many=True).data)

        if not check_object_permission(request, task, self._permission_key('edit')):
            raise PermissionDenied('You do not have permission to edit this task.')

        serializer = TaskChecklistItemSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if 'order_index' not in serializer.validated_data:
            last = self._checklist_queryset(task).order_by('-order_index').first()
            serializer.validated_data['order_index'] = (last.order_index + 1) if last else 0
        item = serializer.save(task=task, tenant_id=task.tenant_id)
        return Response(
            TaskChecklistItemSerializer(item).data, status=status.HTTP_201_CREATED
        )

    @extend_schema(
        request=TaskChecklistItemSerializer,
        parameters=[ITEM_ID_PARAM],
        description='PATCH updates one checklist item (text, is_done, order_index); '
                    'DELETE removes it. Ticking an item stamps done_at.',
    )
    @action(detail=True, methods=['patch', 'delete'],
            url_path=r'checklist/(?P<item_id>[^/.]+)')
    def checklist_item(self, request, pk=None, item_id=None):
        task = self.get_object()
        if not check_object_permission(request, task, self._permission_key('edit')):
            raise PermissionDenied('You do not have permission to edit this task.')

        item = self._checklist_queryset(task).filter(pk=item_id).first()
        if item is None:
            raise NotFound('Checklist item not found on this task.')

        if request.method == 'DELETE':
            item.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)

        serializer = TaskChecklistItemSerializer(item, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
