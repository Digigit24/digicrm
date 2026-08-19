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
  2. the calling profile explicitly assigned to this user
  3. the tenant's `is_default` calling profile
  4. the legacy tenant-wide `TeleCMICredential.default_agent_id`
  5. unresolvable — the caller gets a 424 saying which piece is missing

Per-user agent rows keep winning so that a tenant which *has* provisioned
individual extensions is unaffected by any of the fallbacks, and the legacy
tenant default keeps working at step 4 so no tenant configured before calling
profiles existed is orphaned.

The `source` returned alongside the credentials tells the UI which identity is
connected — "you are on the Sales line" rather than an anonymous dial tone.

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
from typing import NamedTuple, Optional

from django.db.models import Q

from integrations.utils.encryption import EncryptionError as LegacyEncryptionError
from integrations.utils.encryption import decrypt_token
from telephony.services.crypto import (
    decrypt_default_agent_password, decrypt_profile_password,
)

logger = logging.getLogger(__name__)

# Reasons returned alongside a 424 so the frontend — and whoever is reading the
# logs at 2am — can tell the two failure modes apart.
REASON_TENANT_NOT_CONFIGURED = 'tenant_not_configured'
REASON_NO_AGENT = 'no_agent'

SOURCE_USER = 'user'
SOURCE_ASSIGNED_PROFILE = 'assigned_profile'
SOURCE_TENANT_PROFILE = 'tenant_profile'
SOURCE_TENANT_DEFAULT = 'tenant_default'


class SoftphoneConfigError(Exception):
    """Raised when no extension can be resolved for this user."""

    def __init__(self, message, reason):
        super().__init__(message)
        self.reason = reason


class SoftphoneIdentity(NamedTuple):
    """The extension a browser softphone should register as, and where it came from."""

    telecmi_user_id: str
    password: str
    source: str
    caller_id: Optional[str] = None
    profile: object = None


def _profile_identity(profile, source):
    """Turn a usable calling profile into a `SoftphoneIdentity`."""
    try:
        password = decrypt_profile_password(profile)
    except LegacyEncryptionError as exc:
        raise SoftphoneConfigError(
            f'The password for calling profile "{profile.label}" cannot be '
            'decrypted. Ask an admin to re-enter it under Settings -> TeleCMI '
            '-> Calling profiles.',
            REASON_NO_AGENT,
        ) from exc
    return SoftphoneIdentity(
        telecmi_user_id=profile.telecmi_user_id,
        password=password,
        source=source,
        caller_id=profile.caller_id or None,
        profile=profile,
    )


def resolve_softphone_auth(credential, tenant_id, user_id):
    """
    Return the `SoftphoneIdentity` this user's softphone should register with.

    `credential` is the tenant's active `TeleCMICredential`. Raises
    `SoftphoneConfigError` with a `.reason` of `no_agent` when neither a
    personal extension, an assigned profile, a tenant default profile, nor the
    legacy tenant default extension is usable.

    The result is a NamedTuple, so `telecmi_user_id, password, source, ... =`
    still unpacks positionally in that order.
    """
    from telephony.models import (
        TeleCMIAgent, TeleCMICallingProfile, TeleCMIProfileAssignment,
    )

    # 1. The user's own extension always wins — existing rows keep working.
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
        return SoftphoneIdentity(
            telecmi_user_id=agent.telecmi_user_id,
            password=password,
            source=SOURCE_USER,
            caller_id=None,
            profile=None,
        )

    # 2. A profile an admin assigned to this specific user.
    assignment = (
        TeleCMIProfileAssignment.objects
        .filter(tenant_id=tenant_id, user_id=user_id)
        .select_related('profile')
        .first()
    )
    if assignment and assignment.profile.is_usable:
        return _profile_identity(assignment.profile, SOURCE_ASSIGNED_PROFILE)

    # 3. The tenant's default profile — everyone with no assignment lands here.
    tenant_profile = TeleCMICallingProfile.objects.filter(
        tenant_id=tenant_id, is_default=True, is_active=True
    ).first()
    if tenant_profile and tenant_profile.is_usable:
        return _profile_identity(tenant_profile, SOURCE_TENANT_PROFILE)

    # 4. The pre-profiles tenant-wide extension. Still supported: tenants
    #    configured before calling profiles existed must not be orphaned.
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
        return SoftphoneIdentity(
            telecmi_user_id=credential.default_agent_id,
            password=password,
            source=SOURCE_TENANT_DEFAULT,
            caller_id=credential.default_caller_id or None,
            profile=None,
        )

    raise SoftphoneConfigError(
        'TeleCMI is connected for this tenant, but no calling extension is '
        'available for you. Ask an admin to add a calling profile under '
        'Settings -> TeleCMI -> Calling profiles and assign it to you, or to '
        'give this user their own extension under Settings -> TeleCMI -> '
        'Agents.',
        REASON_NO_AGENT,
    )


def usable_profiles_for(tenant_id, user_id):
    """
    The calling profiles a non-admin user is allowed to see.

    That is: the one assigned to them, plus the tenant default they would fall
    back to. Deliberately not "every profile in the tenant" — a sales rep has
    no business reading the support line's configuration.
    """
    from telephony.models import TeleCMICallingProfile, TeleCMIProfileAssignment

    assigned_ids = list(
        TeleCMIProfileAssignment.objects
        .filter(tenant_id=tenant_id, user_id=user_id)
        .values_list('profile_id', flat=True)
    )
    return TeleCMICallingProfile.objects.filter(
        Q(tenant_id=tenant_id) & (Q(id__in=assigned_ids) | Q(is_default=True))
    )


def push_caller_id(identity):
    """
    Best-effort: make TeleCMI present this profile's caller ID.

    Caller ID is a property of the *extension* on TeleCMI's side, switched with
    `POST /v2/set_callerid`; the WebRTC SDK's `piopiy.call()` has no per-call
    caller-ID parameter (see `_plans/07-telecmi-multi-callerid.md`). So the only
    place to pin it is here, as the session resolves.

    Three deliberate properties:

      * **Never fatal.** A failure to switch the number must not stop a user
        from connecting a phone. Everything is caught and logged.
      * **Idempotent and cheap.** TeleCMI's `get_callerid` has no "currently
        active" flag, so `caller_id_pushed_value` is our only record of what we
        last set. When it already matches, we make no HTTP calls at all —
        otherwise every page load would cost a login plus a set.
      * **Only for profiles.** The legacy tenant default and per-user agent rows
        keep their existing behaviour untouched.

    Returns True when a push actually succeeded.
    """
    from django.utils import timezone

    from telephony.services import telecmi_client as client

    profile = identity.profile
    caller_id = identity.caller_id
    if not profile or not caller_id:
        return False
    if profile.caller_id_pushed_value == caller_id:
        return False

    try:
        if profile.is_token_stale():
            token = client.get_user_login_token(
                profile.telecmi_user_id, identity.password
            )
            profile.cached_token = token
            profile.token_obtained_at = timezone.now()
        else:
            token = profile.cached_token

        client.set_caller_id(token, caller_id)
    except Exception:  # noqa: BLE001 — never block a softphone on this
        logger.warning(
            'Could not push caller ID for calling profile %s (extension %s); '
            'the softphone still connects.',
            profile.id, profile.telecmi_user_id, exc_info=True,
        )
        return False

    profile.caller_id_pushed_value = caller_id
    profile.save(
        update_fields=[
            'cached_token', 'token_obtained_at', 'caller_id_pushed_value',
            'updated_at',
        ]
    )
    return True
