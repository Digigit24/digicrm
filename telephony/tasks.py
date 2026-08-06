"""
Celery tasks for the telephony app.

Background tasks for:
- Periodic CDR sync safety net (catches missed/misconfigured webhooks)
"""
import logging
from datetime import datetime, timedelta

from celery import shared_task, Task
from django.utils import timezone

from telephony.models import TeleCMIAgent, CallLog, RecordingStorageStatusEnum
from telephony.services.call_log_service import sync_cdr_for_agent

logger = logging.getLogger(__name__)


class TelephonyTask(Task):
    """Base task with consistent failure logging."""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        logger.error(
            'Task %s failed: %s',
            self.name,
            exc,
            extra={'task_id': task_id, 'args': args, 'kwargs': kwargs},
            exc_info=True,
        )


@shared_task(base=TelephonyTask)
def sync_all_telecmi_cdrs(hours_back: int = 1):
    """
    Periodic safety-net task: pull recent CDR for every active TeleCMI agent.

    Runs every 5 minutes via Celery Beat. This does NOT replace the CDR webhook;
    it backfills calls that arrived while the webhook was unavailable or
    misconfigured. Uses a short lookback window (1 hour) to avoid hammering
    TeleCMI's API.
    """
    agents = TeleCMIAgent.objects.filter(is_active=True).values(
        'tenant_id', 'user_id'
    )
    total = agents.count()
    logger.info('Starting periodic CDR sync for %s active TeleCMI agent(s)', total)

    processed = 0
    for agent in agents:
        tenant_id = agent['tenant_id']
        user_id = agent['user_id']
        try:
            result = sync_cdr_for_agent(tenant_id, user_id, hours_back=hours_back)
            processed += 1
            logger.info(
                'Periodic CDR sync for tenant=%s user=%s: %s',
                tenant_id, user_id, result,
            )
        except Exception as exc:
            logger.error(
                'Periodic CDR sync failed for tenant=%s user=%s: %s',
                tenant_id, user_id, exc,
                exc_info=True,
            )

    logger.info('Finished periodic CDR sync (%s/%s agents processed)', processed, total)


@shared_task(bind=True, base=TelephonyTask, max_retries=3)
def reconcile_outbound_call(
    self,
    tenant_id,
    user_id,
    to_number,
    initiated_at,
    lead_id=None,
):
    """Finite post-call reconciliation: one minute after dial, then bounded retries."""
    started = datetime.fromisoformat(initiated_at)
    if timezone.is_naive(started):
        started = timezone.make_aware(started)

    result = sync_cdr_for_agent(tenant_id, user_id, hours_back=1, queue_archives=True)
    digits = ''.join(ch for ch in str(to_number) if ch.isdigit())
    suffix = digits[-10:] if len(digits) >= 10 else digits
    if not suffix:
        logger.warning(
            'Post-call reconciliation received an invalid destination for tenant=%s user=%s',
            tenant_id, user_id,
        )
        return {'found': False, 'sync': result, 'error': 'Invalid destination number'}
    found = CallLog.objects.filter(
        tenant_id=tenant_id,
        agent_user_id=user_id,
        direction='outbound',
        to_number__endswith=suffix,
        call_time__gte=started - timedelta(minutes=2),
    ).order_by('-call_time').first()

    if found:
        if lead_id and not found.lead_id:
            found.lead_id = lead_id
            found.save(update_fields=['lead_id', 'updated_at'])
        return {'found': True, 'call_log_id': found.id, 'sync': result}

    if self.request.retries < self.max_retries:
        delays = (60, 120, 300)
        raise self.retry(countdown=delays[self.request.retries])
    logger.warning(
        'Post-call reconciliation exhausted for tenant=%s user=%s destination_suffix=%s',
        tenant_id, user_id, suffix[-4:],
    )
    return {'found': False, 'sync': result}


@shared_task(bind=True, base=TelephonyTask, max_retries=4)
def archive_call_recording(self, call_log_id):
    """Copy a TeleCMI recording into the tenant's private Zata bucket."""
    from integrations.utils.encryption import EncryptionError
    from telephony.services import telecmi_client
    from telephony.services.crypto import decrypt_secret
    from telephony.services.token_service import get_tenant_credential, TokenServiceError
    from telephony.services.zata_storage import (
        archive_telecmi_response, get_storage_credential, ZataStorageError,
    )

    try:
        call_log = CallLog.objects.get(pk=call_log_id)
    except CallLog.DoesNotExist:
        return {'status': 'missing_call'}
    if not call_log.recording_file:
        return {'status': 'no_recording'}
    if call_log.recording_storage_status == RecordingStorageStatusEnum.ARCHIVED:
        return {'status': 'already_archived'}

    try:
        get_storage_credential(call_log.tenant_id)
    except ZataStorageError:
        return {'status': 'zata_not_configured'}

    CallLog.objects.filter(pk=call_log_id).update(
        recording_storage_status=RecordingStorageStatusEnum.ARCHIVING,
        recording_archive_attempts=call_log.recording_archive_attempts + 1,
        recording_archive_error='',
    )
    response = None
    try:
        credential = get_tenant_credential(call_log.tenant_id)
        secret = decrypt_secret(credential)
        response = telecmi_client.stream_recording(
            credential.app_id, secret, call_log.recording_file
        )
        stored = archive_telecmi_response(call_log, response)
    except (TokenServiceError, EncryptionError, telecmi_client.TeleCMIError, ZataStorageError) as exc:
        CallLog.objects.filter(pk=call_log_id).update(
            recording_storage_status=RecordingStorageStatusEnum.FAILED,
            recording_archive_error=str(exc)[:2000],
        )
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=min(900, 60 * (2 ** self.request.retries)))
        return {'status': 'failed', 'error': str(exc)}
    finally:
        if response is not None:
            response.close()

    CallLog.objects.filter(pk=call_log_id).update(
        recording_storage_status=RecordingStorageStatusEnum.ARCHIVED,
        recording_object_key=stored['object_key'],
        recording_content_type=stored['content_type'],
        recording_size=stored['size'],
        recording_sha256=stored['sha256'],
        recording_archived_at=timezone.now(),
        recording_archive_error='',
    )
    return {'status': 'archived', 'object_key': stored['object_key']}
