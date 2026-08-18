"""The unified calendar API, mounted at ``/api/calendar/``.

The Django app is named ``scheduling`` on purpose -- a top-level ``calendar``
package shadows the stdlib module Django itself imports.
"""
import logging
from datetime import timedelta

import requests
from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from common.permissions import (
    CRMPermissions,
    JWTAuthentication,
    check_permission,
    get_queryset_for_permission,
    has_module_access,
    is_admin_request,
)
from meetings import recurrence
from meetings.models import Meeting, MeetingStatusEnum, TransparencyEnum, VisibilityEnum

from . import directory
from .permissions import HasJWTIdentity
from .events import (
    ALL_LAYERS,
    DEFAULT_LAYERS,
    LAYER_MEETINGS,
    MAX_EVENTS,
    MAX_RANGE_DAYS,
    build_events,
    meeting_window_q,
)
from .models import CalendarPreference
from .scoping import SCOPE_ALL, actor_id, allowed_user_ids, resolve_scope
from .serializers import (
    AvailabilityRequestSerializer,
    CalendarPreferenceSerializer,
    ConflictRequestSerializer,
)

logger = logging.getLogger(__name__)

TRUE_VALUES = {'1', 'true', 'True', 'TRUE', 'yes', 'on'}

LAYER_METADATA = [
    {'key': 'meetings', 'label': 'Meetings', 'permission': 'crm.meetings.view',
     'color_key': 'meeting', 'default_on': True},
    {'key': 'tasks', 'label': 'Tasks', 'permission': 'crm.tasks.view',
     'color_key': 'task', 'default_on': True},
    {'key': 'follow_ups', 'label': 'Follow-ups', 'permission': 'crm.leads.view',
     'color_key': 'follow_up', 'default_on': True},
    {'key': 'activities', 'label': 'Activities', 'permission': 'crm.leads.view',
     'color_key': 'activity', 'default_on': False},
]

COLOR_TOKENS = {
    'meeting': 'blue', 'demo': 'violet', 'call': 'cyan', 'site_visit': 'teal',
    'internal': 'slate', 'follow_up': 'indigo', 'task': 'amber',
    'activity': 'slate', 'cancelled': 'muted', 'busy': 'slate',
}


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------
def get_or_create_preference(request):
    preference, _ = CalendarPreference.objects.get_or_create(
        tenant_id=request.tenant_id,
        user_id=request.user_id,
        defaults={'timezone': 'UTC'},
    )
    return preference


def caller_timezone(request, override=None):
    if override:
        if not recurrence.is_valid_timezone(override):
            raise ValidationError({'tz': f'"{override}" is not a known IANA timezone name.'})
        return override
    preference = CalendarPreference.objects.filter(
        tenant_id=getattr(request, 'tenant_id', None),
        user_id=getattr(request, 'user_id', None),
    ).only('timezone').first()
    return preference.timezone if preference else 'UTC'


def _csv(value):
    if not value:
        return []
    return [item.strip() for item in value.split(',') if item.strip()]


def _require_meetings_view(request):
    if not has_module_access(request, 'crm'):
        raise PermissionDenied('Permission not granted for crm module.')
    if not check_permission(request, CRMPermissions.CRM_MEETINGS_VIEW):
        raise PermissionDenied('Permission not granted for this module.')


class CalendarBaseView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [HasJWTIdentity]


# ---------------------------------------------------------------------------
# 13. GET /api/calendar/events/
# ---------------------------------------------------------------------------
class CalendarEventsView(CalendarBaseView):
    """The one endpoint the calendar grid runs.

    Merges Meetings + Tasks (due) + Lead follow-ups into a single normalised
    ``CalendarEvent`` feed. Each layer resolves its own permission scope
    independently, which is why the merge cannot happen client-side.
    """

    @extend_schema(
        description='Unified calendar range query across meetings, tasks and lead '
                    'follow-ups. Recurrence is expanded server-side in each '
                    "meeting's own timezone; PRIVATE meetings are redacted to "
                    'free/busy for non-participants.',
        parameters=[
            OpenApiParameter('start', OpenApiTypes.DATETIME, OpenApiParameter.QUERY,
                             required=True, description='Inclusive window start (ISO-8601).'),
            OpenApiParameter('end', OpenApiTypes.DATETIME, OpenApiParameter.QUERY,
                             required=True,
                             description=f'Exclusive window end. end - start must be '
                                         f'<= {MAX_RANGE_DAYS} days.'),
            OpenApiParameter('tz', str, OpenApiParameter.QUERY, required=False,
                             description="IANA zone for day bucketing. Defaults to the "
                                         "caller's CalendarPreference.timezone, else UTC."),
            OpenApiParameter('user_ids', str, OpenApiParameter.QUERY, required=False,
                             description='Comma-separated user UUIDs (team view). '
                                         'Defaults to the caller.'),
            OpenApiParameter('layers', str, OpenApiParameter.QUERY, required=False,
                             description='Comma-separated subset of '
                                         'meetings,tasks,follow_ups,activities.'),
            OpenApiParameter('meeting_types', str, OpenApiParameter.QUERY, required=False),
            OpenApiParameter('include_cancelled', bool, OpenApiParameter.QUERY, required=False),
            OpenApiParameter('include_declined', bool, OpenApiParameter.QUERY, required=False),
            OpenApiParameter('expand_recurring', bool, OpenApiParameter.QUERY, required=False),
        ],
    )
    def get(self, request):
        params = request.query_params

        window_start = recurrence.parse_instant(params.get('start'))
        window_end = recurrence.parse_instant(params.get('end'))
        if window_start is None or window_end is None:
            raise ValidationError({'start': 'start and end are required ISO-8601 datetimes.'})
        if window_end <= window_start:
            raise ValidationError({'end': 'Must be after start.'})
        if (window_end - window_start).days > MAX_RANGE_DAYS:
            raise ValidationError({'end': f'Range must not exceed {MAX_RANGE_DAYS} days.'})

        tz_name = caller_timezone(request, params.get('tz'))
        preference = CalendarPreference.objects.filter(
            tenant_id=request.tenant_id, user_id=request.user_id
        ).first()

        requested_layers = _csv(params.get('layers')) or list(DEFAULT_LAYERS)
        unknown = [layer for layer in requested_layers if layer not in ALL_LAYERS]
        if unknown:
            raise ValidationError({'layers': f'Unknown layers: {", ".join(unknown)}'})

        include_declined = params.get('include_declined')
        if include_declined is None:
            include_declined = bool(preference.show_declined) if preference else False
        else:
            include_declined = include_declined in TRUE_VALUES

        user_ids = _csv(params.get('user_ids')) or [actor_id(request)]

        build_params = {
            'start': window_start,
            'end': window_end,
            'tz': tz_name,
            'user_ids': user_ids,
            'layers': requested_layers,
            'meeting_types': _csv(params.get('meeting_types')),
            'include_cancelled': params.get('include_cancelled') in TRUE_VALUES,
            'include_declined': include_declined,
            'expand_recurring': params.get('expand_recurring', 'true') in TRUE_VALUES
            or params.get('expand_recurring') is None,
            'names': directory.name_map(request.tenant_id),
        }

        result = build_events(request, build_params)

        if all(scope is None for scope in result['layer_scopes'].values()):
            raise PermissionDenied('Permission not granted for this module.')

        return Response({
            'range': {
                'start': window_start,
                'end': window_end,
                'timezone': tz_name,
            },
            'scope': result['layer_scopes'].get(LAYER_MEETINGS),
            'layer_scopes': result['layer_scopes'],
            'requested_user_ids': user_ids,
            'denied_user_ids': result['denied_user_ids'],
            'truncated': result['truncated'],
            'max_events': MAX_EVENTS,
            'count': len(result['events']),
            'events': result['events'],
        })


# ---------------------------------------------------------------------------
# 14. GET /api/calendar/members/
# ---------------------------------------------------------------------------
class CalendarMembersView(CalendarBaseView):
    """Team directory + a stable per-person colour index + in-range counts."""

    @extend_schema(
        description='Tenant users for the calendar left rail. Proxies the SuperAdmin '
                    'user directory through a 300s Django cache. When the caller '
                    'cannot view the team, only the caller is returned.',
        parameters=[
            OpenApiParameter('start', OpenApiTypes.DATETIME, OpenApiParameter.QUERY,
                             required=False),
            OpenApiParameter('end', OpenApiTypes.DATETIME, OpenApiParameter.QUERY,
                             required=False),
            OpenApiParameter('search', str, OpenApiParameter.QUERY, required=False),
        ],
    )
    def get(self, request):
        self_id = actor_id(request)
        scope = resolve_scope(request, CRMPermissions.CRM_MEETINGS_VIEW)
        can_view_team = is_admin_request(request) or scope == SCOPE_ALL

        if not can_view_team:
            members = [{
                'user_id': self_id,
                'name': getattr(request, 'email', '') or self_id,
                'email': getattr(request, 'email', '') or '',
                'color_index': directory.color_index(self_id),
            }]
        else:
            try:
                members = directory.normalized_users(
                    request.tenant_id, search=request.query_params.get('search')
                )
            except requests.HTTPError as exc:
                code = exc.response.status_code if exc.response is not None else None
                logger.warning('Calendar members upstream error: %s', code)
                return Response(
                    {'error': 'Failed to fetch users from auth service', 'status': code},
                    status=status.HTTP_502_BAD_GATEWAY,
                )
            except requests.RequestException as exc:
                logger.error('Calendar members directory unreachable: %s', exc)
                return Response(
                    {'error': f'Auth service unreachable: {exc}'},
                    status=status.HTTP_502_BAD_GATEWAY,
                )

        window_start = recurrence.parse_instant(request.query_params.get('start'))
        window_end = recurrence.parse_instant(request.query_params.get('end'))
        counts = {}
        if window_start and window_end and window_end > window_start:
            counts = self._counts(request, window_start, window_end)

        payload = []
        for member in members:
            payload.append({
                **member,
                'counts': counts.get(member['user_id'],
                                     {'meetings': 0, 'tasks': 0, 'follow_ups': 0}),
                'is_self': member['user_id'] == self_id,
            })

        return Response({
            'can_view_team': can_view_team,
            'self_user_id': self_id,
            'members': payload,
        })

    @staticmethod
    def _counts(request, window_start, window_end):
        from crm.models import Lead
        from tasks.models import Task

        counts = {}

        def bump(user_id, key):
            if not user_id:
                return
            bucket = counts.setdefault(
                str(user_id), {'meetings': 0, 'tasks': 0, 'follow_ups': 0}
            )
            bucket[key] += 1

        meetings = get_queryset_for_permission(
            Meeting.objects.all(), request, CRMPermissions.CRM_MEETINGS_VIEW
        ).filter(meeting_window_q(window_start, window_end)).exclude(is_deleted=True)
        for owner in meetings.values_list('owner_user_id', flat=True):
            bump(owner, 'meetings')

        tasks = get_queryset_for_permission(
            Task.objects.all(), request, CRMPermissions.CRM_TASKS_VIEW
        ).filter(due_date__gte=window_start, due_date__lt=window_end)
        for owner in tasks.values_list('owner_user_id', flat=True):
            bump(owner, 'tasks')

        leads = get_queryset_for_permission(
            Lead.objects.all(), request, CRMPermissions.CRM_LEADS_VIEW
        ).filter(next_follow_up_at__gte=window_start, next_follow_up_at__lt=window_end)
        for owner in leads.values_list('owner_user_id', flat=True):
            bump(owner, 'follow_ups')

        return counts


# ---------------------------------------------------------------------------
# 15. POST /api/calendar/availability/
# ---------------------------------------------------------------------------
class CalendarAvailabilityView(CalendarBaseView):
    """Free/busy blocks + suggested slots.

    Only ``{start, end, reason}`` is returned -- never titles -- so this is safe
    even for users whose events the caller cannot read in full.
    """

    @extend_schema(request=AvailabilityRequestSerializer,
                   description='Free/busy blocks and suggested meeting slots for a set '
                               'of users. Only OPAQUE, non-cancelled, non-deleted '
                               'meetings count as busy.')
    def post(self, request):
        _require_meetings_view(request)
        payload = AvailabilityRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        window_start = recurrence.to_utc(data['start'])
        window_end = recurrence.to_utc(data['end'])
        tz_name = caller_timezone(request, data.get('tz'))
        requested = data.get('user_ids') or [actor_id(request)]
        allowed, denied, scope = allowed_user_ids(
            request, requested, CRMPermissions.CRM_MEETINGS_VIEW
        )
        if scope is None:
            raise PermissionDenied('Permission not granted for this module.')

        busy = {user_id: [] for user_id in sorted(allowed)}
        queryset = (
            get_queryset_for_permission(
                Meeting.objects.all(), request, CRMPermissions.CRM_MEETINGS_VIEW
            )
            .filter(meeting_window_q(window_start, window_end))
            .filter(owner_user_id__in=list(allowed))
            .filter(transparency=TransparencyEnum.OPAQUE)
            .exclude(is_deleted=True)
            .exclude(status=MeetingStatusEnum.CANCELLED)
            .distinct()
        )
        for meeting in queryset:
            owner = str(meeting.owner_user_id)
            duration = recurrence.to_utc(meeting.end_at) - recurrence.to_utc(meeting.start_at)
            for occurrence_start in recurrence.expand_occurrences(
                meeting, window_start, window_end
            ):
                busy.setdefault(owner, []).append({
                    'start': occurrence_start,
                    'end': occurrence_start + duration,
                    'reason': 'meeting',
                })
        for blocks in busy.values():
            blocks.sort(key=lambda block: block['start'])

        slots = self._suggest_slots(
            request, busy, window_start, window_end, tz_name, data
        )

        return Response({
            'busy': busy,
            'suggested_slots': slots,
            'denied_user_ids': sorted(denied),
            'scope': scope,
            'timezone': tz_name,
        })

    @staticmethod
    def _working_hours(request, user_ids):
        preferences = {
            str(pref.user_id): pref
            for pref in CalendarPreference.objects.filter(
                tenant_id=request.tenant_id, user_id__in=list(user_ids)
            )
        }
        return preferences

    def _suggest_slots(self, request, busy, window_start, window_end, tz_name, data):
        duration = timedelta(minutes=data['duration_minutes'])
        step = timedelta(minutes=data['granularity_minutes'])
        user_ids = sorted(busy.keys())
        if not user_ids:
            return []

        preferences = self._working_hours(request, user_ids) \
            if data['respect_working_hours'] else {}
        zone = recurrence.get_zone(tz_name)

        slots = []
        cursor = window_start
        guard = 0
        while cursor + duration <= window_end and len(slots) < data['max_slots']:
            guard += 1
            if guard > 20000:
                break
            slot_end = cursor + duration
            available = []
            for user_id in user_ids:
                if self._is_free(busy[user_id], cursor, slot_end) and self._in_working_hours(
                    preferences.get(user_id), cursor, slot_end, zone,
                    enabled=data['respect_working_hours'],
                ):
                    available.append(user_id)
            if len(available) == len(user_ids):
                slots.append({
                    'start': cursor,
                    'end': slot_end,
                    'available_user_ids': available,
                })
            cursor += step
        return slots

    @staticmethod
    def _is_free(blocks, start, end):
        for block in blocks:
            if block['start'] < end and block['end'] > start:
                return False
        return True

    @staticmethod
    def _in_working_hours(preference, start, end, zone, enabled=True):
        if not enabled or preference is None:
            return True
        local_start = start.astimezone(zone)
        local_end = end.astimezone(zone)
        working_days = preference.working_days or [1, 2, 3, 4, 5]
        # Python weekday(): Monday == 0; the stored convention is 0=Sunday.
        weekday = (local_start.weekday() + 1) % 7
        if working_days and weekday not in working_days:
            return False
        if local_start.date() != local_end.date():
            return False
        return (
            preference.working_hours_start <= local_start.time()
            and local_end.time() <= preference.working_hours_end
        )


# ---------------------------------------------------------------------------
# 16. POST /api/calendar/conflicts/
# ---------------------------------------------------------------------------
class CalendarConflictsView(CalendarBaseView):
    """Overlap check for a proposed time. Returns a warning, never blocks --
    double-booking is legitimate."""

    @extend_schema(request=ConflictRequestSerializer,
                   description='Detect overlapping meetings for a proposed time (and '
                               'optionally a proposed recurrence rule).')
    def post(self, request):
        _require_meetings_view(request)
        payload = ConflictRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        start_at = recurrence.to_utc(data['start_at'])
        end_at = recurrence.to_utc(data['end_at'])
        duration = end_at - start_at
        tz_name = caller_timezone(request, data.get('timezone'))

        requested = data.get('user_ids') or [actor_id(request)]
        allowed, denied, scope = allowed_user_ids(
            request, requested, CRMPermissions.CRM_MEETINGS_VIEW
        )
        if scope is None:
            raise PermissionDenied('Permission not granted for this module.')

        # Candidate instants to test: either the single proposed slot, or every
        # occurrence of the proposed rule inside a bounded horizon.
        candidates = [start_at]
        rule = (data.get('recurrence_rule') or '').strip()
        if rule:
            probe = Meeting(
                start_at=start_at, end_at=end_at,
                timezone=tz_name, recurrence_rule=rule, recurrence_exdates=[],
            )
            horizon_end = start_at + timedelta(days=90)
            candidates = recurrence.expand_occurrences(probe, start_at, horizon_end) \
                or [start_at]

        horizon_start = min(candidates)
        horizon_end = max(candidates) + duration

        queryset = (
            get_queryset_for_permission(
                Meeting.objects.all(), request, CRMPermissions.CRM_MEETINGS_VIEW
            )
            .filter(meeting_window_q(horizon_start, horizon_end))
            .filter(Q(owner_user_id__in=list(allowed))
                    | Q(attendees__user_id__in=list(allowed)))
            .exclude(is_deleted=True)
            .exclude(status=MeetingStatusEnum.CANCELLED)
            .select_related('lead')
            .distinct()
        )
        if data.get('exclude_meeting_id'):
            queryset = queryset.exclude(
                Q(pk=data['exclude_meeting_id'])
                | Q(recurring_parent_id=data['exclude_meeting_id'])
            )

        names = directory.name_map(request.tenant_id)
        viewer_id = actor_id(request)
        conflicts = []
        for meeting in queryset:
            owner = str(meeting.owner_user_id)
            existing_duration = (
                recurrence.to_utc(meeting.end_at) - recurrence.to_utc(meeting.start_at)
            )
            participant_ids = {owner} | {
                str(attendee.user_id)
                for attendee in meeting.attendees.all() if attendee.user_id
            }
            is_participant = viewer_id in participant_ids
            redacted = (
                meeting.visibility == VisibilityEnum.PRIVATE
                and owner != viewer_id and not is_participant
            )
            for occurrence_start in recurrence.expand_occurrences(
                meeting, horizon_start, horizon_end
            ):
                occurrence_end = occurrence_start + existing_duration
                for candidate in candidates:
                    if occurrence_start < candidate + duration \
                            and occurrence_end > candidate:
                        conflicts.append({
                            'user_id': owner,
                            'user_name': None if redacted else names.get(owner),
                            'occurrence_start': candidate,
                            'meeting_id': meeting.id,
                            'title': 'Busy' if redacted else meeting.title,
                            'start_at': occurrence_start,
                            'end_at': occurrence_end,
                            'redacted': redacted,
                        })
                        break

        return Response({
            'has_conflicts': bool(conflicts),
            'conflicts': conflicts,
            'denied_user_ids': sorted(denied),
        })


# ---------------------------------------------------------------------------
# 17/18. GET + PATCH /api/calendar/preferences/
# ---------------------------------------------------------------------------
class CalendarPreferenceView(CalendarBaseView):
    """The caller's own calendar preferences (auto-created with defaults).

    ``timezone`` is the local source of truth for the user's IANA zone; the
    frontend seeds it from ``Intl.DateTimeFormat().resolvedOptions().timeZone``.
    """

    @extend_schema(responses=CalendarPreferenceSerializer,
                   description="Return the caller's calendar preferences, creating "
                               'defaults on first access.')
    def get(self, request):
        preference = get_or_create_preference(request)
        return Response(CalendarPreferenceSerializer(preference).data)

    @extend_schema(request=CalendarPreferenceSerializer,
                   responses=CalendarPreferenceSerializer,
                   description="Update the caller's calendar preferences.")
    def patch(self, request):
        preference = get_or_create_preference(request)
        serializer = CalendarPreferenceSerializer(preference, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


# ---------------------------------------------------------------------------
# 19. GET /api/calendar/layers/
# ---------------------------------------------------------------------------
class CalendarLayersView(CalendarBaseView):
    """Static layer metadata plus which layers the caller may actually see."""

    @extend_schema(description='Layer keys, labels, colour tokens and per-layer scope '
                               'for the current caller.')
    def get(self, request):
        layers = []
        for layer in LAYER_METADATA:
            scope = resolve_scope(request, layer['permission'])
            layers.append({**layer, 'scope': scope, 'visible': scope is not None})
        return Response({
            'layers': layers,
            'color_tokens': COLOR_TOKENS,
            'max_range_days': MAX_RANGE_DAYS,
            'max_events': MAX_EVENTS,
        })
