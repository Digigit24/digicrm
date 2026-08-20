"""Celery tasks for the tasks app.

``Task.reminder_minutes_before`` is the *rule* ("remind me 30 minutes before it
is due").  ``notifications.Reminder`` remains the *delivery queue*, so
``notifications.tasks.dispatch_due_reminders`` (Beat, every 30s) is reused
verbatim -- claiming, locking, the grace period, retries and the realtime publish
are all untouched.  This module only materialises rows into that queue, exactly
the way ``meetings.tasks.materialize_meeting_reminders`` does.
"""
import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone as dj_timezone

from notifications.models import Reminder, ReminderStatus, ReminderSubjectType

from .models import CLOSED_TASK_STATUSES, Task

logger = logging.getLogger(__name__)

DEFAULT_HORIZON_DAYS = 30


def _recipients_for(task):
    """Who hears about this task: the assignee, falling back to the owner."""
    recipients = []
    seen = set()
    for candidate in (task.assignee_user_id, task.owner_user_id):
        if not candidate:
            continue
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        recipients.append(candidate)
    return recipients


def materialize_for_task(task, now=None, grace=None):
    """Create the delivery row(s) for one task. Idempotent."""
    now = now or dj_timezone.now()
    if grace is None:
        grace = timedelta(hours=getattr(settings, 'REMINDER_DELIVERY_GRACE_HOURS', 24))

    if task.status in CLOSED_TASK_STATUSES:
        return 0
    if task.reminder_minutes_before is None or not task.due_date:
        return 0
    if task.snoozed_until and task.snoozed_until > now:
        return 0

    remind_at = task.due_date - timedelta(minutes=task.reminder_minutes_before)
    if remind_at < now - grace:
        return 0

    created = 0
    for recipient in _recipients_for(task):
        exists = Reminder.objects.filter(
            task=task,
            recipient_user_id=recipient,
            offset_minutes=task.reminder_minutes_before,
        ).exists()
        if exists:
            continue
        try:
            with transaction.atomic():
                Reminder.objects.create(
                    tenant_id=task.tenant_id,
                    lead_id=task.lead_id,
                    task=task,
                    subject_type=ReminderSubjectType.TASK,
                    method='IN_APP',
                    recipient_user_id=recipient,
                    created_by_user_id=task.owner_user_id,
                    follow_up_at=task.due_date,
                    remind_at=remind_at,
                    offset_minutes=task.reminder_minutes_before,
                    status=ReminderStatus.PENDING,
                )
            created += 1
        except IntegrityError:
            # uniq_active_task_reminder -- another worker got there first.
            continue
    return created


def cancel_reminders_for_task(task):
    """Drop the still-pending delivery rows for a task (completed / rescheduled)."""
    return Reminder.objects.filter(
        task=task,
        status__in=[ReminderStatus.PENDING, ReminderStatus.PROCESSING],
    ).update(
        status=ReminderStatus.CANCELLED,
        cancelled_at=dj_timezone.now(),
        locked_at=None,
    )


@shared_task(name='tasks.tasks.materialize_task_reminders')
def materialize_task_reminders(horizon_days=DEFAULT_HORIZON_DAYS):
    """Turn every open task's reminder rule into ``notifications.Reminder`` rows."""
    now = dj_timezone.now()
    horizon = now + timedelta(days=horizon_days)
    grace = timedelta(hours=getattr(settings, 'REMINDER_DELIVERY_GRACE_HOURS', 24))

    queryset = (
        Task.objects
        .filter(reminder_minutes_before__isnull=False, due_date__isnull=False)
        .filter(due_date__lte=horizon, due_date__gte=now - grace)
        .exclude(status__in=CLOSED_TASK_STATUSES)
    )

    scanned = 0
    created = 0
    for task in queryset.iterator(chunk_size=500):
        scanned += 1
        try:
            created += materialize_for_task(task, now=now, grace=grace)
        except Exception:
            logger.exception('Failed to materialize reminders for task %s', task.id)
    return {'scanned': scanned, 'created': created}
