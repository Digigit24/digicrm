"""
Resolve which TeleCMI extension a browser softphone should log in as.

Why this module exists
----------------------
`WebRTCConfigView` used to require a per-user `TeleCMIAgent` row and return
424 when it was missing. The tenant settings UI only ever creates the
tenant-level `TeleCMICredential`, so every tenant hit 424 on every page load
and the softphone could never connect for anyone immediately after setup.

Resolution order
----------------
  1. the caller's own `TeleCMIAgent` row (tenant_id, user_id, is_active)
  2. the tenant credential's shared default extension
  3. unresolvable — the caller gets a 424 that says which of the two is missing

Per-user rows keep winning so that a tenant which *has* provisioned individual
extensions is unaffected by the fallback.

On the password
---------------
`piopiy.login(user_id, password, SBC_URI)` performs a SIP REGISTER against the
SBC using `password` as the digest secret (see `piopiyjs/lib/piopiy.js`: it
builds `{authorization_user, password, register: true}` and hands it to JsSIP).
Digest auth is challenge-response over the shared secret, so the browser must
hold the real password — a REST bearer token from `/v2/user/login` cannot
stand in for it. The SDK fetches that token itself, separately, for its
websocket; it is not an alternative credential.

So `resolve_softphone_auth()` returns the plaintext password and the callers
are responsible for only ever handing it to an authenticated, permission-gated
response. Nothing here writes it to a log.
"""
import logging

from integrations.utils.encryption import EncryptionError as LegacyEncryptionError
from integrations.utils.encryption import decrypt_token
from telephony.services.crypto import decrypt_default_agent_password

logger = logging.getLogger(__name__)

# Reasons returned alongside a 424 so the frontend — and whoever is reading the
# logs at 2am — can tell the two failure modes apart.
REASON_TENANT_NOT_CONFIGURED = 'tenant_not_configured'
REASON_NO_AGENT = 'no_agent'

SOURCE_USER = 'user'
SOURCE_TENANT = 'tenant'


class SoftphoneConfigError(Exception):
    """Raised when no extension can be resolved for this user."""

    def __init__(self, message, reason):
        super().__init__(message)
        self.reason = reason


def resolve_softphone_auth(credential, tenant_id, user_id):
    """
    Return `(telecmi_user_id, password, source)` for this user's softphone.

    `credential` is the tenant's active `TeleCMICredential`. Raises
    `SoftphoneConfigError` with a `.reason` of `no_agent` when neither a
    personal extension nor a tenant default is usable.
    """
    from telephony.models import TeleCMIAgent

    agent = TeleCMIAgent.objects.filter(
        tenant_id=tenant_id, user_id=user_id, is_active=True
    ).first()

    if agent and agent.password_encrypted:
        try:
            # Per-user rows predate the envelope scheme and are still written
            # by TeleCMIAgentSerializer with the shared SECRET_KEY cipher.
            password = decrypt_token(agent.password_encrypted)
        except LegacyEncryptionError as exc:
            raise SoftphoneConfigError(
                'Your TeleCMI extension password cannot be decrypted. Ask an '
                'admin to re-enter it under Settings -> TeleCMI -> Agents.',
                REASON_NO_AGENT,
            ) from exc
        return agent.telecmi_user_id, password, SOURCE_USER

    if credential.has_default_agent:
        try:
            password = decrypt_default_agent_password(credential)
        except LegacyEncryptionError as exc:
            raise SoftphoneConfigError(
                'This tenant\'s shared TeleCMI extension password cannot be '
                'decrypted. Ask an admin to re-enter it under Settings -> '
                'TeleCMI.',
                REASON_TENANT_NOT_CONFIGURED,
            ) from exc
        return credential.default_agent_id, password, SOURCE_TENANT

    raise SoftphoneConfigError(
        'TeleCMI is connected for this tenant, but no calling extension is '
        'available for you. Set a default extension under Settings -> TeleCMI, '
        'or give this user their own extension under Settings -> TeleCMI -> '
        'Agents.',
        REASON_NO_AGENT,
    )
