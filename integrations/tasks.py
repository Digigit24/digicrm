"""
Celery tasks for Integration System

Background tasks for:
- Polling workflows for trigger detection
- Executing workflows
- Refreshing OAuth tokens
- Retrying failed executions
"""

import logging
from datetime import timedelta
from django.utils import timezone
from celery import shared_task, Task
from celery.exceptions import MaxRetriesExceededError

from integrations.models import (
    Workflow, Connection, ExecutionLog,
    WorkflowTrigger, TriggerTypeEnum,
    ExecutionStatusEnum, ConnectionStatusEnum
)
from integrations.services.workflow_engine import (
    WorkflowEngine, WorkflowEngineError, execute_workflow_by_id
)
from integrations.utils.oauth import get_oauth_handler, OAuthError
from integrations.utils.encryption import encrypt_token, decrypt_token

logger = logging.getLogger(__name__)


class CallbackTask(Task):
    """
    Base task with callbacks for error handling.
    """
    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """Log task failure"""
        logger.error(
            f"Task {self.name} failed: {exc}",
            extra={'task_id': task_id, 'args': args, 'kwargs': kwargs},
            exc_info=True
        )


@shared_task(base=CallbackTask, bind=True, max_retries=3)
def poll_workflow_triggers(self):
    """
    Periodic task to poll all active workflow triggers.

    This task should run every 5-10 minutes via Celery Beat.
    It checks all active workflows and executes them if trigger conditions are met.
    """
    logger.info("Starting workflow trigger polling...")

    try:
        # Get all active workflows with triggers
        workflows = Workflow.objects.filter(
            is_active=True,
            is_deleted=False
        ).select_related('connection', 'trigger').prefetch_related('actions')

        executed_count = 0
        error_count = 0

        for workflow in workflows:
            try:
                # Check if workflow has a trigger
                if not hasattr(workflow, 'trigger'):
                    logger.warning(f"Workflow {workflow.id} has no trigger configured")
                    continue

                trigger = workflow.trigger

                # Check if it's time to poll this trigger
                if not trigger.should_poll():
                    continue

                # Update last checked time
                trigger.last_checked_at = timezone.now()
                trigger.save(update_fields=['last_checked_at', 'updated_at'])

                # Execute workflow asynchronously
                execute_workflow_async.delay(workflow.id)
                executed_count += 1

            except Exception as e:
                logger.error(f"Error polling workflow {workflow.id}: {e}", exc_info=True)
                error_count += 1

        logger.info(
            f"Workflow trigger polling completed. "
            f"Triggered: {executed_count}, Errors: {error_count}"
        )

        return {
            'triggered': executed_count,
            'errors': error_count
        }

    except Exception as e:
        logger.error(f"Workflow trigger polling failed: {e}", exc_info=True)
        raise self.retry(exc=e, countdown=300)  # Retry after 5 minutes


@shared_task(base=CallbackTask, bind=True, max_retries=3)
def execute_workflow_async(self, workflow_id: int):
    """
    Execute a workflow asynchronously.

    Args:
        workflow_id: The workflow ID to execute

    Returns:
        Dict with execution results
    """
    logger.info(f"Executing workflow {workflow_id}...")

    try:
        execution_logs = execute_workflow_by_id(workflow_id)

        logger.info(f"Workflow {workflow_id} executed. Logs: {len(execution_logs)}")

        return {
            'workflow_id': workflow_id,
            'execution_count': len(execution_logs),
            'execution_ids': [str(log.execution_id) for log in execution_logs]
        }

    except WorkflowEngineError as e:
        logger.error(f"Workflow {workflow_id} execution failed: {e}")
        raise self.retry(exc=e, countdown=60)  # Retry after 1 minute

    except Exception as e:
        logger.error(f"Unexpected error executing workflow {workflow_id}: {e}", exc_info=True)
        raise self.retry(exc=e, countdown=60)


@shared_task(base=CallbackTask, bind=True, max_retries=5)
def refresh_connection_token(self, connection_id: int):
    """
    Refresh OAuth token for a connection.

    Args:
        connection_id: The connection ID

    Returns:
        Dict with refresh status
    """
    logger.info(f"Refreshing token for connection {connection_id}...")

    try:
        connection = Connection.objects.get(id=connection_id)

        if not connection.refresh_token_encrypted:
            logger.error(f"Connection {connection_id} has no refresh token")
            return {'status': 'error', 'message': 'No refresh token'}

        # Decrypt refresh token
        refresh_token = decrypt_token(connection.refresh_token_encrypted)

        # Refresh token
        oauth_handler = get_oauth_handler()
        token_data = oauth_handler.refresh_access_token(refresh_token)

        # Update connection
        connection.access_token_encrypted = encrypt_token(token_data['access_token'])
        if token_data.get('refresh_token'):
            connection.refresh_token_encrypted = encrypt_token(token_data['refresh_token'])
        connection.token_expires_at = token_data.get('expires_at')
        connection.status = ConnectionStatusEnum.CONNECTED
        connection.save()

        logger.info(f"Token refreshed for connection {connection_id}")

        return {
            'status': 'success',
            'connection_id': connection_id,
            'expires_at': connection.token_expires_at.isoformat() if connection.token_expires_at else None
        }

    except Connection.DoesNotExist:
        logger.error(f"Connection {connection_id} not found")
        return {'status': 'error', 'message': 'Connection not found'}

    except OAuthError as e:
        logger.error(f"Token refresh failed for connection {connection_id}: {e}")
        connection.mark_as_error(str(e))
        raise self.retry(exc=e, countdown=300)  # Retry after 5 minutes

    except Exception as e:
        logger.error(f"Unexpected error refreshing token: {e}", exc_info=True)
        raise self.retry(exc=e, countdown=300)


@shared_task(base=CallbackTask)
def refresh_expiring_tokens():
    """
    Periodic task to refresh tokens that are about to expire.

    Should run every hour via Celery Beat.
    Refreshes tokens expiring in the next 24 hours.
    """
    logger.info("Checking for expiring tokens...")

    try:
        # Get connections with tokens expiring in next 24 hours
        expiry_threshold = timezone.now() + timedelta(hours=24)

        connections = Connection.objects.filter(
            status=ConnectionStatusEnum.CONNECTED,
            token_expires_at__lte=expiry_threshold,
            token_expires_at__isnull=False
        )

        refreshed_count = 0
        error_count = 0

        for connection in connections:
            try:
                refresh_connection_token.delay(connection.id)
                refreshed_count += 1
            except Exception as e:
                logger.error(f"Failed to queue token refresh for connection {connection.id}: {e}")
                error_count += 1

        logger.info(
            f"Token refresh check completed. "
            f"Queued: {refreshed_count}, Errors: {error_count}"
        )

        return {
            'queued': refreshed_count,
            'errors': error_count
        }

    except Exception as e:
        logger.error(f"Expiring token check failed: {e}", exc_info=True)
        return {'status': 'error', 'message': str(e)}


@shared_task(base=CallbackTask, bind=True, max_retries=3)
def retry_failed_execution(self, execution_log_id: int):
    """
    Retry a failed workflow execution.

    Args:
        execution_log_id: The execution log ID to retry

    Returns:
        Dict with retry status
    """
    logger.info(f"Retrying failed execution {execution_log_id}...")

    try:
        execution_log = ExecutionLog.objects.get(id=execution_log_id)

        if execution_log.status != ExecutionStatusEnum.FAILED:
            logger.warning(f"Execution {execution_log_id} is not in FAILED status")
            return {'status': 'skipped', 'message': 'Not a failed execution'}

        # Check max retries
        if execution_log.retry_count >= 3:
            logger.warning(f"Execution {execution_log_id} has exceeded max retries")
            return {'status': 'skipped', 'message': 'Max retries exceeded'}

        workflow = execution_log.workflow
        trigger_data = execution_log.trigger_data

        # Execute workflow with same trigger data
        engine = WorkflowEngine(workflow)
        new_logs = engine.execute_workflow([trigger_data] if trigger_data else None)

        if new_logs:
            new_log = new_logs[0]
            new_log.is_retry = True
            new_log.parent_execution_id = execution_log.execution_id
            new_log.retry_count = execution_log.retry_count + 1
            new_log.save()

            logger.info(f"Retried execution {execution_log_id}. New execution: {new_log.execution_id}")

            return {
                'status': 'success',
                'original_execution_id': str(execution_log.execution_id),
                'new_execution_id': str(new_log.execution_id),
                'retry_count': new_log.retry_count
            }

        return {'status': 'skipped', 'message': 'No trigger data to retry'}

    except ExecutionLog.DoesNotExist:
        logger.error(f"Execution log {execution_log_id} not found")
        return {'status': 'error', 'message': 'Execution log not found'}

    except Exception as e:
        logger.error(f"Retry execution failed: {e}", exc_info=True)
        raise self.retry(exc=e, countdown=120)  # Retry after 2 minutes


@shared_task(base=CallbackTask)
def cleanup_old_execution_logs():
    """
    Periodic task to clean up old execution logs.

    Should run daily via Celery Beat.
    Deletes execution logs older than 90 days.
    """
    logger.info("Cleaning up old execution logs...")

    try:
        cutoff_date = timezone.now() - timedelta(days=90)

        deleted_count, _ = ExecutionLog.objects.filter(
            started_at__lt=cutoff_date
        ).delete()

        logger.info(f"Deleted {deleted_count} old execution logs")

        return {
            'deleted': deleted_count,
            'cutoff_date': cutoff_date.isoformat()
        }

    except Exception as e:
        logger.error(f"Cleanup failed: {e}", exc_info=True)
        return {'status': 'error', 'message': str(e)}


@shared_task(base=CallbackTask)
def check_connection_health():
    """
    Periodic task to check health of all connections.

    Should run daily via Celery Beat.
    Marks unhealthy connections as ERROR status.
    """
    logger.info("Checking connection health...")

    try:
        from integrations.services.google_sheets import create_sheets_service, GoogleSheetsError

        connections = Connection.objects.filter(
            status=ConnectionStatusEnum.CONNECTED
        )

        healthy_count = 0
        unhealthy_count = 0

        for connection in connections:
            try:
                # Try to make a simple API call
                sheets_service = create_sheets_service(connection)
                sheets_service.list_spreadsheets(page_size=1)

                # Update last used
                connection.last_used_at = timezone.now()
                connection.save(update_fields=['last_used_at'])

                healthy_count += 1

            except GoogleSheetsError as e:
                logger.error(f"Connection {connection.id} health check failed: {e}")
                connection.mark_as_error(str(e))
                unhealthy_count += 1

        logger.info(
            f"Connection health check completed. "
            f"Healthy: {healthy_count}, Unhealthy: {unhealthy_count}"
        )

        return {
            'healthy': healthy_count,
            'unhealthy': unhealthy_count
        }

    except Exception as e:
        logger.error(f"Connection health check failed: {e}", exc_info=True)
        return {'status': 'error', 'message': str(e)}
