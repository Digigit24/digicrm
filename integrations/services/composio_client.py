"""
Composio client wrapper.

The ONLY place in digicrm that imports the ``composio`` SDK. Every view, task,
serializer and management command goes through ``ComposioClient`` so that:

  * ``COMPOSIO_API_KEY`` is read from settings in exactly one place and can
    never leak into a response, a model field or a log line;
  * every call gets uniform retry/backoff, circuit breaking and error
    translation;
  * responses are scrubbed of credential material before they reach a model,
    a serializer or a log line.

Concept mapping::

    Composio                          digicrm
    ------------------------------    -------------------------------------
    auth config       (ac_...)        integrations.ComposioAuthConfig
    connected account (ca_...)        integrations.ComposioConnection
    user_id / entity                  build_composio_user_id(tenant, user)
    toolkit slug                      integrations.ComposioToolkit.slug

Verified against ``composio==0.19.0`` (see ``requirements.txt``).

IMPORTANT - ``connected_accounts.initiate()`` is dead for Composio-managed
OAuth. Composio sunset it on 2026-05-08 (new orgs) and 2026-07-03 (all orgs);
both dates have passed, and ``POST /api/v3/connected_accounts`` now returns
400 for managed OAuth1/OAuth2/DCR_OAUTH auth configs. All four of our priority
toolkits (GMAIL, NOTION, GOOGLEDRIVE, GOOGLECALENDAR) are Composio-managed
OAuth2, so ``connected_accounts.link()`` is the only viable call. Do not
"restore" ``initiate()`` from an older tutorial.
"""

import base64
import hashlib
import hmac
import logging
import random
import time
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ComposioError(Exception):
    """Base for all Composio failures surfaced to callers."""

    def __init__(self, message, status_code=None, code=None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class ComposioNotConfigured(ComposioError):
    """COMPOSIO_API_KEY missing, COMPOSIO_ENABLED is False, or SDK not installed."""


class ComposioRateLimited(ComposioError):
    """429 from Composio after exhausting retries."""


class ComposioNotFound(ComposioError):
    """404 - auth config or connected account does not exist at Composio."""


class ComposioIdentityMismatch(ComposioError):
    """
    A stored ``composio_user_id`` does not match the one derived from the
    request's tenant/user. Treated as tampering: never fall back to the stored
    value, never proceed with the call.
    """


class ComposioWebhookVerificationError(ComposioError):
    """Inbound webhook signature or timestamp failed verification."""


# ---------------------------------------------------------------------------
# Entity id - the single isolation boundary
# ---------------------------------------------------------------------------

TENANT_SCOPE_SENTINEL = 'tenant'


def build_composio_user_id(tenant_id, user_id=None) -> str:
    """
    Deterministic Composio entity id for a (tenant, user) pair.

        {namespace}:{tenant_id}:{user_id}     per-user connection
        {namespace}:{tenant_id}:tenant        tenant-wide shared connection

    Composio's ``user_id`` is an opaque string and the ONLY isolation boundary
    Composio enforces between end users, so it must encode the tenant. The
    namespace prefix (``COMPOSIO_USER_NAMESPACE``: celiyo-dev / celiyo-staging /
    celiyo-prod) makes it structurally impossible for a staging deployment
    pointed at the same Composio project to address a production entity.

    ``tenant`` is not a valid UUID, so the shared-connection form can never
    collide with a per-user id.

    This function is the single source of truth. Never inline the format
    anywhere else, and never accept an entity id from a request payload.
    """
    if not tenant_id:
        raise ComposioError('tenant_id is required to build a Composio user id')
    namespace = getattr(settings, 'COMPOSIO_USER_NAMESPACE', 'celiyo-dev')
    if not namespace:
        raise ComposioError('COMPOSIO_USER_NAMESPACE must not be empty')
    scope = str(user_id) if user_id else TENANT_SCOPE_SENTINEL
    return f"{namespace}:{tenant_id}:{scope}"


def assert_connection_identity(connection, tenant_id=None):
    """
    Re-derive the entity id for a stored connection and refuse to act on it if
    the stored value disagrees.

    This is layer 3 of the four defence layers in the plan (middleware ->
    queryset -> entity assertion -> never-list-unscoped). It catches a row that
    somehow escaped the queryset filter, a hand-edited database row, and any
    future change to the derivation rule.

    ``tenant_id`` (when given, from the request) is checked against the row
    first, so a row belonging to another tenant is rejected even if its own
    ``composio_user_id`` is internally consistent.
    """
    from integrations.models import ComposioConnectionScopeEnum

    if tenant_id is not None and str(connection.tenant_id) != str(tenant_id):
        logger.error(
            'Composio tenant mismatch: conn=%s row_tenant=%s request_tenant=%s',
            connection.public_id, connection.tenant_id, tenant_id,
        )
        raise ComposioIdentityMismatch('Connection does not belong to this tenant')

    if connection.scope == ComposioConnectionScopeEnum.TENANT:
        expected = build_composio_user_id(connection.tenant_id, None)
    else:
        expected = build_composio_user_id(connection.tenant_id, connection.user_id)

    if connection.composio_user_id != expected:
        logger.error(
            'Composio entity mismatch: conn=%s stored=%s expected=%s',
            connection.public_id, connection.composio_user_id, expected,
        )
        raise ComposioIdentityMismatch('Connection does not belong to this tenant')

    return expected


# ---------------------------------------------------------------------------
# Scrubbing
# ---------------------------------------------------------------------------

_SECRET_KEYS = {
    'val', 'access_token', 'refresh_token', 'id_token', 'api_key', 'apikey',
    'client_secret', 'client_id', 'password', 'token', 'secret', 'private_key',
    'authorization', 'credentials', 'link_token', 'generic_api_key',
    'bearer_token', 'session_token', 'code', 'code_verifier',
}

REDACTED = '[redacted]'


def scrub(obj: Any, _depth: int = 0) -> Any:
    """
    Recursively strip credential material from a Composio payload.

    Applied to EVERYTHING before it is persisted to a ``metadata`` column, put
    on an audit event, or logged. Composio already partially masks
    ``state.val``, but partial masking is not a storage policy - we drop it
    entirely.

    Depth-bounded so a pathological payload cannot blow the stack.
    """
    if _depth > 12:
        return REDACTED
    if isinstance(obj, dict):
        return {
            k: (REDACTED if str(k).lower() in _SECRET_KEYS else scrub(v, _depth + 1))
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return [scrub(v, _depth + 1) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    # Pydantic models and other SDK objects.
    if hasattr(obj, 'model_dump'):
        try:
            return scrub(obj.model_dump(), _depth + 1)
        except Exception:  # noqa: BLE001 - never let scrubbing raise
            return REDACTED
    return str(obj)


def to_plain(obj: Any) -> Any:
    """Best-effort conversion of an SDK response object into plain JSON data."""
    if obj is None or isinstance(obj, (str, int, float, bool, list, dict)):
        return obj
    if hasattr(obj, 'model_dump'):
        try:
            return obj.model_dump(mode='json')
        except Exception:  # noqa: BLE001
            try:
                return obj.model_dump()
            except Exception:  # noqa: BLE001
                return None
    if hasattr(obj, '__dict__'):
        return {k: v for k, v in vars(obj).items() if not k.startswith('_')}
    return None


# ---------------------------------------------------------------------------
# Webhook signature verification (pure Python, no SDK import)
# ---------------------------------------------------------------------------

_CIRCUIT_CACHE_KEY = 'composio:circuit:open'
_CIRCUIT_FAILURE_KEY = 'composio:circuit:failures'
_CIRCUIT_THRESHOLD = 5
_CIRCUIT_WINDOW_SECONDS = 60
_CIRCUIT_OPEN_SECONDS = 30


def verify_webhook_signature(*, webhook_id, webhook_timestamp, body, signature,
                             secret, tolerance_seconds=None, now=None):
    """
    Verify a Composio trigger webhook.

    Mirrors ``composio.triggers._verify_webhook_signature`` exactly, but
    implemented here so the callback view never has to import the SDK (and so
    it is unit-testable with no network and no ``COMPOSIO_API_KEY``).

    Scheme:
        signed = "{webhook-id}.{webhook-timestamp}.{raw body}"
        header = "v1,<base64(HMAC-SHA256(secret, signed))>"
                 (space-separated when Composio rotates secrets)

    Also rejects a replayed or clock-skewed delivery: ``webhook-timestamp`` is
    Unix seconds and must be within ``tolerance_seconds`` of now.

    :raises ComposioWebhookVerificationError: on any failure. There is no
        "unsigned is fine" path - a blank secret rejects everything.
    """
    if tolerance_seconds is None:
        tolerance_seconds = getattr(settings, 'COMPOSIO_WEBHOOK_TOLERANCE_SECONDS', 300)

    if not secret:
        raise ComposioWebhookVerificationError(
            'COMPOSIO_WEBHOOK_SECRET is not configured; refusing all inbound webhooks'
        )
    if not webhook_id:
        raise ComposioWebhookVerificationError('Missing webhook-id header')
    if not webhook_timestamp:
        raise ComposioWebhookVerificationError('Missing webhook-timestamp header')
    if not signature:
        raise ComposioWebhookVerificationError('Missing webhook-signature header')
    if body is None:
        raise ComposioWebhookVerificationError('Missing webhook body')

    # Replay window first - a stale delivery is rejected even if it is validly
    # signed, so a captured request cannot be replayed forever.
    try:
        sent_at = int(float(webhook_timestamp))
    except (TypeError, ValueError):
        raise ComposioWebhookVerificationError('Malformed webhook-timestamp header')

    current = int(now if now is not None else time.time())
    if tolerance_seconds and abs(current - sent_at) > int(tolerance_seconds):
        raise ComposioWebhookVerificationError(
            f'webhook-timestamp outside the {tolerance_seconds}s replay window'
        )

    if isinstance(body, bytes):
        payload = body.decode('utf-8', errors='replace')
    else:
        payload = str(body)

    signed = f"{webhook_id}.{webhook_timestamp}.{payload}"
    expected = base64.b64encode(
        hmac.new(
            key=secret.encode('utf-8'),
            msg=signed.encode('utf-8'),
            digestmod=hashlib.sha256,
        ).digest()
    ).decode('utf-8')

    provided = [part[3:] for part in str(signature).split(' ') if part.startswith('v1,')]
    if not provided:
        raise ComposioWebhookVerificationError(
            "No v1 signature in webhook-signature header (expected 'v1,<base64>')"
        )

    for candidate in provided:
        if hmac.compare_digest(candidate, expected):
            return True

    raise ComposioWebhookVerificationError('Webhook signature mismatch')


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class ComposioClient:
    """
    Thin, retrying wrapper over the Composio Python SDK (``composio==0.19.0``).

    Usage::

        client = get_composio_client()
        req = client.initiate_connection(
            composio_user_id=build_composio_user_id(tenant_id, user_id),
            auth_config_id='ac_...',
            callback_url='https://crm.celiyo.com/api/integrations/composio/callback/?state=...',
            alias='Sales inbox',
        )
        # req -> {'id': 'ca_...', 'status': 'INITIALIZING', 'redirect_url': 'https://...'}
    """

    RETRY_STATUSES = {429, 500, 502, 503, 504}

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key or getattr(settings, 'COMPOSIO_API_KEY', '')
        if not getattr(settings, 'COMPOSIO_ENABLED', False) or not self._api_key:
            raise ComposioNotConfigured(
                'Composio is not configured. Set COMPOSIO_API_KEY and COMPOSIO_ENABLED=true.'
            )
        try:
            from composio import Composio  # local import: optional dependency
        except ImportError as exc:
            raise ComposioNotConfigured(
                'The composio package is not installed. Add composio==0.19.0 to requirements.'
            ) from exc

        kwargs = {'api_key': self._api_key}
        base_url = getattr(settings, 'COMPOSIO_BASE_URL', None)
        if base_url:
            kwargs['base_url'] = base_url
        timeout = getattr(settings, 'COMPOSIO_HTTP_TIMEOUT', None)
        if timeout:
            kwargs['timeout'] = int(timeout)
        # The SDK has its own retry loop; we do our own so that backoff,
        # jitter and the circuit breaker are uniform and observable.
        kwargs['max_retries'] = 0
        self._sdk = Composio(**kwargs)

    # -- circuit breaker ---------------------------------------------------
    @staticmethod
    def _circuit_is_open() -> bool:
        try:
            return bool(cache.get(_CIRCUIT_CACHE_KEY))
        except Exception:  # noqa: BLE001 - cache must never break a call
            return False

    @staticmethod
    def _record_failure():
        """Open the circuit after N consecutive failures inside the window."""
        try:
            failures = (cache.get(_CIRCUIT_FAILURE_KEY) or 0) + 1
            cache.set(_CIRCUIT_FAILURE_KEY, failures, _CIRCUIT_WINDOW_SECONDS)
            if failures >= _CIRCUIT_THRESHOLD:
                cache.set(_CIRCUIT_CACHE_KEY, True, _CIRCUIT_OPEN_SECONDS)
                cache.delete(_CIRCUIT_FAILURE_KEY)
                logger.error(
                    'Composio circuit breaker OPEN for %ss after %s consecutive failures',
                    _CIRCUIT_OPEN_SECONDS, failures,
                )
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _record_success():
        try:
            cache.delete(_CIRCUIT_FAILURE_KEY)
        except Exception:  # noqa: BLE001
            pass

    # -- retry -------------------------------------------------------------
    def _call(self, label: str, fn, *args, **kwargs):
        """
        Invoke an SDK method with exponential backoff and full jitter.

        Retries only on 429/5xx and transport errors. Other 4xx are
        deterministic failures and raise immediately. ``Retry-After`` is
        honoured when Composio supplies it.
        """
        if self._circuit_is_open():
            raise ComposioError(
                'Composio is temporarily unavailable (circuit breaker open)',
                status_code=503,
            )

        max_retries = int(getattr(settings, 'COMPOSIO_MAX_RETRIES', 3))
        for attempt in range(max_retries + 1):
            try:
                result = fn(*args, **kwargs)
                self._record_success()
                return result
            except Exception as exc:  # noqa: BLE001 - SDK raises many types
                status = getattr(exc, 'status_code', None) or getattr(
                    getattr(exc, 'response', None), 'status_code', None)

                if status == 404:
                    raise ComposioNotFound(str(exc), status_code=404) from exc

                retryable = status in self.RETRY_STATUSES or status is None
                if not retryable or attempt == max_retries:
                    self._record_failure()
                    if status == 429:
                        raise ComposioRateLimited(str(exc), status_code=429) from exc
                    logger.error('Composio %s failed (status=%s): %s',
                                 label, status, exc, exc_info=True)
                    raise ComposioError(str(exc), status_code=status) from exc

                retry_after = getattr(exc, 'retry_after', None)
                try:
                    delay = float(retry_after) if retry_after else random.uniform(0, 2 ** attempt)
                except (TypeError, ValueError):
                    delay = random.uniform(0, 2 ** attempt)
                logger.warning('Composio %s retry %s/%s in %.2fs (status=%s)',
                               label, attempt + 1, max_retries, delay, status)
                time.sleep(delay)

    # -- toolkits ----------------------------------------------------------
    def list_toolkits(self, category=None, managed_by='composio', limit=200,
                      cursor=None, sort_by=None):
        """
        GET /api/v3.1/toolkits - one page of the catalogue.

        NOTE: ``composio==0.19.0``'s ``toolkits.list()`` accepts only
        ``category``, ``cursor``, ``limit``, ``sort_by`` and ``managed_by``.
        There is no ``search`` parameter (the REST API documents one, the SDK
        does not expose it) - catalogue search is done locally against the
        cached ComposioToolkit rows instead.
        """
        return self._call('toolkits.list', self._sdk.toolkits.list,
                          category=category, managed_by=managed_by,
                          limit=limit, cursor=cursor, sort_by=sort_by)

    def get_toolkit(self, slug: str):
        """GET /api/v3.1/toolkits/{slug}"""
        return self._call('toolkits.get', self._sdk.toolkits.get, str(slug).upper())

    # -- auth configs ------------------------------------------------------
    def create_managed_auth_config(self, toolkit_slug: str, name: str):
        """
        POST /api/v3.1/auth_configs using Composio-managed auth, i.e. Composio
        owns the OAuth app and we never hold a client secret. All four priority
        toolkits support this.
        """
        return self._call('auth_configs.create', self._sdk.auth_configs.create,
                          toolkit=str(toolkit_slug).lower(),
                          options={'type': 'use_composio_managed_auth', 'name': name})

    def get_auth_config(self, auth_config_id: str):
        return self._call('auth_configs.get', self._sdk.auth_configs.get, auth_config_id)

    # -- connected accounts ------------------------------------------------
    def initiate_connection(self, composio_user_id: str, auth_config_id: str,
                            callback_url: str, alias: Optional[str] = None,
                            allow_multiple: bool = True) -> Dict:
        """
        Start hosted auth via ``connected_accounts.link()``.

        ``initiate()`` is DEPRECATED for Composio-managed OAuth and returns 400
        as of 2026-07-03 for all orgs. All four priority toolkits are
        Composio-managed OAuth2, so ``link()`` is the only viable call.

        Returns ``{'id', 'status', 'redirect_url', 'expires_at'}``.

        ``expires_at`` is always None with composio 0.19.0: the REST 201 body
        carries it, but the SDK's ``ConnectionRequest`` drops it. Callers fall
        back to ``COMPOSIO_LINK_TTL_SECONDS``.
        """
        if not composio_user_id:
            raise ComposioError('composio_user_id is required')
        if not auth_config_id:
            raise ComposioError('auth_config_id is required')

        req = self._call('connected_accounts.link', self._sdk.connected_accounts.link,
                         user_id=composio_user_id,
                         auth_config_id=auth_config_id,
                         callback_url=callback_url,
                         alias=alias,
                         allow_multiple=allow_multiple)
        return {
            'id': getattr(req, 'id', None) or getattr(req, 'connected_account_id', None),
            'status': getattr(req, 'status', None) or 'INITIALIZING',
            'redirect_url': getattr(req, 'redirect_url', None),
            'expires_at': getattr(req, 'expires_at', None),
        }

    def get_connection(self, connected_account_id: str) -> Dict:
        """
        Retrieve a connected account. Returns a SCRUBBED dict - ``state.val``
        never survives this method.
        """
        acc = self._call('connected_accounts.get', self._sdk.connected_accounts.get,
                         connected_account_id)
        toolkit = getattr(acc, 'toolkit', None)
        state = getattr(acc, 'state', None)
        return {
            'id': getattr(acc, 'id', connected_account_id),
            'status': getattr(acc, 'status', None),
            'toolkit_slug': (getattr(toolkit, 'slug', None) or '').upper() or None,
            'auth_scheme': getattr(state, 'auth_scheme', None),
            'raw': scrub(to_plain(acc) or {}),
        }

    def list_connections(self, composio_user_ids: List[str],
                         auth_config_ids=None, statuses=None):
        """
        List connected accounts for specific entity ids.

        ALWAYS pass ``composio_user_ids``. An unfiltered
        ``connected_accounts.list()`` would span every tenant in the Composio
        project, so this method refuses to make that call at all.
        """
        if not composio_user_ids:
            raise ComposioError('composio_user_ids is required - refusing an unscoped list')
        return self._call('connected_accounts.list', self._sdk.connected_accounts.list,
                          user_ids=list(composio_user_ids),
                          auth_config_ids=auth_config_ids,
                          statuses=statuses)

    def refresh_connection(self, connected_account_id: str) -> Dict:
        """Re-authorise an existing connected account."""
        res = self._call('connected_accounts.refresh', self._sdk.connected_accounts.refresh,
                         connected_account_id)
        return {
            'id': getattr(res, 'id', connected_account_id),
            'redirect_url': getattr(res, 'redirect_url', None),
            'status': getattr(res, 'status', None),
        }

    def enable_connection(self, connected_account_id: str):
        return self._call('connected_accounts.enable', self._sdk.connected_accounts.enable,
                          connected_account_id)

    def disable_connection(self, connected_account_id: str):
        return self._call('connected_accounts.disable', self._sdk.connected_accounts.disable,
                          connected_account_id)

    def delete_connection(self, connected_account_id: str) -> None:
        """
        Permanently delete the connected account at Composio. Irreversible.

        Deliberately NOT retried: a retry after a success would 404 and confuse
        the state machine. A 404 here means "already gone", which is the
        outcome we wanted, so it is swallowed.
        """
        try:
            self._sdk.connected_accounts.delete(connected_account_id)
        except Exception as exc:  # noqa: BLE001
            status = getattr(exc, 'status_code', None) or getattr(
                getattr(exc, 'response', None), 'status_code', None)
            if status == 404:
                logger.info('Composio connected account %s already deleted', connected_account_id)
                return
            logger.error('Composio connected_accounts.delete failed (status=%s): %s',
                         status, exc, exc_info=True)
            raise ComposioError(str(exc), status_code=status) from exc

    # -- tools -------------------------------------------------------------
    def execute_tool(self, slug: str, composio_user_id: str,
                     connected_account_id: str, arguments: Optional[Dict] = None,
                     version: Optional[str] = None):
        """
        Execute a single tool action.

        ``version`` should be pinned per tool slug via
        ``ComposioAuthConfig.default_tool_versions``. We never pass
        ``dangerously_skip_version_check`` - schema drift would break callers
        silently.
        """
        return self._call('tools.execute', self._sdk.tools.execute,
                          slug,
                          user_id=composio_user_id,
                          connected_account_id=connected_account_id,
                          arguments=arguments or {},
                          version=version)


_client: Optional[ComposioClient] = None


def get_composio_client() -> ComposioClient:
    """
    Process-wide singleton, mirroring ``integrations.utils.encryption.get_encryptor()``.

    Raises ``ComposioNotConfigured`` when the integration is switched off, which
    the API layer maps to 424 Failed Dependency.
    """
    global _client
    if _client is None:
        _client = ComposioClient()
    return _client


def reset_composio_client():
    """Drop the cached singleton. Used by tests and by settings reloads."""
    global _client
    _client = None


def composio_is_configured() -> bool:
    """Cheap check for 'can we talk to Composio at all', with no SDK import."""
    return bool(getattr(settings, 'COMPOSIO_ENABLED', False)
                and getattr(settings, 'COMPOSIO_API_KEY', ''))
