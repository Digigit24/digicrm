import uuid

from django.db import models

from crm.models import Lead


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
class MeetingTypeEnum(models.TextChoices):
    MEETING = 'MEETING', 'Meeting'
    CALL = 'CALL', 'Call'
    DEMO = 'DEMO', 'Demo'
    SITE_VISIT = 'SITE_VISIT', 'Site Visit'
    FOLLOW_UP = 'FOLLOW_UP', 'Follow Up'
    INTERNAL = 'INTERNAL', 'Internal'
    OTHER = 'OTHER', 'Other'


class MeetingStatusEnum(models.TextChoices):
    SCHEDULED = 'SCHEDULED', 'Scheduled'
    CONFIRMED = 'CONFIRMED', 'Confirmed'
    TENTATIVE = 'TENTATIVE', 'Tentative'
    CANCELLED = 'CANCELLED', 'Cancelled'
    COMPLETED = 'COMPLETED', 'Completed'
    NO_SHOW = 'NO_SHOW', 'No Show'


class TransparencyEnum(models.TextChoices):
    OPAQUE = 'OPAQUE', 'Busy'
    TRANSPARENT = 'TRANSPARENT', 'Free'


class VisibilityEnum(models.TextChoices):
    DEFAULT = 'DEFAULT', 'Default'
    PUBLIC = 'PUBLIC', 'Public'
    PRIVATE = 'PRIVATE', 'Private'


class SyncStatusEnum(models.TextChoices):
    NOT_SYNCED = 'NOT_SYNCED', 'Not synced'
    PENDING = 'PENDING', 'Pending'
    SYNCED = 'SYNCED', 'Synced'
    FAILED = 'FAILED', 'Failed'
    SKIPPED = 'SKIPPED', 'Skipped'


class AttendeeRoleEnum(models.TextChoices):
    ORGANIZER = 'ORGANIZER', 'Organizer'
    REQUIRED = 'REQUIRED', 'Required'
    OPTIONAL = 'OPTIONAL', 'Optional'


class AttendeeResponseEnum(models.TextChoices):
    NEEDS_ACTION = 'NEEDS_ACTION', 'No response'
    ACCEPTED = 'ACCEPTED', 'Accepted'
    DECLINED = 'DECLINED', 'Declined'
    TENTATIVE = 'TENTATIVE', 'Maybe'


class ReminderMethodEnum(models.TextChoices):
    IN_APP = 'IN_APP', 'In-app'
    EMAIL = 'EMAIL', 'Email'
    WHATSAPP = 'WHATSAPP', 'WhatsApp'


# Colour token key derived from meeting_type when Meeting.color is blank.
MEETING_TYPE_COLOR_KEYS = {
    MeetingTypeEnum.MEETING: 'meeting',
    MeetingTypeEnum.CALL: 'call',
    MeetingTypeEnum.DEMO: 'demo',
    MeetingTypeEnum.SITE_VISIT: 'site_visit',
    MeetingTypeEnum.FOLLOW_UP: 'follow_up',
    MeetingTypeEnum.INTERNAL: 'internal',
    MeetingTypeEnum.OTHER: 'meeting',
}


class Meeting(models.Model):
    """Meeting model for scheduling and tracking meetings.

    A row is either a standalone meeting, a recurring *master* (``recurrence_rule``
    is set) or an *override* of one occurrence of a master (``recurring_parent`` +
    ``recurrence_original_start`` are set).
    """

    # ---- existing ----------------------------------------------------------
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(db_index=True)
    lead = models.ForeignKey(
        Lead,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='meetings',
        db_column='lead_id'
    )
    title = models.TextField()
    location = models.TextField(null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    owner_user_id = models.UUIDField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # ---- stable identity ----------------------------------------------------
    uid = models.UUIDField(
        default=uuid.uuid4, editable=False, db_index=True,
        help_text='Stable iCalendar-style UID. Survives external round-trips; used as the '
                  'RFC 5545 UID when pushed to Google/CalDAV and as the React key prefix.'
    )

    # ---- classification / appearance ---------------------------------------
    meeting_type = models.CharField(
        max_length=20, choices=MeetingTypeEnum.choices,
        default=MeetingTypeEnum.MEETING, db_index=True
    )
    color = models.CharField(
        max_length=20, blank=True, default='',
        help_text='Optional colour-token override (e.g. "violet"). '
                  'Empty means derive the colour from meeting_type.'
    )

    # ---- time semantics -----------------------------------------------------
    all_day = models.BooleanField(default=False)
    timezone = models.CharField(
        max_length=64, default='UTC',
        help_text='IANA timezone the meeting was authored in (e.g. "Asia/Kolkata"). '
                  'start_at/end_at remain UTC instants; this is what the UI renders in and '
                  'what an RRULE is expanded against so DST shifts stay correct.'
    )

    # ---- lifecycle ----------------------------------------------------------
    status = models.CharField(
        max_length=20, choices=MeetingStatusEnum.choices,
        default=MeetingStatusEnum.SCHEDULED, db_index=True
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by_user_id = models.UUIDField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True, default='')
    completed_at = models.DateTimeField(null=True, blank=True)
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    # ---- availability / privacy --------------------------------------------
    transparency = models.CharField(
        max_length=12, choices=TransparencyEnum.choices,
        default=TransparencyEnum.OPAQUE,
        help_text='OPAQUE blocks the owner in free/busy; TRANSPARENT does not.'
    )
    visibility = models.CharField(
        max_length=10, choices=VisibilityEnum.choices,
        default=VisibilityEnum.DEFAULT,
        help_text='PRIVATE meetings are redacted to "Busy" for anyone who is not the '
                  'owner or an attendee.'
    )
    conference_url = models.TextField(null=True, blank=True)

    # ---- recurrence ---------------------------------------------------------
    recurrence_rule = models.TextField(
        null=True, blank=True,
        help_text='RFC 5545 RRULE without the "RRULE:" prefix, e.g. '
                  '"FREQ=WEEKLY;BYDAY=MO,WE;UNTIL=20261231T183000Z". '
                  'Null means a single, non-recurring meeting.'
    )
    recurrence_end_at = models.DateTimeField(
        null=True, blank=True, db_index=True,
        help_text='Denormalised UTC instant of the last possible occurrence (resolved from '
                  'UNTIL or COUNT at save time). Null = open-ended.'
    )
    recurrence_exdates = models.JSONField(
        default=list, blank=True,
        help_text='List of ISO-8601 UTC occurrence start instants removed from the series '
                  '(RFC 5545 EXDATE).'
    )
    recurring_parent = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.CASCADE, related_name='overrides'
    )
    recurrence_original_start = models.DateTimeField(
        null=True, blank=True,
        help_text='RFC 5545 RECURRENCE-ID. On an override row, the original UTC start of the '
                  'occurrence in the parent series that this row replaces.'
    )

    # ---- external calendar sync seam ---------------------------------------
    external_provider = models.CharField(
        max_length=32, null=True, blank=True, db_index=True,
        help_text='GOOGLE | CALDAV | MICROSOFT | COMPOSIO_GOOGLE ... Null = CRM-only meeting.'
    )
    external_event_id = models.CharField(max_length=512, null=True, blank=True, db_index=True)
    external_calendar_id = models.CharField(max_length=255, null=True, blank=True)
    external_sync = models.JSONField(
        null=True, blank=True,
        help_text='Provider-specific sync metadata that must NOT require a migration to '
                  'extend: {"account_id":..., "connected_account_id":..., "etag":..., '
                  '"sync_token":..., "ical_uid":..., "html_link":..., "sequence":..., '
                  '"raw_updated":...}'
    )
    sync_status = models.CharField(
        max_length=12, choices=SyncStatusEnum.choices,
        default=SyncStatusEnum.NOT_SYNCED, db_index=True
    )
    last_synced_at = models.DateTimeField(null=True, blank=True)
    sync_error = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'meetings'
        indexes = [
            models.Index(fields=['tenant_id'], name='idx_meetings_tenant_id'),
            models.Index(fields=['lead'], name='idx_meetings_lead_id'),
            models.Index(fields=['start_at'], name='idx_meetings_start_at'),
            models.Index(fields=['owner_user_id'], name='idx_meetings_owner_user_id'),
            models.Index(fields=['tenant_id', 'owner_user_id', 'start_at'],
                         name='idx_meetings_tenant_owner_st'),
            models.Index(fields=['tenant_id', 'start_at', 'end_at'],
                         name='idx_meetings_tenant_window'),
            models.Index(fields=['recurring_parent', 'recurrence_original_start'],
                         name='idx_meetings_override'),
        ]
        constraints = [
            # ``>=`` (was ``>``) so all-day events and zero-duration markers are legal.
            # The serializer still enforces end > start for timed, non-all-day meetings.
            models.CheckConstraint(
                check=models.Q(end_at__gte=models.F('start_at')),
                name='meetings_end_after_start'
            ),
            # NOTE: ``unique_meeting_per_tenant`` on (tenant_id, title, start_at) was DROPPED
            # in migration 0002 — it made two "Standup at 09:00" rows impossible.
            models.UniqueConstraint(
                fields=['recurring_parent', 'recurrence_original_start'],
                condition=models.Q(recurring_parent__isnull=False),
                name='uniq_meeting_override_per_occurrence'
            ),
            models.UniqueConstraint(
                fields=['tenant_id', 'external_provider', 'external_event_id'],
                condition=models.Q(external_event_id__isnull=False),
                name='uniq_meeting_external_event'
            ),
        ]

    def __str__(self):
        return f"{self.title} - {self.start_at}"

    # -- helpers -------------------------------------------------------------
    @property
    def is_recurring_master(self):
        return bool(self.recurrence_rule) and self.recurring_parent_id is None

    @property
    def is_override(self):
        return self.recurring_parent_id is not None

    @property
    def color_key(self):
        if self.status == MeetingStatusEnum.CANCELLED:
            return 'cancelled'
        if self.color:
            return self.color
        return MEETING_TYPE_COLOR_KEYS.get(self.meeting_type, 'meeting')


class MeetingAttendee(models.Model):
    """A participant on a meeting: an internal user, a CRM lead, or a raw email."""

    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(db_index=True)
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name='attendees')

    user_id = models.UUIDField(
        null=True, blank=True, db_index=True,
        help_text='SuperAdmin user UUID for an internal attendee.'
    )
    lead = models.ForeignKey(
        Lead, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='meeting_invites'
    )
    email = models.CharField(max_length=255, blank=True, default='')
    display_name = models.CharField(max_length=255, blank=True, default='')

    role = models.CharField(
        max_length=12, choices=AttendeeRoleEnum.choices, default=AttendeeRoleEnum.REQUIRED
    )
    response_status = models.CharField(
        max_length=14, choices=AttendeeResponseEnum.choices,
        default=AttendeeResponseEnum.NEEDS_ACTION, db_index=True
    )
    responded_at = models.DateTimeField(null=True, blank=True)
    comment = models.TextField(blank=True, default='')
    is_organizer = models.BooleanField(default=False)
    notify = models.BooleanField(default=True, help_text='Generate reminder rows for this attendee.')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'meeting_attendees'
        indexes = [
            models.Index(fields=['tenant_id', 'user_id'], name='idx_mtg_att_tenant_user'),
            models.Index(fields=['meeting', 'response_status'], name='idx_mtg_att_response'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['meeting', 'user_id'],
                condition=models.Q(user_id__isnull=False),
                name='uniq_attendee_user_per_meeting'
            ),
            models.UniqueConstraint(
                fields=['meeting', 'email'],
                condition=~models.Q(email=''),
                name='uniq_attendee_email_per_meeting'
            ),
            models.CheckConstraint(
                check=(models.Q(user_id__isnull=False)
                       | models.Q(lead__isnull=False)
                       | ~models.Q(email='')),
                name='meeting_attendee_has_identity'
            ),
        ]

    def __str__(self):
        return self.display_name or self.email or str(self.user_id) or f'attendee {self.pk}'


class MeetingReminder(models.Model):
    """A reminder RULE attached to a meeting (or series).

    Materialised into ``notifications.Reminder`` rows for a rolling horizon by
    ``meetings.tasks.materialize_meeting_reminders``.
    """

    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(db_index=True)
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name='reminder_rules')
    minutes_before = models.PositiveIntegerField()
    method = models.CharField(
        max_length=12, choices=ReminderMethodEnum.choices, default=ReminderMethodEnum.IN_APP
    )
    for_attendees = models.BooleanField(
        default=True, help_text='True = every attendee with notify=True; False = organiser only.'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'meeting_reminders'
        constraints = [
            models.UniqueConstraint(
                fields=['meeting', 'minutes_before', 'method'],
                name='uniq_meeting_reminder_rule'
            ),
        ]

    def __str__(self):
        return f'{self.minutes_before}m before ({self.method})'
