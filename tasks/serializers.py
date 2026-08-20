from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from common.mixins import TenantMixin
from meetings import recurrence

from . import services
from .models import Task, TaskChecklistItem, TaskRelatedTypeEnum


class TaskChecklistItemSerializer(serializers.ModelSerializer):
    """
    Serialize one line of a task's checklist.

    Agents use this schema to add, tick off, reorder and remove the individual
    steps of a task instead of rewriting a whole JSON document.
    """

    class Meta:
        model = TaskChecklistItem
        fields = ['id', 'task', 'text', 'is_done', 'order_index', 'done_at',
                  'created_at', 'updated_at']
        read_only_fields = ['id', 'task', 'done_at', 'created_at', 'updated_at']
        extra_kwargs = {
            'id': {'help_text': 'Unique numeric identifier for this checklist item. Read-only.'},
            'task': {'help_text': 'Numeric ID of the task this item belongs to. Read-only.'},
            'text': {'help_text': 'The step to be done, e.g. "Collect PAN card".'},
            'is_done': {'help_text': 'True when this step has been completed.'},
            'order_index': {'help_text': 'Sort position within the checklist; lower sorts first.'},
            'done_at': {'help_text': 'Timestamp this item was ticked, in ISO 8601. Read-only.'},
        }


class RelatedLinkMixin:
    """Shared ``related_label`` resolution and ``related_*`` write validation.

    The label is resolved through ``tasks.services``, which filters on the task's
    own ``tenant_id`` -- so a ``related_id`` that belongs to another tenant comes
    back as ``null`` rather than disclosing that tenant's project or lead name.
    """

    def _label_cache(self):
        # One cache per serializer instance; primed in bulk by the ViewSet for
        # list responses so a page of tasks does not fan out into N queries.
        context = self.context
        cache = context.get('related_label_cache')
        if cache is None:
            cache = {}
            context['related_label_cache'] = cache
        return cache

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_related_label(self, obj):
        return services.resolve_related_label(
            obj.tenant_id, obj.related_type, obj.related_id,
            cache=self._label_cache(),
        )

    @extend_schema_field(serializers.IntegerField())
    def get_checklist_total_count(self, obj):
        value = getattr(obj, 'checklist_total_count', None)
        if value is None:
            return obj.checklist_items.count()
        return value

    @extend_schema_field(serializers.IntegerField())
    def get_checklist_done_count(self, obj):
        value = getattr(obj, 'checklist_done_count', None)
        if value is None:
            return obj.checklist_items.filter(is_done=True).count()
        return value

    def _tenant_id(self):
        request = self.context.get('request')
        tenant_id = getattr(request, 'tenant_id', None) if request else None
        if tenant_id:
            return tenant_id
        return getattr(self.instance, 'tenant_id', None) if self.instance else None

    def validate(self, attrs):
        attrs = super().validate(attrs)
        instance = getattr(self, 'instance', None)

        def current(name):
            if name in attrs:
                return attrs[name]
            return getattr(instance, name, None)

        related_type = current('related_type') or TaskRelatedTypeEnum.NONE
        related_id = current('related_id')
        lead = current('lead')

        # A LEAD-typed task may name the lead either way round; the redundancy
        # between ``lead`` and ``related_id`` is intentional (see Task.save).
        if related_type == TaskRelatedTypeEnum.LEAD and not related_id and lead:
            related_id = getattr(lead, 'pk', lead)
            attrs['related_id'] = related_id

        if related_type == TaskRelatedTypeEnum.NONE:
            if related_id and not lead:
                raise serializers.ValidationError({
                    'related_id': 'related_id requires a related_type other than NONE.'
                })
        elif related_id:
            tenant_id = self._tenant_id()
            if tenant_id and not services.related_exists(tenant_id, related_type, related_id):
                # Deliberately the same message whether the row is missing or
                # simply belongs to someone else -- do not confirm existence.
                raise serializers.ValidationError({
                    'related_id': f'No {related_type} with id {related_id} in this tenant.'
                })
        elif related_type != TaskRelatedTypeEnum.NONE and not lead:
            raise serializers.ValidationError({
                'related_id': f'related_type {related_type} requires a related_id.'
            })

        tz_name = current('timezone')
        if tz_name and not recurrence.is_valid_timezone(tz_name):
            raise serializers.ValidationError({
                'timezone': f'"{tz_name}" is not a valid IANA timezone name.'
            })

        rule = current('rrule')
        if rule:
            anchor = current('start_date') or current('due_date')
            if not anchor:
                raise serializers.ValidationError({
                    'rrule': 'A recurring task needs a start_date or a due_date to recur from.'
                })
            if not recurrence.rule_is_valid(rule, anchor, tz_name or 'UTC'):
                raise serializers.ValidationError({
                    'rrule': 'Not a parseable RFC 5545 RRULE.'
                })

        start = current('start_date')
        due = current('due_date')
        if start and due and due < start:
            raise serializers.ValidationError({
                'due_date': 'due_date cannot be before start_date.'
            })

        labels = attrs.get('labels')
        if labels is not None:
            if not isinstance(labels, list) or any(not isinstance(x, str) for x in labels):
                raise serializers.ValidationError({
                    'labels': 'labels must be a list of strings.'
                })
        return attrs


RELATED_TYPE_HELP = (
    'Which CRM object this task is about: LEAD, PROJECT, UNIT, MEETING, or NONE '
    'for a standalone task with no link at all.'
)
RELATED_ID_HELP = (
    'Numeric ID of the related_type object. Must belong to the caller\'s tenant; '
    'ids from other tenants are rejected on write and never resolved on read.'
)
TIMEZONE_HELP = (
    'IANA timezone the task was authored in (e.g. "Asia/Kolkata"). start_date and '
    'due_date remain UTC instants; this is what the UI renders in and what rrule '
    'is expanded against.'
)
RRULE_HELP = (
    'RFC 5545 RRULE without the "RRULE:" prefix, e.g. "FREQ=WEEKLY;BYDAY=MO". '
    'Null for a one-off task. Completing a recurring task spawns the next '
    'occurrence rather than ending the series.'
)


class TaskSerializer(RelatedLinkMixin, TenantMixin):
    """
    Serialize CRM task records.

    Agents use this schema to create and manage follow-up work, reminders,
    assignments and checklists. A task may be linked to a lead, a project, a
    unit or a meeting -- or to nothing at all.
    """

    lead_name = serializers.CharField(
        source='lead.name',
        read_only=True,
        help_text='Display name of the linked lead, if any. Read-only.'
    )
    related_label = serializers.SerializerMethodField(
        help_text='Resolved display name of the related object (lead name, project '
                  'name, unit number or meeting title). Null when there is no link '
                  'or the id does not belong to this tenant. Read-only.'
    )
    owner_user_id = serializers.UUIDField(
        required=False,
        allow_null=True,
        help_text='UUID of the task owner. If omitted during create, the CRM uses the authenticated JWT user_id.'
    )
    checklist_items = TaskChecklistItemSerializer(
        many=True, read_only=True,
        help_text='The task checklist, as individually addressable rows. Read-only '
                  'here -- use the /checklist/ endpoints to change it.'
    )
    checklist_done_count = serializers.SerializerMethodField(
        help_text='Number of checklist items already ticked. Read-only.'
    )
    checklist_total_count = serializers.SerializerMethodField(
        help_text='Total number of checklist items. Read-only.'
    )

    class Meta:
        model = Task
        fields = [
            'id', 'lead', 'lead_name', 'related_type', 'related_id', 'related_label',
            'title', 'description', 'status', 'priority',
            'start_date', 'due_date', 'is_all_day', 'timezone',
            'rrule', 'recurrence_end_at', 'recurring_parent',
            'assignee_user_id', 'reporter_user_id', 'owner_user_id',
            'reminder_minutes_before', 'snoozed_until',
            'order_index', 'labels',
            'checklist', 'checklist_items', 'checklist_done_count',
            'checklist_total_count',
            'attachments_count', 'created_at', 'updated_at', 'completed_at',
        ]
        read_only_fields = [
            'id', 'created_at', 'updated_at', 'completed_at',
            'recurrence_end_at', 'recurring_parent',
        ]
        extra_kwargs = {
            'id': {'help_text': 'Unique numeric identifier for this task. Read-only.'},
            'lead': {'help_text': 'Numeric ID of the lead this task is related to. '
                                  'Optional -- a task no longer needs a lead. Kept in '
                                  'sync with related_id when related_type is LEAD.'},
            'related_type': {'help_text': RELATED_TYPE_HELP},
            'related_id': {'help_text': RELATED_ID_HELP},
            'title': {'help_text': 'Short task title describing the work to be done.'},
            'description': {'help_text': 'Optional detailed task description, instructions, or context.'},
            'status': {'help_text': 'Task status. Valid values are TODO, IN_PROGRESS, DONE, or CANCELLED.'},
            'priority': {'help_text': 'Task priority. Valid values are LOW, MEDIUM, or HIGH.'},
            'start_date': {'help_text': 'Optional start date and time in ISO 8601 date-time format.'},
            'due_date': {'help_text': 'Optional due date and time in ISO 8601 date-time format.'},
            'is_all_day': {'help_text': 'True when the dates are day-granular rather than clock times.'},
            'timezone': {'help_text': TIMEZONE_HELP},
            'rrule': {'help_text': RRULE_HELP},
            'recurrence_end_at': {'help_text': 'Last possible occurrence of the series, '
                                               'derived from UNTIL/COUNT. Read-only.'},
            'recurring_parent': {'help_text': 'ID of the first task in the recurring series. Read-only.'},
            'assignee_user_id': {'help_text': 'Optional UUID of the user assigned to complete this task.'},
            'reporter_user_id': {'help_text': 'Optional UUID of the user who requested or reported this task.'},
            'reminder_minutes_before': {'help_text': 'Minutes before due_date to send an in-app '
                                                     'reminder. Null means no reminder.'},
            'snoozed_until': {'help_text': 'While set in the future, reminders for this task are '
                                           'suppressed. Set by the snooze endpoint.'},
            'order_index': {'help_text': 'Manual sort position; lower sorts first. Set by the reorder endpoint.'},
            'labels': {'help_text': 'List of free-form string labels, e.g. ["urgent", "documents"].'},
            'checklist': {'help_text': 'DEPRECATED raw JSON checklist. Use checklist_items and the '
                                       '/checklist/ endpoints instead.'},
            'attachments_count': {'help_text': 'Number of attachments associated with this task.'},
            'created_at': {'help_text': 'Timestamp when this task was created, in ISO 8601 date-time format. Read-only.'},
            'updated_at': {'help_text': 'Timestamp when this task was last updated, in ISO 8601 date-time format. Read-only.'},
            'completed_at': {'help_text': 'Timestamp when this task was completed, in ISO 8601 date-time format. Read-only.'},
        }


class TaskListSerializer(RelatedLinkMixin, TenantMixin):
    """
    Serialize compact task records for task lists, boards and dashboards.

    Agents use this schema when browsing many tasks without needing the full
    description or the expanded checklist.
    """

    lead_name = serializers.CharField(
        source='lead.name',
        read_only=True,
        help_text='Display name of the linked lead, if any. Read-only.'
    )
    related_label = serializers.SerializerMethodField(
        help_text='Resolved display name of the related object, tenant-scoped. Read-only.'
    )
    checklist_done_count = serializers.SerializerMethodField(
        help_text='Number of checklist items already ticked. Read-only.'
    )
    checklist_total_count = serializers.SerializerMethodField(
        help_text='Total number of checklist items. Read-only.'
    )

    class Meta:
        model = Task
        fields = [
            'id', 'lead', 'lead_name', 'related_type', 'related_id', 'related_label',
            'title', 'status', 'priority', 'start_date', 'due_date', 'is_all_day',
            'timezone', 'rrule', 'order_index', 'labels',
            'checklist_done_count', 'checklist_total_count',
            'assignee_user_id', 'created_at', 'completed_at',
        ]
        read_only_fields = ['id', 'created_at', 'completed_at']
        extra_kwargs = {
            'id': {'help_text': 'Unique numeric identifier for this task. Read-only.'},
            'lead': {'help_text': 'Numeric ID of the linked lead, or null.'},
            'related_type': {'help_text': RELATED_TYPE_HELP},
            'related_id': {'help_text': RELATED_ID_HELP},
            'title': {'help_text': 'Short task title describing the work to be done.'},
            'status': {'help_text': 'Task status. Valid values are TODO, IN_PROGRESS, DONE, or CANCELLED.'},
            'priority': {'help_text': 'Task priority. Valid values are LOW, MEDIUM, or HIGH.'},
            'start_date': {'help_text': 'Optional start date and time in ISO 8601 date-time format.'},
            'due_date': {'help_text': 'Optional due date and time in ISO 8601 date-time format.'},
            'is_all_day': {'help_text': 'True when the dates are day-granular rather than clock times.'},
            'timezone': {'help_text': TIMEZONE_HELP},
            'rrule': {'help_text': RRULE_HELP},
            'order_index': {'help_text': 'Manual sort position; lower sorts first.'},
            'labels': {'help_text': 'List of free-form string labels.'},
            'assignee_user_id': {'help_text': 'Optional UUID of the user assigned to complete this task.'},
            'created_at': {'help_text': 'Timestamp when this task was created, in ISO 8601 date-time format. Read-only.'},
            'completed_at': {'help_text': 'Timestamp when this task was completed, in ISO 8601 date-time format. Read-only.'},
        }


class TaskBulkPatchSerializer(serializers.Serializer):
    """
    Request body for PATCH /api/tasks/bulk/.

    Agents use this to apply the same change (status, priority, assignee, due
    date, labels...) to many tasks in one round trip.
    """

    ids = serializers.ListField(
        child=serializers.IntegerField(),
        allow_empty=False,
        max_length=500,
        help_text='Task IDs to update. Tasks outside your tenant or permission '
                  'scope are silently skipped rather than erroring.'
    )
    patch = serializers.DictField(
        help_text='Field/value pairs to apply to every listed task. Only these '
                  'fields may be bulk-patched: ' + ', '.join(sorted(
                      ['status', 'priority', 'due_date', 'start_date', 'is_all_day',
                       'timezone', 'assignee_user_id', 'reporter_user_id',
                       'owner_user_id', 'labels', 'order_index', 'lead',
                       'related_type', 'related_id', 'reminder_minutes_before']
                  )) + '.'
    )


class TaskReorderSerializer(serializers.Serializer):
    """
    Request body for POST /api/tasks/reorder/.

    Agents use this after a drag-and-drop: send every affected task ID in its new
    visual order and each one's order_index is rewritten to its position.
    """

    ids = serializers.ListField(
        child=serializers.IntegerField(),
        allow_empty=False,
        max_length=1000,
        help_text='Task IDs in their new order. Position 0 gets order_index 0.'
    )


class TaskSnoozeSerializer(serializers.Serializer):
    """Request body for POST /api/tasks/{id}/snooze/."""

    minutes = serializers.IntegerField(
        required=False, min_value=1, max_value=60 * 24 * 365,
        help_text='Push the due date forward by this many minutes.'
    )
    until = serializers.DateTimeField(
        required=False,
        help_text='Absolute ISO 8601 instant to move the due date to. Takes '
                  'precedence over minutes.'
    )

    def validate(self, attrs):
        if not attrs.get('minutes') and not attrs.get('until'):
            raise serializers.ValidationError(
                'Provide either minutes or until.'
            )
        return attrs
