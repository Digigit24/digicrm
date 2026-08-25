"""
The tenant's WhatsApp vendor credential, fetched from SuperAdmin.

WHY THIS EXISTS
---------------
The credential a tenant admin saves in Admin Settings lands in SuperAdmin's
``Tenant.settings`` as ``whatsapp_vendor_uid`` / ``whatsapp_api_token``. Until
now nothing in DigiCRM read it: ``_adapter_from_request`` used a single GLOBAL
``WA_VENDOR_UID`` / ``WA_API_TOKEN`` pair from ``.env`` for every tenant. So the
only self-serve place to configure WhatsApp had no effect, and every tenant
shared one vendor account regardless of what they had saved.

This module is the missing link. It mirrors ``crm/user_directory.py`` — the
existing SuperAdmin server-to-server client — deliberately and closely: same
service JWT, same short-TTL per-tenant cache, same refusal to follow redirects,
same fail-closed posture. A second, subtly different way of calling the same
service would be a second thing to get wrong.

Upstream contract::

    GET {SUPERADMIN_URL}/api/tenants/{tenant_id}/whatsapp-credentials/
    Authorization: Bearer <service JWT>
    -> {"tenant_id", "vendor_uid", "api_token", "base_url", "configured"}

That endpoint exists precisely so this call does not have to use the tenant
DETAIL endpoint, whose serializer also returns ``database_url``. Fetching a
WhatsApp token should not hand this service a database URL.

SECRET HANDLING
---------------
The token is a credential. It is never logged, never included in an exception
message, and never put in a cache key — only in the cache VALUE, which lives in
the same cache the rest of the app already trusts. Every log line here reports
booleans (``has_token``) exactly as ``_adapter_from_request`` already does.

FAIL CLOSED, BUT NOT LOUDLY
---------------------------
A tenant with nothing saved is the normal case during rollout, not an error:
the caller falls back to the global env credential. So "not configured" returns
None rather than raising. Only a genuine transport/config fault raises, and even
that is caught by the caller — a SuperAdmin blip must not take WhatsApp down
when a working env fallback exists.
"""
import hashlib
import logging
import os
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

#: Short. A rotated token must take effect without a deploy, and this is on the
#: hot path for every WhatsApp read.
DEFAULT_CACHE_TTL = 300
#: A transport failure is remembered too, briefly. Without this, a SuperAdmin
#: outage costs EVERY WhatsApp request a full REQUEST_TIMEOUT before it falls
#: back to env — turning a dependency being slow into this service being slow.
#: 30s is short enough that recovery is nearly immediate and long enough that a
#: burst of requests makes one attempt between them.
FAILURE_CACHE_TTL = 30
CACHE_KEY_PREFIX = 'wa:tenant-credentials:v1'
#: Deliberately short: this call sits on a hot read path and has a working
#: fallback, so waiting is worse than falling back.
REQUEST_TIMEOUT = 5

#: Cached markers, distinct from a cache miss (None) and from a real credential.
_NOT_CONFIGURED = False
_LOOKUP_FAILED = 'error'


class TenantCredentialsError(RuntimeError):
    """The lookup could not be performed (transport or configuration fault)."""


def _service_token() -> str:
    """The same admin-issued service JWT the user directory uses."""
    return (
        getattr(settings, 'MCP_SERVICE_JWT', '')
        or os.environ.get('DIGICRM_JWT_TOKEN', '')
        or os.environ.get('MCP_SERVICE_JWT', '')
    )


def _cache_ttl() -> int:
    try:
        return int(getattr(settings, 'WA_TENANT_CREDENTIALS_CACHE_TTL', DEFAULT_CACHE_TTL))
    except (TypeError, ValueError):
        return DEFAULT_CACHE_TTL


def _cache_key(tenant_id: str) -> str:
    """Tenant-scoped both literally and in the digest. Never contains the token."""
    digest = hashlib.sha256(tenant_id.encode('utf-8')).hexdigest()[:32]
    return f'{CACHE_KEY_PREFIX}:{tenant_id}:{digest}'


def _base_url() -> str:
    return str(getattr(settings, 'SUPERADMIN_URL', '') or '').rstrip('/')


def fetch_tenant_whatsapp_credentials(tenant_id, use_cache: bool = True):
    """
    Return ``{'vendor_uid', 'api_token', 'base_url'}`` for a tenant, or None.

    None means "this tenant has not saved a credential" — a normal state the
    caller handles by falling back. It does NOT mean the lookup failed; that
    raises :class:`TenantCredentialsError`.
    """
    tenant = str(tenant_id or '').strip()
    if not tenant:
        # No tenant, no lookup. Never fetch unscoped.
        return None

    key = _cache_key(tenant)
    if use_cache:
        cached = cache.get(key)
        # `==`, NOT `is`: the marker comes back from the cache as a different
        # string object, so an identity check silently misses and the sentinel
        # escapes to the caller AS the credential. It did exactly that once.
        if isinstance(cached, str) and cached == _LOOKUP_FAILED:
            # Remembered failure: raise immediately rather than making the
            # caller wait out another timeout it is only going to fall back from.
            raise TenantCredentialsError(
                'SuperAdmin credential lookup failed recently; not retrying yet.'
            )
        if cached is not None:
            # Anything that is not a credential dict means "nothing usable
            # stored". Typed rather than trusted, so no future marker can ever
            # be returned as if it were a credential.
            return cached if isinstance(cached, dict) else None

    token = _service_token()
    if not token:
        raise TenantCredentialsError(
            'No service token configured (MCP_SERVICE_JWT); refusing to call '
            'SuperAdmin unauthenticated.'
        )

    base = _base_url()
    if not base:
        raise TenantCredentialsError('SUPERADMIN_URL is not configured.')

    url = f'{base}/api/tenants/{tenant}/whatsapp-credentials/'
    try:
        resp = requests.get(
            url,
            headers={
                'Authorization': f'Bearer {token}',
                'Accept': 'application/json',
            },
            timeout=REQUEST_TIMEOUT,
            # Never followed: a 3xx would replay the privileged service JWT at
            # whatever host it points to. Same rule as the user directory.
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        cache.set(key, _LOOKUP_FAILED, FAILURE_CACHE_TTL)
        raise TenantCredentialsError(f'Could not reach SuperAdmin: {exc}') from exc

    if 300 <= resp.status_code < 400:
        location = resp.headers.get('Location', '')
        logger.error(
            '[WA Credentials] SuperAdmin returned an unexpected %s redirect to %s; '
            'refusing to follow it with the service credential.',
            resp.status_code, urlparse(location).netloc or '<unknown host>',
        )
        raise TenantCredentialsError(
            f'SuperAdmin returned an unexpected {resp.status_code} redirect'
        )

    if resp.status_code == 404:
        # Either an unknown tenant or a SuperAdmin that predates the endpoint.
        # Both mean "nothing to use from here"; the caller falls back.
        logger.info('[WA Credentials] SuperAdmin has no such tenant/endpoint. tenant=%s', tenant)
        cache.set(key, _NOT_CONFIGURED, _cache_ttl())
        return None

    if resp.status_code >= 400:
        cache.set(key, _LOOKUP_FAILED, FAILURE_CACHE_TTL)
        raise TenantCredentialsError(
            f'SuperAdmin returned HTTP {resp.status_code} for the tenant credential lookup'
        )

    try:
        data = resp.json()
    except ValueError as exc:
        raise TenantCredentialsError('SuperAdmin returned a non-JSON credential response') from exc

    if not isinstance(data, dict):
        raise TenantCredentialsError('SuperAdmin returned an unexpected credential shape')

    vendor_uid = (data.get('vendor_uid') or '').strip()
    api_token = (data.get('api_token') or '').strip()

    if not (vendor_uid and api_token):
        logger.info(
            '[WA Credentials] tenant has no stored WhatsApp credential. '
            'tenant=%s has_vendor=%s has_token=%s',
            tenant, bool(vendor_uid), bool(api_token),
        )
        cache.set(key, _NOT_CONFIGURED, _cache_ttl())
        return None

    credentials = {
        'vendor_uid': vendor_uid,
        'api_token': api_token,
        'base_url': (data.get('base_url') or '').strip() or None,
    }
    cache.set(key, credentials, _cache_ttl())
    logger.info(
        '[WA Credentials] using the tenant-stored credential. tenant=%s vendor=%s has_token=%s',
        tenant, vendor_uid, True,
    )
    return credentials


def invalidate_tenant_whatsapp_credentials(tenant_id) -> None:
    """Drop the cached credential — call after a tenant updates it."""
    tenant = str(tenant_id or '').strip()
    if tenant:
        cache.delete(_cache_key(tenant))
