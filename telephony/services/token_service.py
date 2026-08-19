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

    Resolution mirrors the softphone config endpoint so that every telephony
    action agrees on which extension a user is:

      1. the user's own `TeleCMIAgent` row, using its cached token when fresh;
      2. the calling profile assigned to them, then the tenant default profile,
         each caching its token on its own row;
      3. otherwise the tenant's legacy shared default extension, cached on the
         credential row.

    Without steps 2 and 3 a user who resolves to anything but a personal agent
    row would get a softphone that connects but 424s on click-to-call, SMS,
    caller IDs, breaks, callbacks and notes — every one of those funnels
    through here.

    Raises TokenServiceError if none resolves or login fails.
    """
    # Import here to avoid circular imports at module load time
    from telephony.models import TeleCMIAgent

    agent = TeleCMIAgent.objects.filter(
        tenant_id=tenant_id, user_id=user_id, is_active=True
    ).first()

    if agent:
        if not agent.is_token_stale():
            logger.debug('Using cached TeleCMI token for user %s', user_id)
            return agent.cached_token
        logger.info('TeleCMI token stale for user %s, re-authenticating', user_id)
        return _refresh_token(agent)

    profile = _resolve_profile(tenant_id, user_id)
    if profile is not None:
        if not profile.is_token_stale():
            logger.debug('Using cached calling-profile token for user %s', user_id)
            return profile.cached_token
        return _refresh_profile_token(profile)

    return _get_tenant_default_token(tenant_id, user_id)


def _resolve_profile(tenant_id, user_id):
    """
    The calling profile this user's REST calls should authenticate as.

    Same order as `softphone_service.resolve_softphone_auth` steps 2 and 3 —
    assigned profile first, then the tenant default — so the extension a user's
    softphone registers as is the same one their click-to-call runs from.
    """
    from telephony.models import TeleCMICallingProfile, TeleCMIProfileAssignment

    assignment = (
        TeleCMIProfileAssignment.objects
        .filter(tenant_id=tenant_id, user_id=user_id)
        .select_related('profile')
        .first()
    )
    if assignment and assignment.profile.is_usable:
        return assignment.profile

    profile = TeleCMICallingProfile.objects.filter(
        tenant_id=tenant_id, is_default=True, is_active=True
    ).first()
    if profile and profile.is_usable:
        return profile
    return None


def _refresh_profile_token(profile) -> str:
    """Log a calling profile's extension in and cache the token on its row."""
    from django.utils import timezone

    from telephony.services.crypto import decrypt_profile_password

    try:
        password = decrypt_profile_password(profile)
    except EncryptionError as exc:
        raise TokenServiceError(
            f'The password for calling profile "{profile.label}" cannot be '
            'decrypted. Re-enter it under Settings -> TeleCMI -> Calling '
            'profiles and save.'
        ) from exc

    try:
        token = get_user_login_token(profile.telecmi_user_id, password)
    except TeleCMIError as exc:
        raise TokenServiceError(
            f'TeleCMI login failed for calling profile "{profile.label}" '
            f'({profile.telecmi_user_id}): {exc}'
        )

    profile.cached_token = token
    profile.token_obtained_at = timezone.now()
    profile.save(update_fields=['cached_token', 'token_obtained_at', 'updated_at'])
    logger.info('Refreshed TeleCMI token for calling profile %s', profile.id)
    return token


def _get_tenant_default_token(tenant_id, user_id) -> str:
    """Token for the tenant-wide shared extension, refreshed when stale."""
    from django.utils import timezone
    from telephony.services.crypto import decrypt_default_agent_password

    credential = get_tenant_credential(tenant_id)
    if not credential.has_default_agent:
        raise TokenServiceError(
            f'No TeleCMI extension is available for user {user_id} in tenant '
            f'{tenant_id}. Ask your admin to set a default extension under '
            'Settings -> TeleCMI, or to give this user their own extension '
            'under Settings -> TeleCMI -> Agents.'
        )

    if not credential.is_default_token_stale():
        logger.debug('Using cached tenant-default TeleCMI token for %s', tenant_id)
        return credential.default_agent_token

    try:
        password = decrypt_default_agent_password(credential)
    except EncryptionError as exc:
        raise TokenServiceError(
            'The shared TeleCMI extension password cannot be decrypted. '
            'Re-enter it under Settings -> TeleCMI and save.'
        ) from exc

    try:
        token = get_user_login_token(credential.default_agent_id, password)
    except TeleCMIError as exc:
        raise TokenServiceError(
            f'TeleCMI login failed for shared extension '
            f'{credential.default_agent_id}: {exc}'
        )

    credential.default_agent_token = token
    credential.default_agent_token_obtained_at = timezone.now()
    credential.save(update_fields=[
        'default_agent_token', 'default_agent_token_obtained_at', 'updated_at',
    ])
    logger.info('Refreshed tenant-default TeleCMI token for tenant %s', tenant_id)
    return token


def _refresh_token(agent) -> str:
    """Decrypt password, call TeleCMI login, persist new token."""
    from django.utils import timezone

    try:
        password = decrypt_token(agent.password_encrypted)
    except EncryptionError as exc:
        raise TokenServiceError(
            'TeleCMI agent password cannot be decrypted. '
            'The encryption key may have changed. '
            'Go to Settings → TeleCMI → Agents, re-enter the password for this agent, and save.'
        ) from exc

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
        return

    # No personal row: the user is on whichever calling profile — or the legacy
    # shared extension — actually served them, so that is the token to clear.
    profile = _resolve_profile(tenant_id, user_id)
    if profile is not None:
        profile.cached_token = None
        profile.token_obtained_at = None
        profile.save(update_fields=['cached_token', 'token_obtained_at', 'updated_at'])
        logger.info('Invalidated TeleCMI token for calling profile %s', profile.id)
        return

    invalidate_tenant_default_token(tenant_id)


def invalidate_tenant_default_token(tenant_id) -> None:
    """
    Drop the cached token for a tenant's shared extension.

    Called whenever the shared extension or its password changes, so a
    just-corrected credential takes effect on the next request instead of after
    the 20-hour refresh window.
    """
    from telephony.models import TeleCMICredential

    updated = TeleCMICredential.objects.filter(tenant_id=tenant_id).update(
        default_agent_token=None, default_agent_token_obtained_at=None
    )
    if updated:
        logger.info('Invalidated tenant-default TeleCMI token for tenant %s', tenant_id)


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
