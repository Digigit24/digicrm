from datetime import timedelta

from django.db import transaction
from rest_framework import serializers

from common.mixins import TenantMixin
from common.permissions import CRMPermissions, check_object_permission
from . import recurrence
from .models import (
    AttendeeResponseEnum,
    AttendeeRoleEnum,
    Meeting,
    MeetingAttendee,
    MeetingReminder,
    MeetingStatusEnum,
    VisibilityEnum,
)


class MeetingAttendeeSerializer(serializers.ModelSerializer):
    """A participant on a meeting: an internal user, a CRM lead, or a raw email.

    NOTE (v1 product decision): a Lead attendee is purely a CRM record. No
    outbound WhatsApp/email is sent to a Lead attendee.
    """

    lead_name = serializers.CharField(source='lead.name', read_only=True)

    class Meta:
        model = MeetingAttendee
        fields = [
            'id', 'user_id', 'lead', 'lead_name', 'email', 'display_name',
            'role', 'response_status', 'responded_at', 'comment',
            'is_organizer', 'notify',
        ]
        read_only_fields = ['id', 'lead_name', 'responded_at', 'is_organizer']

    def validate(self, data):
        identity = data.get('user_id') or data.get('lead') or (data.get('email') or '').strip()
        if not identity:
            raise serializers.ValidationError(
                'An attendee needs at least one of user_id, lead or email.'
            )
        return data


class MeetingReminderSerializer(serializers.ModelSerializer):
    """A reminder RULE ("10 minutes before, in-app"). Delivery rows are
    materialised into notifications.Reminder by a Celery Beat task."""

    class Meta:
        model = MeetingReminder
        fields = ['id', 'minutes_before', 'method', 'for_attendees']
        read_only_fields = ['id']

    def validate_minutes_before(self, value):
        # 40320 minutes == 4 weeks; anything beyond that is almost certainly a typo.
        if value > 40320:
            raise serializers.ValidationError('minutes_before cannot exceed 40320 (4 weeks).')
        return value


class MeetingSerializer(TenantMixin):
    """
    Serialize scheduled meetings and appointments.

    Agents use this schema to create, update, and inspect meetings with leads,
    including meeting time, location, agenda, attendees, recurrence, reminders
    and follow-up notes.

    Timezone contract (see plan A.7):
      * ``start_at`` / ``end_at`` are always **UTC instants**.
      * ``timezone`` is the IANA zone the meeting was authored in. It is what the
        UI renders in and what the RRULE is expanded against, so a weekly 09:00
        Asia/Kolkata standup stays 09:00 local across DST shifts elsewhere.
      * All-day meetings are stored as local midnight -> next local midnight in
        ``timezone`` (exclusive end, matching RFC 5545 ``DTEND;VALUE=DATE``).
    """

    lead_name = serializers.CharField(
        source='lead.name',
        read_only=True,
        help_text='Display name of the linked lead, if the meeting is connected to a lead. Read-only.'
    )
    owner_user_id = serializers.UUIDField(
        required=False,
        allow_null=True,
        help_text='UUID of the meeting owner. If omitted during create, the CRM uses the authenticated JWT user_id.'
    )
    attendees = MeetingAttendeeSerializer(many=True, required=False)
    reminder_rules = MeetingReminderSerializer(many=True, required=False)
    color_key = serializers.CharField(read_only=True)
    occurrence_count = serializers.SerializerMethodField()
    attendee_summary = serializers.SerializerMethodField()
    my_response = serializers.SerializerMethodField()
    can_edit = serializers.SerializerMethodField()
    can_delete = serializers.SerializerMethodField()

    class Meta:
        model = Meeting
        fields = [
            'id', 'uid', 'lead', 'lead_name', 'title', 'location',
            'description', 'notes', 'start_at', 'end_at',
            'owner_user_id', 'created_at', 'updated_at',
            # classification / appearance
            'meeting_type', 'color', 'color_key',
            # time semantics
            'all_day', 'timezone',
            # lifecycle
            'status', 'cancelled_at', 'cancelled_by_user_id', 'cancellation_reason',
            'completed_at', 'is_deleted', 'deleted_at',
            # availability / privacy
            'transparency', 'visibility', 'conference_url',
            # recurrence
            'recurrence_rule', 'recurrence_end_at', 'recurrence_exdates',
            'recurring_parent', 'recurrence_original_start',
            # external sync
            'external_provider', 'external_event_id', 'external_calendar_id',
            'sync_status', 'last_synced_at', 'sync_error',
            # nested + computed
            'attendees', 'reminder_rules', 'occurrence_count', 'attendee_summary',
            'my_response', 'can_edit', 'can_delete',
        ]
        read_only_fields = [
            'id', 'uid', 'created_at', 'updated_at', 'recurrence_end_at',
            'cancelled_at', 'cancelled_by_user_id', 'completed_at', 'deleted_at',
            'sync_status', 'last_synced_at', 'sync_error',
            'recurring_parent', 'recurrence_original_start',
        ]
        extra_kwargs = {
            'id': {'help_text': 'Unique numeric identifier for this meeting. Read-only.'},
            'lead': {'help_text': 'Optional numeric ID of the lead this meeting is connected to.'},
            'title': {'help_text': 'Short meeting title or subject.'},
            'location': {'help_text': 'Optional physical address, online meeting link, or location label.'},
            'description': {'help_text': 'Optional meeting agenda, purpose, or description.'},
            'notes': {'help_text': 'Optional internal notes, meeting outcome, or follow-up summary. Never synced externally.'},
            'start_at': {'help_text': 'Meeting start as a UTC instant, ISO 8601.'},
            'end_at': {'help_text': 'Meeting end as a UTC instant, ISO 8601. Must be after start_at unless all_day.'},
            'timezone': {'help_text': 'IANA timezone the meeting was authored in, e.g. "Asia/Kolkata".'},
            'all_day': {'help_text': 'True for a whole-day event. start_at/end_at are snapped to local midnight boundaries.'},
            'meeting_type': {'help_text': 'MEETING | CALL | DEMO | SITE_VISIT | FOLLOW_UP | INTERNAL | OTHER.'},
            'status': {'help_text': 'SCHEDULED | CONFIRMED | TENTATIVE | CANCELLED | COMPLETED | NO_SHOW.'},
            'transparency': {'help_text': 'OPAQUE blocks the owner in free/busy; TRANSPARENT does not.'},
            'visibility': {'help_text': 'DEFAULT | PUBLIC | PRIVATE. PRIVATE is redacted to free/busy for non-participants.'},
            'recurrence_rule': {'help_text': 'RFC 5545 RRULE without the "RRULE:" prefix, e.g. "FREQ=WEEKLY;BYDAY=TU;COUNT=8".'},
            'created_at': {'help_text': 'Timestamp when this meeting was created, ISO 8601. Read-only.'},
            'updated_at': {'help_text': 'Timestamp when this meeting was last updated, ISO 8601. Read-only.'},
        }

    # -- computed ----------------------------------------------------------
    def _viewer_id(self):
        request = self.context.get('request')
        return getattr(request, 'user_id', None) if request else None

    def get_occurrence_count(self, obj):
        """Number of materialisable occurrences, or None for an open-ended series."""
        if not obj.recurrence_rule:
            return 1
        if obj.recurrence_end_at is None:
            return None
        occurrences = recurrence.expand_occurrences(
            obj,
            recurrence.to_utc(obj.start_at),
            obj.recurrence_end_at + timedelta(seconds=1),
        )
        return len(occurrences)

    def get_attendee_summary(self, obj):
        summary = {'accepted': 0, 'declined': 0, 'tentative': 0, 'needs_action': 0}
        for attendee in obj.attendees.all():
            key = {
                AttendeeResponseEnum.ACCEPTED: 'accepted',
                AttendeeResponseEnum.DECLINED: 'declined',
                AttendeeResponseEnum.TENTATIVE: 'tentative',
                AttendeeResponseEnum.NEEDS_ACTION: 'needs_action',
            }.get(attendee.response_status)
            if key:
                summary[key] += 1
        return summary

    def get_my_response(self, obj):
        viewer = self._viewer_id()
        if not viewer:
            return None
        for attendee in obj.attendees.all():
            if str(attendee.user_id) == str(viewer):
                return attendee.response_status
        return None

    def get_can_edit(self, obj):
        request = self.context.get('request')
        if not request:
            return False
        return check_object_permission(request, obj, CRMPermissions.CRM_MEETINGS_EDIT)

    def get_can_delete(self, obj):
        request = self.context.get('request')
        if not request:
            return False
        return check_object_permission(request, obj, CRMPermissions.CRM_MEETINGS_DELETE)

    # -- validation --------------------------------------------------------
    def validate_timezone(self, value):
        """A.7 rule 5: reject unknown zones with a 400 rather than storing garbage."""
        if not recurrence.is_valid_timezone(value):
            raise serializers.ValidationError(
                f'"{value}" is not a known IANA timezone name.'
            )
        return value

    def validate_recurrence_exdates(self, value):
        if value in (None, ''):
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError('recurrence_exdates must be a list of ISO-8601 instants.')
        normalised = []
        for item in value:
            parsed = recurrence.parse_instant(item)
            if parsed is None:
                raise serializers.ValidationError(f'"{item}" is not a valid ISO-8601 instant.')
            normalised.append(parsed.isoformat())
        return normalised

    def validate(self, data):
        instance = self.instance

        def resolved(field, default=None):
            if field in data:
                return data[field]
            if instance is not None:
                return getattr(instance, field)
            return default

        start_at = recurrence.to_utc(resolved('start_at'))
        end_at = recurrence.to_utc(resolved('end_at'))
        tz_name = resolved('timezone', 'UTC') or 'UTC'
        all_day = bool(resolved('all_day', False))

        if start_at is None or end_at is None:
            raise serializers.ValidationError('Both start_at and end_at are required.')

        if all_day:
            # A.7 rule 3: local midnight -> next local midnight, exclusive end.
            zone_date = recurrence.local_date(start_at, tz_name)
            end_local_date = recurrence.local_date(end_at, tz_name)
            span_days = (end_local_date - zone_date).days
            if end_at <= start_at:
                span_days = 1
            elif span_days <= 0:
                span_days = 1
            snapped_start, snapped_end = recurrence.all_day_bounds(zone_date, tz_name, days=span_days)
            data['start_at'] = snapped_start
            data['end_at'] = snapped_end
            start_at, end_at = snapped_start, snapped_end
        elif end_at <= start_at:
            # The DB check is now ``>=`` so all-day and zero-duration markers are
            # legal rows, but a *timed* meeting created through the API must still
            # end after it starts (unchanged 400 for existing clients).
            raise serializers.ValidationError('End time must be after start time')

        rule = resolved('recurrence_rule')
        if rule:
            if not recurrence.rule_is_valid(rule, start_at, tz_name):
                raise serializers.ValidationError(
                    {'recurrence_rule': 'Not a parseable RFC 5545 RRULE.'}
                )
            data['recurrence_rule'] = recurrence.normalize_rule(rule)
            data['recurrence_end_at'] = recurrence.compute_recurrence_end(
                data['recurrence_rule'], start_at, tz_name
            )
        elif 'recurrence_rule' in data:
            data['recurrence_end_at'] = None

        return data

    # -- writes ------------------------------------------------------------
    @staticmethod
    def _attendee_lookup(meeting, row):
        """Identity used to match an existing attendee row.

        Mirrors the DB partial uniques: user_id first, then email, then lead.
        """
        if row.get('user_id'):
            return {'meeting': meeting, 'user_id': row['user_id']}
        if (row.get('email') or '').strip():
            return {'meeting': meeting, 'email': row['email'].strip()}
        return {'meeting': meeting, 'lead': row.get('lead')}

    def _sync_children(self, meeting, attendees_data, reminders_data, replace=True):
        tenant_id = meeting.tenant_id
        if attendees_data is not None:
            if replace:
                meeting.attendees.exclude(is_organizer=True).delete()
            for row in attendees_data:
                lookup = self._attendee_lookup(meeting, row)
                existing = MeetingAttendee.objects.filter(**lookup).first()
                if existing is not None and existing.is_organizer:
                    # Never demote the organiser via a nested attendee payload.
                    existing.display_name = row.get('display_name') or existing.display_name
                    existing.notify = row.get('notify', existing.notify)
                    existing.save(update_fields=['display_name', 'notify', 'updated_at'])
                    continue
                defaults = {
                    'tenant_id': tenant_id,
                    'lead': row.get('lead'),
                    'email': (row.get('email') or '').strip(),
                    'user_id': row.get('user_id'),
                    'display_name': row.get('display_name', ''),
                    'role': row.get('role', AttendeeRoleEnum.REQUIRED),
                    'response_status': row.get(
                        'response_status', AttendeeResponseEnum.NEEDS_ACTION
                    ),
                    'comment': row.get('comment', ''),
                    'notify': row.get('notify', True),
                }
                if existing is not None:
                    for field, value in defaults.items():
                        setattr(existing, field, value)
                    existing.save()
                else:
                    MeetingAttendee.objects.create(**{**lookup, **defaults})

        if reminders_data is not None:
            if replace:
                meeting.reminder_rules.all().delete()
            for row in reminders_data:
                MeetingReminder.objects.update_or_create(
                    meeting=meeting,
                    minutes_before=row['minutes_before'],
                    method=row.get('method', 'IN_APP'),
                    defaults={
                        'tenant_id': tenant_id,
                        'for_attendees': row.get('for_attendees', True),
                    },
                )

    @transaction.atomic
    def create(self, validated_data):
        attendees_data = validated_data.pop('attendees', None)
        reminders_data = validated_data.pop('reminder_rules', None)
        meeting = super().create(validated_data)

        # The organiser is always an attendee, so `team` scope and RSVP work.
        MeetingAttendee.objects.update_or_create(
            meeting=meeting,
            user_id=meeting.owner_user_id,
            email='',
            defaults={
                'tenant_id': meeting.tenant_id,
                'role': AttendeeRoleEnum.ORGANIZER,
                'response_status': AttendeeResponseEnum.ACCEPTED,
                'is_organizer': True,
            },
        )
        self._sync_children(meeting, attendees_data, reminders_data, replace=False)
        return meeting

    @transaction.atomic
    def update(self, instance, validated_data):
        attendees_data = validated_data.pop('attendees', None)
        reminders_data = validated_data.pop('reminder_rules', None)
        meeting = super().update(instance, validated_data)
        self._sync_children(meeting, attendees_data, reminders_data, replace=True)
        return meeting


class MeetingListSerializer(TenantMixin):
    """
    Serialize compact meeting records for calendars and meeting lists.

    Agents use this schema when browsing scheduled meetings without full notes
    or description details.
    """

    lead_name = serializers.CharField(
        source='lead.name',
        read_only=True,
        help_text='Display name of the linked lead, if the meeting is connected to a lead. Read-only.'
    )
    color_key = serializers.CharField(read_only=True)

    class Meta:
        model = Meeting
        fields = [
            'id', 'uid', 'lead', 'lead_name', 'title', 'location',
            'start_at', 'end_at', 'created_at',
            'meeting_type', 'status', 'all_day', 'timezone', 'owner_user_id',
            'color_key', 'visibility', 'transparency', 'is_deleted',
            'recurrence_rule', 'recurring_parent',
        ]
        read_only_fields = ['id', 'uid', 'created_at', 'color_key']
        extra_kwargs = {
            'id': {'help_text': 'Unique numeric identifier for this meeting. Read-only.'},
            'lead': {'help_text': 'Optional numeric ID of the lead this meeting is connected to.'},
            'title': {'help_text': 'Short meeting title or subject.'},
            'location': {'help_text': 'Optional physical address, online meeting link, or location label.'},
            'start_at': {'help_text': 'Meeting start as a UTC instant, ISO 8601.'},
            'end_at': {'help_text': 'Meeting end as a UTC instant, ISO 8601.'},
            'created_at': {'help_text': 'Timestamp when this meeting was created, ISO 8601. Read-only.'},
        }


class MeetingOccurrenceSerializer(serializers.Serializer):
    """One expanded occurrence of a series (read-only)."""

    occurrence_start = serializers.DateTimeField()
    occurrence_end = serializers.DateTimeField()
    is_override = serializers.BooleanField()
    meeting_id = serializers.IntegerField()
    status = serializers.CharField()


# ---------------------------------------------------------------------------
# Action payloads
# ---------------------------------------------------------------------------
EDIT_SCOPE_CHOICES = ('this', 'this_and_following', 'all')


class EditScopeMixin(serializers.Serializer):
    edit_scope = serializers.ChoiceField(choices=EDIT_SCOPE_CHOICES, required=False, default='this')
    occurrence_start = serializers.DateTimeField(required=False, allow_null=True)


class CancelMeetingSerializer(EditScopeMixin):
    reason = serializers.CharField(required=False, allow_blank=True, default='')


class CompleteMeetingSerializer(EditScopeMixin):
    notes = serializers.CharField(required=False, allow_blank=True, default='')


class RsvpSerializer(serializers.Serializer):
    response_status = serializers.ChoiceField(choices=AttendeeResponseEnum.choices)
    comment = serializers.CharField(required=False, allow_blank=True, default='')
    occurrence_start = serializers.DateTimeField(required=False, allow_null=True)


class AddAttendeesSerializer(serializers.Serializer):
    attendees = MeetingAttendeeSerializer(many=True)


def redact_meeting_payload(payload, meeting):
    """Strip everything but free/busy from a PRIVATE meeting's payload."""
    keep = {'id', 'source', 'source_id', 'series_id', 'occurrence_start',
            'start_at', 'end_at', 'all_day', 'timezone', 'owner_user_id',
            'owner_name', 'transparency', 'visibility', 'status',
            'is_recurring', 'is_override'}
    redacted = {key: value for key, value in payload.items() if key in keep}
    redacted.update({
        'title': 'Busy',
        'redacted': True,
        'color_key': 'busy',
        'can_edit': False,
        'can_delete': False,
        'attendees': [],
        'attendee_summary': None,
        'lead': None,
        'description': None,
        'location': None,
        'conference_url': None,
        'meeting_type': None,
        'has_reminders': False,
        'my_response': None,
    })
    return redacted


__all__ = [
    'MeetingSerializer', 'MeetingListSerializer', 'MeetingAttendeeSerializer',
    'MeetingReminderSerializer', 'MeetingOccurrenceSerializer',
    'CancelMeetingSerializer', 'CompleteMeetingSerializer', 'RsvpSerializer',
    'AddAttendeesSerializer', 'redact_meeting_payload',
    'MeetingStatusEnum', 'VisibilityEnum',
]
