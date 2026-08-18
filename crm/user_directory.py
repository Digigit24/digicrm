"""
User directory proxy.

The CRM does not own users -- they live in the SuperAdmin auth service
(admin.celiyo.com) and are referenced inside the CRM by UUID via the
``lead.assigned_to`` / ``lead.owner_user_id`` fields. This module is the single
place that fetches the tenant's users so both the REST endpoint
(``/api/crm/users/``) and the MCP production dispatcher can reuse it.

SECURITY -- tenant scoping is NOT implied by the service JWT.
The upstream call is made with a shared, super-admin service token. SuperAdmin's
``UserViewSet.get_queryset`` returns *every* user on the platform when a
super-admin caller sends no ``x-tenant-id`` header. Therefore every caller MUST
pass the tenant id it wants (for HTTP callers: the tenant claim of the caller's
own verified JWT -- never a query param, body or client-supplied header), and
this module forwards it upstream as ``x-tenant-id``. If no tenant id can be
resolved the fetch fails closed with :class:`TenantScopeRequired` rather than
performing an unscoped fetch.

Upstream contract (SuperAdmin)::

    GET {SUPERADMIN_URL}/api/users/?page=1&page_size=200&search=<q>
    x-tenant-id: <tenant uuid>
    -> {"count": N, "next": null, "previous": null,
        "results": [{"id", "email", "first_name", "last_name", "full_name",
                     "is_active", "avatar"}]}

Older SuperAdmin builds return ``profile_picture`` instead of ``avatar`` and no
``full_name`` at all, so results are normalised defensively here.

Responses are cached per ``(tenant_id, search, page_size)`` in Django's cache for
``settings.USER_DIRECTORY_CACHE_TTL`` seconds (default 300). The tenant id is
always part of the cache key, so one tenant can never be served another's list.
"""
import hashlib
import logging
import os
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500
#: Safety caps so a misbehaving upstream can never make us loop forever.
MAX_PAGES = 10
MAX_USERS = 2000
DEFAULT_CACHE_TTL = 300  # seconds
CACHE_KEY_PREFIX = 'crm:user-directory:v1'
REQUEST_TIMEOUT = 10


class UserDirectoryError(RuntimeError):
    """Base class for user-directory configuration/scoping failures."""


class TenantScopeRequired(UserDirectoryError):
    """No tenant id could be resolved -- refuse to make an unscoped fetch."""


class ServiceCredentialsMissing(UserDirectoryError):
    """No service token configured -- refuse to make an unauthenticated fetch."""


def _service_token() -> str:
    """Admin-issued service JWT (the same token the MCP / auth middleware use)."""
    return (
        getattr(settings, 'MCP_SERVICE_JWT', '')
        or os.environ.get('DIGICRM_JWT_TOKEN', '')
        or os.environ.get('MCP_SERVICE_JWT', '')
    )


def _resolve_tenant_id(tenant_id=None) -> str:
    """
    Resolve the tenant to scope the fetch to, or raise.

    Only two sources are trusted, in order:

    1. the explicit ``tenant_id`` argument (HTTP callers pass
       ``request.tenant_id``, which the JWT middleware sets from the *verified*
       token claim -- never from a header or query param);
    2. ``DIGICRM_TENANT_ID``, the pinned tenant of a single-tenant deployment,
       used by non-HTTP callers such as the MCP dispatcher (which already scopes
       every one of its other tools by that same value).

    Thread-locals are deliberately NOT consulted: they survive between requests
    on a reused worker thread and would be a cross-tenant leak vector.
    """
    resolved = str(tenant_id).strip() if tenant_id else ''
    if resolved:
        return resolved

    fallback = (
        getattr(settings, 'DIGICRM_TENANT_ID', '')
        or os.environ.get('DIGICRM_TENANT_ID', '')
    ).strip()
    if fallback:
        return fallback

    raise TenantScopeRequired(
        'fetch_tenant_users requires a tenant id; refusing to query the auth '
        'service without tenant scoping.'
    )


def _cache_ttl() -> int:
    try:
        return int(getattr(settings, 'USER_DIRECTORY_CACHE_TTL', DEFAULT_CACHE_TTL))
    except (TypeError, ValueError):
        return DEFAULT_CACHE_TTL


def _cache_key(tenant_id: str, search, page_size: int) -> str:
    """Cache key -- ALWAYS tenant-scoped, both literally and inside the digest."""
    digest = hashlib.sha256(
        '|'.join([tenant_id, search or '', str(page_size)]).encode('utf-8')
    ).hexdigest()[:32]
    return '{}:{}:{}'.format(CACHE_KEY_PREFIX, tenant_id, digest)


def _normalize_user(raw) -> dict:
    """Flatten one upstream user into the pinned response shape."""
    if not isinstance(raw, dict):
        return {}

    user_id = raw.get('id')
    user_id = str(user_id) if user_id is not None else ''
    email = (raw.get('email') or '').strip()
    first_name = (raw.get('first_name') or '').strip()
    last_name = (raw.get('last_name') or '').strip()

    # SuperAdmin is being upgraded to always send a non-empty full_name; until
    # every environment runs that build, compute the fallback locally.
    full_name = (raw.get('full_name') or '').strip()
    if not full_name:
        full_name = ' '.join(part for part in (first_name, last_name) if part).strip()
    if not full_name:
        full_name = email or user_id

    avatar = raw.get('avatar') or raw.get('profile_picture')

    return {
        'id': user_id,
        'email': email,
        'first_name': first_name,
        'last_name': last_name,
        'full_name': full_name,
        'is_active': bool(raw.get('is_active', True)),
        'avatar': avatar or None,
    }


def _same_host(candidate: str, base: str) -> bool:
    """Only follow ``next`` links that stay on the configured auth service."""
    try:
        return urlparse(candidate).netloc == urlparse(base).netloc
    except ValueError:
        return False


def fetch_tenant_users(
    search: str = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    tenant_id=None,
    use_cache: bool = True,
) -> dict:
    """
    Return the users of a single tenant from admin.celiyo.com.

    Args:
        search: optional name/email filter (passed through to the auth service).
        page_size: upstream page size (clamped to 1..500). All pages are then
            walked via the ``next`` link up to ``MAX_PAGES``/``MAX_USERS``, so a
            tenant with 300 users is returned in full rather than truncated.
        tenant_id: the tenant to scope to. HTTP callers MUST pass the caller's
            own ``request.tenant_id`` (verified JWT claim). Omitting it is only
            valid for single-tenant, non-HTTP callers that pin
            ``DIGICRM_TENANT_ID``; otherwise :class:`TenantScopeRequired`.
        use_cache: read/write the short-lived per-tenant cache (default True).

    Returns:
        ``{"count": <int>, "results": [{id, email, first_name, last_name,
        full_name, is_active, avatar}, ...]}`` -- normalised, so callers never
        have to cope with upstream field drift. ``full_name`` is always
        non-empty. ``count`` is the number of users actually returned.

    Raises:
        TenantScopeRequired: no tenant id available (fail closed, never unscoped).
        ServiceCredentialsMissing: no service token configured.
        requests.HTTPError / requests.RequestException: upstream failure.
    """
    tenant = _resolve_tenant_id(tenant_id)

    try:
        page_size = int(page_size)
    except (TypeError, ValueError):
        page_size = DEFAULT_PAGE_SIZE
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))

    search = (search or '').strip() or None

    cache_key = _cache_key(tenant, search, page_size)
    if use_cache:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    token = _service_token()
    if not token:
        # Never fall through to an unauthenticated request: depending on the
        # upstream configuration that could either 401 or -- worse -- return
        # an unscoped list.
        raise ServiceCredentialsMissing(
            'No auth-service credentials configured (MCP_SERVICE_JWT / '
            'DIGICRM_JWT_TOKEN); refusing to issue an unauthenticated request.'
        )

    base = getattr(settings, 'SUPERADMIN_URL', 'https://admin.celiyo.com').rstrip('/')
    url = '{}/api/users/'.format(base)
    params = {'page': 1, 'page_size': page_size}
    if search:
        params['search'] = search

    headers = {
        'Authorization': 'Bearer {}'.format(token),
        'Accept': 'application/json',
        # THE tenant boundary. Without this SuperAdmin hands our super-admin
        # service token every user on the platform.
        'x-tenant-id': tenant,
    }

    users = []
    seen_ids = set()
    pages = 0

    while url and pages < MAX_PAGES and len(users) < MAX_USERS:
        resp = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
        pages += 1

        if isinstance(payload, list):
            page_results, next_url = payload, None
        elif isinstance(payload, dict):
            page_results = payload.get('results') or []
            next_url = payload.get('next')
        else:
            page_results, next_url = [], None

        for raw in page_results:
            user = _normalize_user(raw)
            if not user:
                continue
            if user['id'] and user['id'] in seen_ids:
                continue
            if user['id']:
                seen_ids.add(user['id'])
            users.append(user)
            if len(users) >= MAX_USERS:
                break

        # ``next`` is an absolute upstream URL that already carries
        # page/page_size/search, so params must not be re-applied to it.
        params = None
        if next_url and _same_host(next_url, base):
            url = next_url
        else:
            if next_url:
                logger.warning(
                    'User directory: refusing to follow off-host next link (%s)',
                    urlparse(next_url).netloc,
                )
            url = None

    if url or len(users) >= MAX_USERS:
        logger.warning(
            'User directory truncated for tenant %s at %s users / %s pages',
            tenant, len(users), pages,
        )

    data = {'count': len(users), 'results': users}

    if use_cache:
        cache.set(cache_key, data, _cache_ttl())

    return data
