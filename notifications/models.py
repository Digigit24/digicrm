from django.db import models
from django.db.models import Q


class ReminderStatus(models.TextChoices):
    PENDING = 'PENDING', 'Pending'
    PROCESSING = 'PROCESSING', 'Processing'
    DELIVERED = 'DELIVERED', 'Delivered'
    CANCELLED = 'CANCELLED', 'Cancelled'
    MISSED = 'MISSED', 'Missed'


class ReminderSubjectType(models.TextChoices):
    LEAD_FOLLOW_UP = 'LEAD_FOLLOW_UP', 'Lead follow-up'
    MEETING = 'MEETING', 'Meeting'


class Reminder(models.Model):
    """A durable, tenant-scoped reminder for a lead follow-up or a meeting occurrence."""

    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(db_index=True)
    lead = models.ForeignKey(
        'crm.Lead',
        on_delete=models.CASCADE,
        related_name='reminders',
        null=True,
        blank=True,
    )
    meeting = models.ForeignKey(
        'meetings.Meeting',
        on_delete=models.CASCADE,
        related_name='delivery_reminders',
        null=True,
        blank=True,
    )
    occurrence_start_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text='UTC start of the specific meeting occurrence this reminder belongs to. '
                  'Null for lead follow-ups.',
    )
    subject_type = models.CharField(
        max_length=32,
        default=ReminderSubjectType.LEAD_FOLLOW_UP,
        db_index=True,
    )
    method = models.CharField(max_length=12, default='IN_APP')
    recipient_user_id = models.UUIDField(db_index=True)
    created_by_user_id = models.UUIDField(db_index=True)
    follow_up_at = models.DateTimeField()
    remind_at = models.DateTimeField(db_index=True)
    offset_minutes = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=20,
        choices=ReminderStatus.choices,
        default=ReminderStatus.PENDING,
        db_index=True,
    )
    attempt_count = models.PositiveIntegerField(default=0)
    locked_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'crm_reminders'
        indexes = [
            models.Index(
                fields=['tenant_id', 'status', 'remind_at'],
                name='idx_reminder_due',
            ),
            models.Index(
                fields=['tenant_id', 'recipient_user_id', 'status'],
                name='idx_reminder_recipient',
            ),
        ]
        constraints = [
            # ``lead__isnull=False`` added so a meeting-only reminder (lead is NULL) is not
            # collapsed into a single row per (tenant, NULL, recipient) and so a lead can
            # carry several meeting reminders without tripping the follow-up constraint.
            models.UniqueConstraint(
                fields=['tenant_id', 'lead', 'recipient_user_id'],
                condition=Q(
                    lead__isnull=False,
                    meeting__isnull=True,
                    status__in=[ReminderStatus.PENDING, ReminderStatus.PROCESSING],
                ),
                name='uniq_active_lead_reminder',
            ),
            models.UniqueConstraint(
                fields=['tenant_id', 'meeting', 'occurrence_start_at',
                        'recipient_user_id', 'offset_minutes'],
                condition=Q(
                    meeting__isnull=False,
                    status__in=[ReminderStatus.PENDING, ReminderStatus.PROCESSING],
                ),
                name='uniq_active_meeting_reminder',
            ),
        ]


class Notification(models.Model):
    """A persistent in-app notification. Realtime transport is best effort only."""

    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField(db_index=True)
    recipient_user_id = models.UUIDField(db_index=True)
    reminder = models.OneToOneField(
        Reminder,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notification',
    )
    lead = models.ForeignKey(
        'crm.Lead',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notifications',
    )
    notification_type = models.CharField(max_length=40, default='FOLLOW_UP_REMINDER')
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True, default='')
    lead_name_snapshot = models.CharField(max_length=255, blank=True, default='')
    action_url = models.CharField(max_length=500, blank=True, default='')
    payload = models.JSONField(default=dict, blank=True)
    dedupe_key = models.CharField(max_length=160, unique=True)
    seen_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'crm_notifications'
        ordering = ['-created_at']
        indexes = [
            models.Index(
                fields=['tenant_id', 'recipient_user_id', 'read_at', '-created_at'],
                name='idx_notification_inbox',
            ),
        ]

