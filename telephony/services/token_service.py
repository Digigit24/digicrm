"""
TeleCMI token management.

Fetches and caches per-agent login tokens for TeleCMI REST API calls.
Tokens are obtained via POST /v2/user/login and stored in TeleCMIAgent.
Pattern mirrors how Connection tokens are managed in the integrations app.
"""
import logging
from integrations.utils.encryption import encrypt_token, decrypt_token, EncryptionError
from telephony.services.telecmi_client import get_user_login_token, TeleCMIError

logger = logging.getLogger(__name__)


class TokenServiceError(Exception):
    pass


def get_agent_token(tenant_id, user_id) -> str:
    """
    Return a valid TeleCMI token for the given CRM user.

    Checks TeleCMIAgent.cached_token freshness first.
    If stale or absent, re-authenticates with TeleCMI and caches the new token.

    Raises TokenServiceError if no agent record exists or login fails.
    """
    # Import here to avoid circular imports at module load time
    from telephony.models import TeleCMIAgent

    try:
        agent = TeleCMIAgent.objects.get(tenant_id=tenant_id, user_id=user_id, is_active=True)
    except TeleCMIAgent.DoesNotExist:
        raise TokenServiceError(
            f'No active TeleCMI agent configured for user {user_id} in tenant {tenant_id}. '
            'Ask your admin to set up telephony credentials.'
        )

    if not agent.is_token_stale():
        logger.debug('Using cached TeleCMI token for user %s', user_id)
        return agent.cached_token

    logger.info('TeleCMI token stale for user %s, re-authenticating', user_id)
    return _refresh_token(agent)


def _refresh_token(agent) -> str:
    """Decrypt password, call TeleCMI login, persist new token."""
    from django.utils import timezone

    try:
        password = decrypt_token(agent.password_encrypted)
    except EncryptionError as exc:
        raise TokenServiceError(f'Failed to decrypt TeleCMI agent password: {exc}')

    try:
        token = get_user_login_token(agent.telecmi_user_id, password)
    except TeleCMIError as exc:
        raise TokenServiceError(f'TeleCMI login failed for {agent.telecmi_user_id}: {exc}')

    agent.cached_token = token
    agent.token_obtained_at = timezone.now()
    agent.save(update_fields=['cached_token', 'token_obtained_at', 'updated_at'])
    logger.info('TeleCMI token refreshed for user %s', agent.user_id)
    return token


def invalidate_token(tenant_id, user_id) -> None:
    """Clear the cached token for an agent (e.g. after a 401 response)."""
    from telephony.models import TeleCMIAgent

    updated = TeleCMIAgent.objects.filter(tenant_id=tenant_id, user_id=user_id).update(
        cached_token=None, token_obtained_at=None
    )
    if updated:
        logger.info('Invalidated TeleCMI token for user %s in tenant %s', user_id, tenant_id)


def get_tenant_credential(tenant_id):
    """
    Return the TeleCMICredential for a tenant, or raise TokenServiceError.
    Used by views that need app_id/sbc_region without a per-user token.
    """
    from telephony.models import TeleCMICredential

    try:
        return TeleCMICredential.objects.get(tenant_id=tenant_id, is_active=True)
    except TeleCMICredential.DoesNotExist:
        raise TokenServiceError(
            f'TeleCMI is not configured for tenant {tenant_id}. '
            'Connect TeleCMI under Integrations → TeleCMI.'
        )
