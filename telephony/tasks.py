"""
Celery tasks for the telephony app.

Background tasks for:
- Periodic CDR sync safety net (catches missed/misconfigured webhooks)
"""
import logging

from celery import shared_task, Task

from telephony.models import TeleCMIAgent
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
