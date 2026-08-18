"""The unified calendar read model.

``GET /api/calendar/events/`` merges Meetings + Tasks (due) + Lead follow-ups
into one normalised ``CalendarEvent`` envelope carrying ``source``,
``source_id``, ``owner_user_id`` and ``color_key``.

The merge happens server-side because each source has its own permission scope,
evaluated independently (see ``scoping.py``).
"""
from datetime import timedelta

from django.db.models import Q

from common.permissions import (
    CRMPermissions,
    check_object_permission,
    get_queryset_for_permission,
)
from meetings import recurrence
from meetings.models import Meeting, MeetingStatusEnum, VisibilityEnum
from meetings.serializers import redact_meeting_payload

from .scoping import allowed_user_ids, actor_id

LAYER_MEETINGS = 'meetings'
LAYER_TASKS = 'tasks'
LAYER_FOLLOW_UPS = 'follow_ups'
LAYER_ACTIVITIES = 'activities'
DEFAULT_LAYERS = (LAYER_MEETINGS, LAYER_TASKS, LAYER_FOLLOW_UPS)
ALL_LAYERS = (LAYER_MEETINGS, LAYER_TASKS, LAYER_FOLLOW_UPS, LAYER_ACTIVITIES)

LAYER_PERMISSION = {
    LAYER_MEETINGS: CRMPermissions.CRM_MEETINGS_VIEW,
    LAYER_TASKS: CRMPermissions.CRM_TASKS_VIEW,
    LAYER_FOLLOW_UPS: CRMPermissions.CRM_LEADS_VIEW,
    LAYER_ACTIVITIES: CRMPermissions.CRM_LEADS_VIEW,
}

MAX_RANGE_DAYS = recurrence.MAX_RANGE_DAYS
MAX_OCCURRENCES_PER_SERIES = recurrence.MAX_OCCURRENCES_PER_SERIES
MAX_EVENTS = 5000

FOLLOW_UP_DURATION = timedelta(minutes=30)


# ---------------------------------------------------------------------------
# Meetings
# ---------------------------------------------------------------------------
def meeting_window_q(range_start, range_end):
    """The only index-friendly shape for a calendar range query."""
    return (
        # non-recurring (or an override row): any overlap with the window
        Q(recurrence_rule__isnull=True, start_at__lt=range_end, end_at__gt=range_start)
        # recurring master: starts before the window ends and has not already ended
        | (Q(recurrence_rule__isnull=False, start_at__lt=range_end)
           & (Q(recurrence_end_at__isnull=True) | Q(recurrence_end_at__gt=range_start)))
    )


def _attendee_payload(meeting):
    return [
        {
            'id': attendee.id,
            'user_id': str(attendee.user_id) if attendee.user_id else None,
            'lead_id': attendee.lead_id,
            'email': attendee.email,
            'display_name': attendee.display_name,
            'role': attendee.role,
            'response_status': attendee.response_status,
            'is_organizer': attendee.is_organizer,
        }
        for attendee in meeting.attendees.all()
    ]


def _attendee_summary(meeting):
    summary = {'accepted': 0, 'declined': 0, 'tentative': 0, 'needs_action': 0}
    mapping = {
        'ACCEPTED': 'accepted', 'DECLINED': 'declined',
        'TENTATIVE': 'tentative', 'NEEDS_ACTION': 'needs_action',
    }
    for attendee in meeting.attendees.all():
        key = mapping.get(attendee.response_status)
        if key:
            summary[key] += 1
    return summary


def serialize_meeting_occurrence(meeting, occurrence_start, source_meeting, request,
                                 names, viewer_id, is_override=False, series=None):
    """One ``CalendarEvent`` for one occurrence.

    ``meeting`` supplies the *content* (an override row when one exists);
    ``source_meeting`` is the row the occurrence was expanded from.
    """
    series_row = series or source_meeting
    duration = recurrence.to_utc(meeting.end_at) - recurrence.to_utc(meeting.start_at)
    start_at = recurrence.to_utc(meeting.start_at) if is_override else occurrence_start
    end_at = start_at + duration

    my_response = None
    is_participant = False
    for attendee in meeting.attendees.all():
        if attendee.user_id and str(attendee.user_id) == str(viewer_id):
            my_response = attendee.response_status
            is_participant = True

    owner_id = str(meeting.owner_user_id)
    payload = {
        'id': f'meeting:{meeting.id}:{occurrence_start.isoformat()}',
        'source': 'meeting',
        'source_id': meeting.id,
        'series_id': series_row.id,
        'uid': str(meeting.uid),
        'occurrence_start': occurrence_start,
        'is_recurring': bool(series_row.recurrence_rule),
        'is_override': is_override,
        'title': meeting.title,
        'description': meeting.description,
        'location': meeting.location,
        'conference_url': meeting.conference_url,
        'start_at': start_at,
        'end_at': end_at,
        'all_day': meeting.all_day,
        'timezone': meeting.timezone,
        'status': meeting.status,
        'transparency': meeting.transparency,
        'visibility': meeting.visibility,
        'meeting_type': meeting.meeting_type,
        'color_key': meeting.color_key,
        'owner_user_id': owner_id,
        'owner_name': names.get(owner_id),
        'attendees': _attendee_payload(meeting),
        'attendee_summary': _attendee_summary(meeting),
        'my_response': my_response,
        'lead': ({'id': meeting.lead_id, 'name': meeting.lead.name}
                 if meeting.lead_id and meeting.lead else None),
        'has_reminders': bool(meeting.reminder_rules.all()),
        'redacted': False,
        'can_edit': check_object_permission(request, meeting, CRMPermissions.CRM_MEETINGS_EDIT),
        'can_delete': check_object_permission(request, meeting, CRMPermissions.CRM_MEETINGS_DELETE),
    }

    is_owner = owner_id == str(viewer_id)
    if meeting.visibility == VisibilityEnum.PRIVATE and not is_owner and not is_participant:
        return redact_meeting_payload(payload, meeting)
    return payload


def collect_meeting_events(request, params, names, allowed_ids):
    """Expand every meeting in range into ``CalendarEvent`` rows."""
    viewer_id = actor_id(request)
    base = get_queryset_for_permission(
        Meeting.objects.all(), request, CRMPermissions.CRM_MEETINGS_VIEW
    )
    queryset = (
        base.filter(meeting_window_q(params['start'], params['end']))
        .filter(owner_user_id__in=list(allowed_ids))
        .exclude(is_deleted=True)
        .select_related('lead')
        .prefetch_related('attendees', 'reminder_rules', 'overrides__attendees',
                          'overrides__reminder_rules', 'overrides__lead')
        .distinct()
    )
    if not params['include_cancelled']:
        queryset = queryset.exclude(status=MeetingStatusEnum.CANCELLED)
    if params['meeting_types']:
        queryset = queryset.filter(meeting_type__in=params['meeting_types'])

    events = []
    truncated = False
    for meeting in queryset:
        if meeting.recurring_parent_id is not None:
            # Overrides are spliced in via their master; skip the standalone row
            # so an edited occurrence is not rendered twice.
            continue

        if not meeting.recurrence_rule or not params['expand_recurring']:
            occurrence_start = recurrence.to_utc(meeting.start_at)
            events.append(serialize_meeting_occurrence(
                meeting, occurrence_start, meeting, request, names, viewer_id
            ))
            continue

        overrides = {
            recurrence.to_utc(override.recurrence_original_start): override
            for override in meeting.overrides.all()
            if not override.is_deleted
        }
        occurrences = recurrence.expand_occurrences(
            meeting, params['start'], params['end'], limit=MAX_OCCURRENCES_PER_SERIES
        )
        if len(occurrences) >= MAX_OCCURRENCES_PER_SERIES:
            truncated = True
        for occurrence_start in occurrences:
            override = overrides.get(occurrence_start)
            row = override or meeting
            if override is not None and not params['include_cancelled'] \
                    and override.status == MeetingStatusEnum.CANCELLED:
                continue
            events.append(serialize_meeting_occurrence(
                row, occurrence_start, meeting, request, names, viewer_id,
                is_override=override is not None, series=meeting,
            ))

    if not params['include_declined']:
        events = [
            event for event in events
            if event.get('my_response') != 'DECLINED'
        ]
    return events, truncated


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------
def collect_task_events(request, params, names, allowed_ids):
    from tasks.models import Task

    tz_name = params['tz']
    base = get_queryset_for_permission(
        Task.objects.all(), request, CRMPermissions.CRM_TASKS_VIEW
    )
    queryset = (
        base.filter(due_date__gte=params['start'], due_date__lt=params['end'])
        .filter(Q(owner_user_id__in=list(allowed_ids))
                | Q(assignee_user_id__in=list(allowed_ids)))
        .select_related('lead')
        .distinct()
    )

    events = []
    for task in queryset:
        due_local_date = recurrence.local_date(task.due_date, tz_name)
        start_at, end_at = recurrence.all_day_bounds(due_local_date, tz_name)
        owner_id = str(task.owner_user_id)
        events.append({
            'id': f'task:{task.id}',
            'source': 'task',
            'source_id': task.id,
            'series_id': None,
            'occurrence_start': recurrence.to_utc(task.due_date),
            'is_recurring': False,
            'is_override': False,
            'title': task.title,
            'description': task.description,
            'start_at': start_at,
            'end_at': end_at,
            'all_day': True,
            'timezone': tz_name,
            'status': task.status,
            'priority': task.priority,
            'color_key': 'task',
            'owner_user_id': owner_id,
            'owner_name': names.get(owner_id),
            'assignee_user_id': str(task.assignee_user_id) if task.assignee_user_id else None,
            'lead': ({'id': task.lead_id, 'name': task.lead.name}
                     if task.lead_id and task.lead else None),
            'due_at': recurrence.to_utc(task.due_date),
            'redacted': False,
            'can_edit': check_object_permission(request, task, CRMPermissions.CRM_TASKS_EDIT),
            'can_delete': check_object_permission(request, task, CRMPermissions.CRM_TASKS_DELETE),
        })
    return events


# ---------------------------------------------------------------------------
# Lead follow-ups
# ---------------------------------------------------------------------------
def collect_follow_up_events(request, params, names, allowed_ids):
    from crm.models import Lead
    from notifications.models import Reminder, ReminderStatus

    base = get_queryset_for_permission(
        Lead.objects.all(), request, CRMPermissions.CRM_LEADS_VIEW
    )
    queryset = (
        base.filter(next_follow_up_at__gte=params['start'],
                    next_follow_up_at__lt=params['end'])
        .filter(Q(owner_user_id__in=list(allowed_ids))
                | Q(assigned_to__in=list(allowed_ids)))
        .distinct()
    )

    leads = list(queryset)
    with_reminders = set(
        Reminder.objects.filter(
            lead_id__in=[lead.id for lead in leads],
            status__in=[ReminderStatus.PENDING, ReminderStatus.PROCESSING],
        ).values_list('lead_id', flat=True)
    )

    events = []
    for lead in leads:
        start_at = recurrence.to_utc(lead.next_follow_up_at)
        owner_id = str(lead.owner_user_id)
        events.append({
            'id': f'follow_up:{lead.id}',
            'source': 'follow_up',
            'source_id': lead.id,
            'series_id': None,
            'occurrence_start': start_at,
            'is_recurring': False,
            'is_override': False,
            'title': f'Follow up with {lead.name}',
            'description': None,
            'start_at': start_at,
            'end_at': start_at + FOLLOW_UP_DURATION,
            'all_day': False,
            'timezone': params['tz'],
            'status': None,
            'color_key': 'follow_up',
            'owner_user_id': owner_id,
            'owner_name': names.get(owner_id),
            'assigned_to': str(lead.assigned_to) if lead.assigned_to else None,
            'lead': {'id': lead.id, 'name': lead.name},
            'has_reminders': lead.id in with_reminders,
            'redacted': False,
            'can_edit': check_object_permission(request, lead, CRMPermissions.CRM_LEADS_EDIT),
            'can_delete': False,
        })
    return events


# ---------------------------------------------------------------------------
# Lead activities (opt-in layer; past facts, not appointments)
# ---------------------------------------------------------------------------
def collect_activity_events(request, params, names, allowed_ids):
    from crm.models import LeadActivity

    base = get_queryset_for_permission(
        LeadActivity.objects.all(), request, CRMPermissions.CRM_LEADS_VIEW
    )
    queryset = (
        base.filter(happened_at__gte=params['start'], happened_at__lt=params['end'])
        .filter(Q(lead__owner_user_id__in=list(allowed_ids))
                | Q(by_user_id__in=list(allowed_ids)))
        .select_related('lead')
        .distinct()
    )

    events = []
    for activity in queryset:
        start_at = recurrence.to_utc(activity.happened_at)
        owner_id = str(activity.by_user_id) if activity.by_user_id else None
        events.append({
            'id': f'activity:{activity.id}',
            'source': 'activity',
            'source_id': activity.id,
            'series_id': None,
            'occurrence_start': start_at,
            'is_recurring': False,
            'is_override': False,
            'title': activity.type,
            'description': activity.content,
            'start_at': start_at,
            'end_at': start_at,
            'all_day': False,
            'timezone': params['tz'],
            'status': None,
            'color_key': 'activity',
            'owner_user_id': owner_id,
            'owner_name': names.get(owner_id) if owner_id else None,
            'lead': ({'id': activity.lead_id, 'name': activity.lead.name}
                     if activity.lead_id and activity.lead else None),
            'redacted': False,
            'can_edit': False,
            'can_delete': False,
        })
    return events


LAYER_COLLECTORS = {
    LAYER_MEETINGS: collect_meeting_events,
    LAYER_TASKS: collect_task_events,
    LAYER_FOLLOW_UPS: collect_follow_up_events,
    LAYER_ACTIVITIES: collect_activity_events,
}


def build_events(request, params):
    """Merge every requested layer into one sorted feed."""
    names = params.get('names') or {}
    requested = params['user_ids']

    events = []
    truncated = False
    denied = set()
    layer_scopes = {}

    for layer in params['layers']:
        permission_key = LAYER_PERMISSION[layer]
        allowed, layer_denied, scope = allowed_user_ids(request, requested, permission_key)
        layer_scopes[layer] = scope
        if scope is None or not allowed:
            denied |= layer_denied
            continue
        denied |= layer_denied

        collector = LAYER_COLLECTORS[layer]
        if layer == LAYER_MEETINGS:
            layer_events, layer_truncated = collector(request, params, names, allowed)
            truncated = truncated or layer_truncated
        else:
            layer_events = collector(request, params, names, allowed)
        events.extend(layer_events)

    events.sort(key=lambda event: (
        event['start_at'], str(event.get('title') or ''), event['id']
    ))
    if len(events) > MAX_EVENTS:
        events = events[:MAX_EVENTS]
        truncated = True

    return {
        'events': events,
        'truncated': truncated,
        'denied_user_ids': sorted(denied),
        'layer_scopes': layer_scopes,
    }
