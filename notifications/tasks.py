import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

from .models import Notification, Reminder, ReminderStatus
from .realtime import publish_notification

logger = logging.getLogger(__name__)


def _deliver_claimed_reminder(reminder_id, now):
    notification = None
    with transaction.atomic():
        reminder = (
            Reminder.objects.select_for_update()
            .select_related('lead')
            .filter(pk=reminder_id)
            .first()
        )
        if not reminder or reminder.status != ReminderStatus.PROCESSING:
            return None

        grace_hours = getattr(settings, 'REMINDER_DELIVERY_GRACE_HOURS', 24)
        if reminder.remind_at < now - timedelta(hours=grace_hours):
            reminder.status = ReminderStatus.MISSED
            reminder.locked_at = None
            reminder.last_error = 'Reminder exceeded the delivery grace period.'
            reminder.save(update_fields=['status', 'locked_at', 'last_error', 'updated_at'])
            return None

        lead = reminder.lead
        notification, _ = Notification.objects.get_or_create(
            dedupe_key=f'follow-up-reminder:{reminder.id}',
            defaults={
                'tenant_id': reminder.tenant_id,
                'recipient_user_id': reminder.recipient_user_id,
                'reminder': reminder,
                'lead': lead,
                'notification_type': 'FOLLOW_UP_REMINDER',
                'title': f'Follow up with {lead.name}',
                'body': 'Your scheduled lead follow-up is due now.',
                'lead_name_snapshot': lead.name[:255],
                'action_url': f'/crm/leads/{lead.id}',
                'payload': {
                    'lead_id': lead.id,
                    'follow_up_at': reminder.follow_up_at.isoformat(),
                    'remind_at': reminder.remind_at.isoformat(),
                },
            },
        )
        reminder.status = ReminderStatus.DELIVERED
        reminder.delivered_at = now
        reminder.locked_at = None
        reminder.last_error = ''
        reminder.save(update_fields=[
            'status', 'delivered_at', 'locked_at', 'last_error', 'updated_at',
        ])
        transaction.on_commit(lambda: publish_notification(notification))
    return notification.id if notification else None


@shared_task(name='notifications.tasks.dispatch_due_reminders')
def dispatch_due_reminders(batch_size=200):
    """Claim and deliver due reminders exactly once without per-reminder cron jobs."""
    now = timezone.now()
    stale_before = now - timedelta(minutes=5)

    with transaction.atomic():
        # A worker that died after claiming a row must not strand it forever.
        Reminder.objects.filter(
            status=ReminderStatus.PROCESSING,
            locked_at__lt=stale_before,
        ).update(status=ReminderStatus.PENDING, locked_at=None)

        due = list(
            Reminder.objects.select_for_update(skip_locked=True)
            .filter(status=ReminderStatus.PENDING, remind_at__lte=now)
            .order_by('remind_at')[:batch_size]
        )
        reminder_ids = [item.id for item in due]
        if reminder_ids:
            Reminder.objects.filter(id__in=reminder_ids).update(
                status=ReminderStatus.PROCESSING,
                locked_at=now,
                attempt_count=models.F('attempt_count') + 1,
            )

    delivered = 0
    for reminder_id in reminder_ids:
        try:
            if _deliver_claimed_reminder(reminder_id, now):
                delivered += 1
        except Exception as exc:
            logger.exception('Failed to deliver reminder %s', reminder_id)
            reminder = Reminder.objects.filter(
                pk=reminder_id,
                status=ReminderStatus.PROCESSING,
            ).first()
            if not reminder:
                continue
            max_attempts = getattr(settings, 'REMINDER_MAX_ATTEMPTS', 5)
            reminder.status = (
                ReminderStatus.MISSED
                if reminder.attempt_count >= max_attempts
                else ReminderStatus.PENDING
            )
            reminder.locked_at = None
            reminder.last_error = str(exc)[:2000]
            reminder.save(update_fields=[
                'status', 'locked_at', 'last_error', 'updated_at',
            ])
    return {'claimed': len(reminder_ids), 'delivered': delivered}
