from datetime import date, datetime, timedelta

from django.db import transaction
from django.utils import timezone as dj_timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiTypes,
    extend_schema,
    extend_schema_view,
)
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from common.mixins import TenantViewSetMixin
from common.permissions import (
    CRMPermissionMixin,
    CRMPermissions,
    HasCRMPermission,
    JWTAuthentication,
    check_object_permission,
    get_queryset_for_permission,
)
from . import recurrence, services
from .models import Meeting, MeetingAttendee, MeetingStatusEnum
from .permissions import CanRespondToMeeting
from .serializers import (
    AddAttendeesSerializer,
    CancelMeetingSerializer,
    CompleteMeetingSerializer,
    MeetingAttendeeSerializer,
    MeetingListSerializer,
    MeetingSerializer,
    RsvpSerializer,
)
import logging

logger = logging.getLogger(__name__)

TRUE_VALUES = {'1', 'true', 'True', 'TRUE', 'yes', 'on'}

EDIT_SCOPE_PARAM = OpenApiParameter(
    name='edit_scope',
    location=OpenApiParameter.QUERY,
    required=False,
    type=str,
    enum=list(services.EDIT_SCOPES),
    description="Which occurrences of a recurring meeting the change applies to. "
                "Defaults to 'this'.",
)
OCCURRENCE_START_PARAM = OpenApiParameter(
    name='occurrence_start',
    location=OpenApiParameter.QUERY,
    required=False,
    type=OpenApiTypes.DATETIME,
    description="UTC start of the occurrence the user clicked. Required when "
                "edit_scope is not 'all'.",
)


def _scope_params(request):
    """Read edit_scope / occurrence_start from either the query string or the body."""
    data = request.data if isinstance(request.data, dict) else {}
    scope = request.query_params.get('edit_scope') or data.get('edit_scope')
    occurrence_start = (
        request.query_params.get('occurrence_start') or data.get('occurrence_start')
    )
    return services.parse_edit_scope(scope), occurrence_start


@extend_schema_view(
    list=extend_schema(description='List meetings (tenant + permission scoped). '
                                   'Soft-deleted rows are hidden unless ?is_deleted=true.'),
    retrieve=extend_schema(description='Retrieve a meeting with attendees and reminder rules'),
    create=extend_schema(description='Create a meeting; accepts nested attendees[] and '
                                     'reminder_rules[] plus recurrence_rule'),
    update=extend_schema(description='Update a meeting with recurrence edit semantics',
                         parameters=[EDIT_SCOPE_PARAM, OCCURRENCE_START_PARAM]),
    partial_update=extend_schema(description='Partially update a meeting with recurrence '
                                             'edit semantics',
                                 parameters=[EDIT_SCOPE_PARAM, OCCURRENCE_START_PARAM]),
    destroy=extend_schema(description='Soft delete a meeting with recurrence edit semantics',
                          parameters=[EDIT_SCOPE_PARAM, OCCURRENCE_START_PARAM]),
)
class MeetingViewSet(CRMPermissionMixin, TenantViewSetMixin, viewsets.ModelViewSet):
    """
    Manage meetings, appointments, demos, and scheduled conversations with leads.

    Use this endpoint when an agent needs to schedule a meeting, update meeting
    time or location, connect a meeting to a lead, invite attendees, set
    reminders, record meeting notes, cancel or complete a meeting, or retrieve
    upcoming calendar events.

    ``start_at``/``end_at`` are UTC instants and ``timezone`` is the IANA zone the
    meeting was authored in. All-day meetings span local midnight to the next
    local midnight in that zone.

    Required permissions are based on crm.meetings actions.
    """

    queryset = Meeting.objects.select_related('lead')
    serializer_class = MeetingSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [HasCRMPermission]
    permission_resource = 'meetings'
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = {
        'lead': ['exact'],
        'start_at': ['gte', 'lte', 'exact'],
        'end_at': ['gte', 'lte', 'exact'],
        'created_at': ['gte', 'lte'],
        'status': ['exact', 'in'],
        'meeting_type': ['exact', 'in'],
        'owner_user_id': ['exact', 'in'],
        'all_day': ['exact'],
        'is_deleted': ['exact'],
        'attendees__user_id': ['exact'],
        'recurring_parent': ['exact', 'isnull'],
    }
    search_fields = ['title', 'location', 'description', 'notes']
    ordering_fields = ['start_at', 'end_at', 'created_at', 'title', 'status', 'meeting_type']
    ordering = ['start_at']

    action_permission_map = {
        'cancel': 'cancel',
        'complete': 'edit',
        'rsvp': 'edit',
        'attendees': 'edit',
        'remove_attendee': 'edit',
        'occurrences': 'view',
        'calendar': 'view',
    }

    def get_permissions(self):
        if self.action == 'rsvp':
            return [CanRespondToMeeting()]
        return super().get_permissions()

    def get_object(self):
        """RSVP must reach meetings the caller does not own.

        ``get_queryset`` is scoped by ``crm.meetings.view``; an attendee holding
        only ``own`` would otherwise get a 404 before ``CanRespondToMeeting``
        ever ran. Fall back to the tenant-scoped queryset and let the object
        permission decide.
        """
        if self.action == 'rsvp':
            from django.shortcuts import get_object_or_404
            queryset = Meeting.objects.filter(
                tenant_id=getattr(self.request, 'tenant_id', None)
            ).prefetch_related('attendees')
            obj = get_object_or_404(queryset, pk=self.kwargs.get('pk'))
            self.check_object_permissions(self.request, obj)
            return obj
        return super().get_object()

    def get_queryset(self):
        queryset = super().get_queryset()
        params = getattr(self.request, 'query_params', {}) if self.request else {}
        if params.get('is_deleted') not in TRUE_VALUES:
            queryset = queryset.exclude(is_deleted=True)
        queryset = queryset.prefetch_related('attendees', 'reminder_rules')
        if params.get('attendees__user_id'):
            queryset = queryset.distinct()
        return queryset

    def get_serializer_class(self):
        """Use lighter serializer for list view"""
        if self.action == 'list':
            return MeetingListSerializer
        return MeetingSerializer

    def perform_create(self, serializer):
        """Auto-set owner_user_id and tenant_id when creating meetings"""
        tenant_id = getattr(self.request, 'tenant_id', None)
        if not tenant_id:
            raise ValidationError({'tenant_id': 'Tenant ID is required'})

        owner_user_id = serializer.validated_data.get('owner_user_id')
        if not owner_user_id:
            owner_user_id = self.request.user_id
            logger.debug(f"Auto-setting owner_user_id to {owner_user_id}")

        serializer.save(tenant_id=tenant_id, owner_user_id=owner_user_id)

    # ------------------------------------------------------------------
    # 4. update -- recurrence edit semantics (B.3)
    # ------------------------------------------------------------------
    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        edit_scope, occurrence_start = _scope_params(request)

        with transaction.atomic():
            target, created = services.resolve_edit_target(
                instance, edit_scope, occurrence_start
            )
            payload = {
                key: value for key, value in request.data.items()
                if key not in ('edit_scope', 'occurrence_start')
            } if isinstance(request.data, dict) else request.data

            serializer = self.get_serializer(target, data=payload, partial=partial)
            serializer.is_valid(raise_exception=True)
            serializer.save()

        if edit_scope == services.EDIT_SCOPE_THIS_AND_FOLLOWING and created:
            master = services.series_master(instance)
            master.refresh_from_db()
            return Response({
                'edit_scope': edit_scope,
                'updated': self.get_serializer(master).data,
                'created': serializer.data,
            })

        response = dict(serializer.data)
        response['edit_scope'] = edit_scope
        return Response(response)

    # ------------------------------------------------------------------
    # 5. destroy -- soft delete with recurrence edit semantics
    # ------------------------------------------------------------------
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        edit_scope, occurrence_start = _scope_params(request)
        result = services.soft_delete(instance, edit_scope, occurrence_start)
        return Response(result, status=status.HTTP_200_OK)

    # ------------------------------------------------------------------
    # 6. cancel
    # ------------------------------------------------------------------
    @extend_schema(
        description='Cancel a meeting (or one occurrence / the tail of a series). '
                    'The row stays visible and is rendered struck through.',
        request=CancelMeetingSerializer,
    )
    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        instance = self.get_object()
        payload = CancelMeetingSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        edit_scope = services.parse_edit_scope(payload.validated_data.get('edit_scope'))

        with transaction.atomic():
            target, _ = services.resolve_edit_target(
                instance, edit_scope, payload.validated_data.get('occurrence_start')
            )
            services.cancel_meeting(
                target,
                getattr(request, 'user_id', None),
                payload.validated_data.get('reason', ''),
            )
            if edit_scope == services.EDIT_SCOPE_ALL:
                Meeting.objects.filter(recurring_parent=target).update(
                    status=MeetingStatusEnum.CANCELLED,
                    cancelled_at=dj_timezone.now(),
                    updated_at=dj_timezone.now(),
                )
        return Response(self.get_serializer(target).data)

    # ------------------------------------------------------------------
    # 7. complete
    # ------------------------------------------------------------------
    @extend_schema(
        description='Mark a meeting (or one occurrence) as completed.',
        request=CompleteMeetingSerializer,
    )
    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        instance = self.get_object()
        payload = CompleteMeetingSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        edit_scope = services.parse_edit_scope(payload.validated_data.get('edit_scope'))

        with transaction.atomic():
            target, _ = services.resolve_edit_target(
                instance, edit_scope, payload.validated_data.get('occurrence_start')
            )
            services.complete_meeting(target, payload.validated_data.get('notes', ''))
        return Response(self.get_serializer(target).data)

    # ------------------------------------------------------------------
    # 8. rsvp
    # ------------------------------------------------------------------
    @extend_schema(
        description="Record the caller's own RSVP. Allowed for any attendee, even "
                    'one holding only crm.meetings.view: "own".',
        request=RsvpSerializer,
    )
    @action(detail=True, methods=['post'])
    def rsvp(self, request, pk=None):
        instance = self.get_object()
        payload = RsvpSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        user_id = getattr(request, 'user_id', None)
        occurrence_start = payload.validated_data.get('occurrence_start')

        with transaction.atomic():
            target = instance
            if occurrence_start and instance.recurrence_rule:
                master = services.series_master(instance)
                services.resolve_occurrence_start(
                    master, services.EDIT_SCOPE_THIS, occurrence_start
                )
                target, _ = services.get_or_create_override(master, occurrence_start)

            attendee = target.attendees.filter(user_id=user_id).first()
            if attendee is None:
                if not check_object_permission(
                    request, target, CRMPermissions.CRM_MEETINGS_EDIT
                ):
                    raise PermissionDenied('You are not an attendee of this meeting.')
                attendee = MeetingAttendee.objects.create(
                    tenant_id=target.tenant_id, meeting=target, user_id=user_id
                )
            attendee.response_status = payload.validated_data['response_status']
            attendee.comment = payload.validated_data.get('comment', '')
            attendee.responded_at = dj_timezone.now()
            attendee.save(update_fields=[
                'response_status', 'comment', 'responded_at', 'updated_at',
            ])

        return Response(MeetingAttendeeSerializer(attendee).data)

    # ------------------------------------------------------------------
    # 9. add attendees / 10. remove attendee
    # ------------------------------------------------------------------
    @extend_schema(
        description='Add attendees to a meeting. Each entry needs at least one of '
                    'user_id, lead or email.',
        request=AddAttendeesSerializer,
    )
    @action(detail=True, methods=['post'], url_path='attendees')
    def attendees(self, request, pk=None):
        instance = self.get_object()
        payload = AddAttendeesSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        serializer = MeetingSerializer(context=self.get_serializer_context())
        with transaction.atomic():
            serializer._sync_children(
                instance, payload.validated_data['attendees'], None, replace=False
            )
        instance.refresh_from_db()
        return Response(
            MeetingAttendeeSerializer(instance.attendees.all(), many=True).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(description='Remove an attendee from a meeting.')
    @action(detail=True, methods=['delete'],
            url_path=r'attendees/(?P<attendee_id>[0-9]+)')
    def remove_attendee(self, request, pk=None, attendee_id=None):
        instance = self.get_object()
        deleted, _ = instance.attendees.filter(pk=attendee_id).delete()
        if not deleted:
            return Response({'error': 'Attendee not found'},
                            status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ------------------------------------------------------------------
    # 11. occurrences
    # ------------------------------------------------------------------
    @extend_schema(
        description='Expanded occurrences of one series, for the recurrence preview.',
        parameters=[
            OpenApiParameter(name='start', location=OpenApiParameter.QUERY, required=False,
                             type=OpenApiTypes.DATETIME,
                             description='Window start (UTC ISO-8601). Defaults to the '
                                         'series start.'),
            OpenApiParameter(name='end', location=OpenApiParameter.QUERY, required=False,
                             type=OpenApiTypes.DATETIME,
                             description='Window end (UTC ISO-8601). Defaults to start + 90 days.'),
        ],
    )
    @action(detail=True, methods=['get'], url_path='occurrences')
    def occurrences(self, request, pk=None):
        instance = self.get_object()
        master = services.series_master(instance)

        window_start = recurrence.parse_instant(request.query_params.get('start')) \
            or recurrence.to_utc(master.start_at)
        window_end = recurrence.parse_instant(request.query_params.get('end')) \
            or (window_start + timedelta(days=90))
        if window_end <= window_start:
            raise ValidationError({'end': 'Must be after start.'})
        if (window_end - window_start).days > recurrence.MAX_RANGE_DAYS:
            raise ValidationError({
                'end': f'Range must not exceed {recurrence.MAX_RANGE_DAYS} days.'
            })

        duration = recurrence.to_utc(master.end_at) - recurrence.to_utc(master.start_at)
        overrides = {
            recurrence.to_utc(override.recurrence_original_start): override
            for override in master.overrides.all()
        }

        results = []
        for occurrence_start in recurrence.expand_occurrences(
            master, window_start, window_end
        ):
            override = overrides.get(occurrence_start)
            if override is not None:
                results.append({
                    'occurrence_start': recurrence.to_utc(override.start_at),
                    'occurrence_end': recurrence.to_utc(override.end_at),
                    'is_override': True,
                    'meeting_id': override.pk,
                    'status': override.status,
                })
            else:
                results.append({
                    'occurrence_start': occurrence_start,
                    'occurrence_end': occurrence_start + duration,
                    'is_override': False,
                    'meeting_id': master.pk,
                    'status': master.status,
                })

        return Response({
            'series_id': master.pk,
            'timezone': master.timezone,
            'recurrence_rule': master.recurrence_rule,
            'recurrence_end_at': master.recurrence_end_at,
            'count': len(results),
            'occurrences': results,
        })

    # ------------------------------------------------------------------
    # 12. legacy calendar action
    # ------------------------------------------------------------------
    @extend_schema(
        deprecated=True,
        description='DEPRECATED -- superseded by GET /api/calendar/events/, which merges '
                    'meetings, tasks and lead follow-ups, honours per-user timezones and '
                    'expands recurrence. This action buckets by the UTC start date only. '
                    'Kept for one release; it has no frontend call sites.',
        parameters=[
            OpenApiParameter(
                name='start_date', location=OpenApiParameter.QUERY, required=False,
                type=OpenApiTypes.DATE,
                description='Start date for the calendar range in YYYY-MM-DD format.'
            ),
            OpenApiParameter(
                name='end_date', location=OpenApiParameter.QUERY, required=False,
                type=OpenApiTypes.DATE,
                description='End date for the calendar range in YYYY-MM-DD format.'
            ),
            OpenApiParameter(
                name='month', location=OpenApiParameter.QUERY, required=False, type=str,
                description='Calendar month in YYYY-MM format. Takes precedence over '
                            'start_date/end_date.'
            ),
            OpenApiParameter(
                name='tz', location=OpenApiParameter.QUERY, required=False, type=str,
                description='IANA timezone used for day bucketing. Defaults to UTC.'
            ),
        ],
        responses={200: {
            'type': 'object',
            'properties': {
                'calendar_data': {
                    'type': 'object',
                    'additionalProperties': {
                        'type': 'array',
                        'items': {'$ref': '#/components/schemas/MeetingList'}
                    }
                },
                'total_meetings': {'type': 'integer'},
                'date_range': {
                    'type': 'object',
                    'properties': {
                        'start_date': {'type': 'string', 'format': 'date'},
                        'end_date': {'type': 'string', 'format': 'date'}
                    }
                }
            }
        }}
    )
    @action(detail=False, methods=['get'])
    def calendar(self, request):
        """DEPRECATED. Meetings organised by date. Use /api/calendar/events/."""
        try:
            if not self._has_crm_permission(request, CRMPermissions.CRM_MEETINGS_VIEW):
                raise PermissionDenied({
                    "error": "Permission denied",
                    "detail": "You don't have permission to view meetings"
                })

            start_date_param = request.query_params.get('start_date')
            end_date_param = request.query_params.get('end_date')
            month_param = request.query_params.get('month')
            tz_name = request.query_params.get('tz') or 'UTC'
            if not recurrence.is_valid_timezone(tz_name):
                return Response({'error': f'Unknown timezone "{tz_name}"'},
                                status=status.HTTP_400_BAD_REQUEST)

            if month_param:
                try:
                    year, month = map(int, month_param.split('-'))
                    start_date = date(year, month, 1)
                    if month == 12:
                        end_date = date(year + 1, 1, 1)
                    else:
                        end_date = date(year, month + 1, 1)
                except ValueError:
                    return Response({'error': 'Invalid month format. Use YYYY-MM'},
                                    status=status.HTTP_400_BAD_REQUEST)
            elif start_date_param and end_date_param:
                try:
                    start_date = datetime.strptime(start_date_param, '%Y-%m-%d').date()
                    end_date = datetime.strptime(end_date_param, '%Y-%m-%d').date()
                except ValueError:
                    return Response({'error': 'Invalid date format. Use YYYY-MM-DD'},
                                    status=status.HTTP_400_BAD_REQUEST)
            else:
                today = date.today()
                start_date = date(today.year, today.month, 1)
                if today.month == 12:
                    end_date = date(today.year + 1, 1, 1)
                else:
                    end_date = date(today.year, today.month + 1, 1)

            window_start, _ = recurrence.all_day_bounds(start_date, tz_name)
            window_end, _ = recurrence.all_day_bounds(end_date, tz_name)

            meetings = list(
                get_queryset_for_permission(
                    Meeting.objects.filter(
                        start_at__gte=window_start, start_at__lt=window_end
                    ).exclude(is_deleted=True),
                    request,
                    CRMPermissions.CRM_MEETINGS_VIEW,
                ).select_related('lead').order_by('start_at')
            )

            calendar_data = {}
            for meeting in meetings:
                # Bucket in the caller's zone, never the UTC date (plan A.7 rule 4).
                key = recurrence.local_date(meeting.start_at, tz_name).isoformat()
                calendar_data.setdefault(key, []).append(MeetingListSerializer(meeting).data)

            return Response({
                'calendar_data': calendar_data,
                'total_meetings': len(meetings),
                'timezone': tz_name,
                'deprecated': 'Use GET /api/calendar/events/ instead.',
                'date_range': {
                    'start_date': start_date.isoformat(),
                    'end_date': (end_date - timedelta(days=1)).isoformat()
                }
            })

        except PermissionDenied:
            raise
        except Exception as e:
            logger.error(f"Error in calendar view: {str(e)}")
            return Response(
                {'error': 'Failed to fetch calendar data'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
