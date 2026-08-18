"""Celery tasks for meetings.

``MeetingReminder`` is the *rule* ("10 minutes before, in-app").
``notifications.Reminder`` remains the *delivery queue*, so
``notifications.tasks.dispatch_due_reminders`` (Beat, every 30s) is reused
verbatim -- claiming, locking, grace period, retry and the Pusher publish are all
untouched.
"""
import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone as dj_timezone

from celery import Task
from notifications.models import Reminder, ReminderStatus, ReminderSubjectType

from . import recurrence
from .models import Meeting, MeetingStatusEnum

logger = logging.getLogger(__name__)

DEFAULT_HORIZON_DAYS = 60


class CallbackTask(Task):
    """Base task with failure logging (house style, mirrors integrations.tasks)."""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        logger.error(
            'Task %s failed: %s' % (self.name, exc),
            extra={'task_id': task_id, 'args': args, 'kwargs': kwargs},
            exc_info=True,
        )


def _recipients_for_rule(meeting, rule):
    """UUIDs that should receive ``rule``'s reminder for ``meeting``."""
    if not rule.for_attendees:
        return [meeting.owner_user_id] if meeting.owner_user_id else []

    recipients = []
    seen = set()
    for attendee in meeting.attendees.all():
        if not attendee.notify or not attendee.user_id:
            continue
        key = str(attendee.user_id)
        if key in seen:
            continue
        seen.add(key)
        recipients.append(attendee.user_id)

    if meeting.owner_user_id and str(meeting.owner_user_id) not in seen:
        recipients.append(meeting.owner_user_id)
    return recipients


def _ensure_reminder(meeting, occurrence_start, rule, recipient_user_id, now, grace):
    remind_at = occurrence_start - timedelta(minutes=rule.minutes_before)
    if remind_at < now - grace:
        return False

    existing = Reminder.objects.filter(
        meeting=meeting,
        occurrence_start_at=occurrence_start,
        recipient_user_id=recipient_user_id,
        offset_minutes=rule.minutes_before,
    ).exists()
    if existing:
        return False

    try:
        with transaction.atomic():
            Reminder.objects.create(
                tenant_id=meeting.tenant_id,
                lead=meeting.lead,
                meeting=meeting,
                occurrence_start_at=occurrence_start,
                subject_type=ReminderSubjectType.MEETING,
                method=rule.method,
                recipient_user_id=recipient_user_id,
                created_by_user_id=meeting.owner_user_id,
                follow_up_at=occurrence_start,
                remind_at=remind_at,
                offset_minutes=rule.minutes_before,
                status=ReminderStatus.PENDING,
            )
    except IntegrityError:
        # uniq_active_meeting_reminder -- another worker got there first.
        return False
    return True


def materialize_for_meeting(meeting, window_start, window_end, now=None, grace=None):
    """Create the delivery rows for one meeting inside a window. Idempotent."""
    now = now or dj_timezone.now()
    if grace is None:
        grace = timedelta(hours=getattr(settings, 'REMINDER_DELIVERY_GRACE_HOURS', 24))

    if meeting.is_deleted or meeting.status == MeetingStatusEnum.CANCELLED:
        return 0

    rules = list(meeting.reminder_rules.all())
    if not rules:
        return 0

    occurrences = recurrence.expand_occurrences(meeting, window_start, window_end)
    if meeting.recurrence_rule:
        overridden = {
            recurrence.to_utc(value)
            for value in meeting.overrides.values_list('recurrence_original_start', flat=True)
        }
        occurrences = [o for o in occurrences if o not in overridden]

    created = 0
    for occurrence_start in occurrences:
        for rule in rules:
            for recipient in _recipients_for_rule(meeting, rule):
                if _ensure_reminder(meeting, occurrence_start, rule, recipient, now, grace):
                    created += 1
    return created


@shared_task(base=CallbackTask, bind=True, max_retries=3,
             name='meetings.tasks.materialize_meeting_reminders')
def materialize_meeting_reminders(self, horizon_days=DEFAULT_HORIZON_DAYS, batch_size=500):
    """Expand meetings over a rolling horizon and create notifications.Reminder rows.

    Idempotent: existing rows for the same (meeting, occurrence, recipient,
    offset) are skipped, and ``uniq_active_meeting_reminder`` is the backstop.
    """
    now = dj_timezone.now()
    window_end = now + timedelta(days=horizon_days)
    grace = timedelta(hours=getattr(settings, 'REMINDER_DELIVERY_GRACE_HOURS', 24))

    queryset = (
        Meeting.objects
        .filter(reminder_rules__isnull=False)
        .exclude(is_deleted=True)
        .exclude(status=MeetingStatusEnum.CANCELLED)
        .filter(start_at__lt=window_end)
        .distinct()
        .prefetch_related('attendees', 'reminder_rules', 'overrides')
        .select_related('lead')
    )

    created = 0
    scanned = 0
    for meeting in queryset.iterator(chunk_size=batch_size):
        scanned += 1
        try:
            created += materialize_for_meeting(meeting, now, window_end, now=now, grace=grace)
        except Exception:
            logger.exception('Failed to materialize reminders for meeting %s', meeting.pk)
    return {'scanned': scanned, 'created': created, 'horizon_days': horizon_days}
