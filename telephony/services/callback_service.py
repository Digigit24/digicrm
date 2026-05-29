"""
Callback service — converts missed TeleCMI inbound calls into CRM Tasks.

A Task is only created when the missed call can be matched to a CRM Lead,
because Task requires a lead FK. The cmiuid is embedded in the description
so the task is not duplicated on re-sync.
"""
import logging
from django.utils import timezone

logger = logging.getLogger(__name__)

CALLBACK_TASK_TITLE_PREFIX = 'Call back'


def create_callback_task_if_needed(tenant_id, call_log, owner_user_id=None) -> bool:
    """
    If call_log is a missed inbound call linked to a Lead, create a Task
    titled "Call back <number>" unless one already exists for this cmiuid.

    owner_user_id: UUID to set as task owner (e.g. the agent's user_id).
                   Falls back to a zero UUID if not provided.

    Returns True if a new task was created.
    """
    from telephony.models import CallDirectionEnum, CallTypeEnum

    if call_log.direction != CallDirectionEnum.INBOUND:
        return False
    if call_log.call_type != CallTypeEnum.MISSED:
        return False
    if not call_log.lead_id:
        # Cannot create a Task without a Lead FK
        logger.debug(
            'Skipping callback task for %s: no matching lead', call_log.cmiuid
        )
        return False

    if _task_exists_for_call(tenant_id, call_log.cmiuid):
        return False

    caller = call_log.from_number
    title = f'{CALLBACK_TASK_TITLE_PREFIX}: {caller}'
    if call_log.caller_name and call_log.caller_name.lower() not in ('unknown', ''):
        title = f'{CALLBACK_TASK_TITLE_PREFIX}: {call_log.caller_name} ({caller})'

    description = (
        f'Missed inbound call from {caller} '
        f'at {call_log.call_time.strftime("%Y-%m-%d %H:%M UTC")}.\n'
        f'TeleCMI call ID: {call_log.cmiuid}'
    )

    import uuid
    effective_owner = owner_user_id or uuid.UUID(int=0)
    due = timezone.now() + timezone.timedelta(hours=2)

    try:
        from tasks.models import Task
        task = Task.objects.create(
            tenant_id=tenant_id,
            title=title,
            description=description,
            due_date=due,
            lead_id=call_log.lead_id,
            owner_user_id=effective_owner,
        )
        logger.info(
            'Created callback task %s for missed call %s from %s',
            task.id, call_log.cmiuid, caller,
        )
        return True
    except Exception as exc:
        logger.error('Failed to create callback task for %s: %s', call_log.cmiuid, exc)
        return False


def _task_exists_for_call(tenant_id, cmiuid) -> bool:
    """
    Check if a Task with the TeleCMI cmiuid embedded in description already exists.
    Avoids duplicates on webhook re-delivery or manual re-sync.
    """
    try:
        from tasks.models import Task
        return Task.objects.filter(
            tenant_id=tenant_id,
            description__contains=cmiuid,
        ).exists()
    except Exception:
        return False
