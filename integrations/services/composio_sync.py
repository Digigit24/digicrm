"""
Composio reconciliation helpers.

Shared by ``integrations/views_composio.py`` and ``integrations/tasks.py`` so
that status-mirroring logic lives in exactly one place instead of being
duplicated between the HTTP layer and the Celery layer.

Nothing here imports the ``composio`` SDK directly - everything goes through
``integrations.services.composio_client.ComposioClient``.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from integrations.models import (
    ComposioAuthConfig,
    ComposioConnection,
    ComposioConnectionScopeEnum,
    ComposioConnectionStatusEnum,
    ComposioEventTypeEnum,
    ComposioToolkit,
)
from integrations.services.composio_client import (
    ComposioError,
    ComposioNotFound,
    assert_connection_identity,
    build_composio_user_id,
    get_composio_client,
    scrub,
    to_plain,
)

logger = logging.getLogger(__name__)


# Composio connected-account status -> our local mirror.
# "INITIATED" is what composio 0.19.0's ConnectionRequest reports right after
# link(); the REST API calls the same state INITIALIZING.
_STATUS_MAP = {
    'INITIATED': ComposioConnectionStatusEnum.INITIALIZING,
    'INITIALIZING': ComposioConnectionStatusEnum.INITIALIZING,
    'PENDING': ComposioConnectionStatusEnum.INITIALIZING,
    'ACTIVE': ComposioConnectionStatusEnum.ACTIVE,
    'INACTIVE': ComposioConnectionStatusEnum.INACTIVE,
    'DISABLED': ComposioConnectionStatusEnum.INACTIVE,
    'FAILED': ComposioConnectionStatusEnum.FAILED,
    'ERROR': ComposioConnectionStatusEnum.FAILED,
    'EXPIRED': ComposioConnectionStatusEnum.EXPIRED,
    'REVOKED': ComposioConnectionStatusEnum.REVOKED,
    'DELETED': ComposioConnectionStatusEnum.DELETED,
}


def map_status(composio_status):
    """Translate a Composio status string into ComposioConnectionStatusEnum."""
    if not composio_status:
        return None
    return _STATUS_MAP.get(str(composio_status).upper())


def _first(payload, *paths):
    """
    Pull the first present value out of a nested dict by dotted paths.

    Composio moves fields between response shapes across versions, so every
    read of an optional field goes through here rather than assuming one shape.
    """
    if not isinstance(payload, dict):
        return None
    for path in paths:
        node = payload
        for part in path.split('.'):
            if not isinstance(node, dict) or part not in node:
                node = None
                break
            node = node[part]
        if node not in (None, '', [], {}):
            return node
    return None


def _parse_dt(value):
    if not value:
        return None
    if hasattr(value, 'tzinfo'):
        return value
    parsed = parse_datetime(str(value))
    if parsed and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.utc)
    return parsed


# ---------------------------------------------------------------------------
# Connection status
# ---------------------------------------------------------------------------

def sync_connection_status(connection, force=False, actor_user_id=None):
    """
    Mirror one connection's status from Composio into our row.

    * Short-circuits (no network call) when ``last_status_check_at`` is inside
      ``COMPOSIO_STATUS_MIN_INTERVAL`` and ``force`` is False. This is what
      keeps the frontend's 2-second poll from hammering Composio.
    * Re-derives and asserts ``composio_user_id`` before the call, so a
      tampered row can never address another tenant's entity.
    * Persists only scrubbed metadata - ``state.val`` never lands in the DB.
    * Writes a STATUS_SYNC audit event only when the status actually changed.

    Returns the (possibly updated) connection. Never raises for a missing
    account: a 404 at Composio means the account is gone, so we mark REVOKED.
    """
    if not connection.connected_account_id:
        return connection

    min_interval = int(getattr(settings, 'COMPOSIO_STATUS_MIN_INTERVAL', 10))
    if (not force
            and connection.last_status_check_at
            and timezone.now() - connection.last_status_check_at < timedelta(seconds=min_interval)):
        return connection

    # Defence in depth: layer 3 of the tenant assertion.
    assert_connection_identity(connection)

    previous_status = connection.status
    client = get_composio_client()

    try:
        payload = client.get_connection(connection.connected_account_id)
    except ComposioNotFound:
        logger.warning('Composio connected account %s no longer exists; marking REVOKED',
                       connection.connected_account_id)
        connection.status = ComposioConnectionStatusEnum.REVOKED
        connection.last_status_check_at = timezone.now()
        connection.last_error = 'Connected account no longer exists at Composio'
        connection.last_error_at = timezone.now()
        connection.save(update_fields=['status', 'last_status_check_at',
                                       'last_error', 'last_error_at', 'updated_at'])
        connection.record_event(
            ComposioEventTypeEnum.STATUS_SYNC,
            actor_user_id=actor_user_id,
            message=f'{previous_status} -> REVOKED (not found at Composio)',
        )
        return connection

    raw = payload.get('raw') or {}
    mapped = map_status(payload.get('status'))

    update_fields = ['last_status_check_at', 'updated_at']
    connection.last_status_check_at = timezone.now()

    if mapped and mapped != connection.status:
        connection.status = mapped
        update_fields.append('status')
        if mapped == ComposioConnectionStatusEnum.ACTIVE and connection.connected_at is None:
            connection.connected_at = timezone.now()
            update_fields.append('connected_at')

    toolkit_slug = payload.get('toolkit_slug')
    if toolkit_slug and toolkit_slug != connection.toolkit_slug:
        connection.toolkit_slug = toolkit_slug
        update_fields.append('toolkit_slug')

    label = _first(raw, 'account_label', 'meta.account_label', 'state.account_id',
                   'account_identifier', 'meta.identifier')
    if label and isinstance(label, str) and label != connection.account_label:
        connection.account_label = label[:320]
        update_fields.append('account_label')

    scopes = _first(raw, 'granted_scopes', 'state.scopes', 'meta.scopes')
    if scopes and scopes != connection.granted_scopes:
        connection.granted_scopes = scopes
        update_fields.append('granted_scopes')

    expires = _parse_dt(_first(raw, 'expires_at', 'state.expires_at', 'meta.expires_at'))
    if expires and expires != connection.expires_at:
        connection.expires_at = expires
        update_fields.append('expires_at')

    # `raw` is already scrubbed by ComposioClient.get_connection; scrub again
    # so this function is safe no matter who calls it.
    connection.metadata = scrub(raw)
    update_fields.append('metadata')

    if connection.status == ComposioConnectionStatusEnum.ACTIVE:
        if connection.last_error or connection.last_error_at:
            connection.last_error = None
            connection.last_error_at = None
            update_fields.extend(['last_error', 'last_error_at'])

    connection.save(update_fields=list(dict.fromkeys(update_fields)))

    if connection.status != previous_status:
        connection.record_event(
            ComposioEventTypeEnum.STATUS_SYNC,
            actor_user_id=actor_user_id,
            message=f'{previous_status} -> {connection.status}',
            payload={'from': previous_status, 'to': connection.status},
        )

    return connection


# ---------------------------------------------------------------------------
# Toolkit catalogue
# ---------------------------------------------------------------------------

_PRESERVED_TOOLKIT_FIELDS = ('is_enabled', 'is_featured', 'sort_order')


def sync_toolkit_catalogue(max_pages=25):
    """
    Refresh the ``ComposioToolkit`` cache from Composio.

    Pages through ``toolkits.list()`` with the cursor, upserting by slug. The
    operator-owned fields (``is_enabled``, ``is_featured``, ``sort_order``) are
    NEVER clobbered - a nightly sync must not silently re-expose a toolkit an
    operator disabled.

    Returns the number of rows created or updated.
    """
    client = get_composio_client()
    touched = 0
    cursor = None

    for _ in range(max_pages):
        page = client.list_toolkits(cursor=cursor, limit=200, managed_by='all')
        data = to_plain(page) or {}
        items = data.get('items') or data.get('results') or data.get('data') or []

        for item in items:
            item = to_plain(item) if not isinstance(item, dict) else item
            if not isinstance(item, dict):
                continue
            slug = (item.get('slug') or '').upper()
            if not slug:
                continue
            meta = item.get('meta') or {}
            defaults = {
                'name': item.get('name') or slug.title(),
                'description': meta.get('description') or item.get('description'),
                'logo_url': (meta.get('logo') or item.get('logo') or '')[:500] or None,
                'categories': meta.get('categories') or item.get('categories'),
                'auth_schemes': item.get('auth_schemes'),
                'composio_managed_auth_schemes': item.get('composio_managed_auth_schemes'),
                'no_auth': bool(item.get('no_auth', False)),
                'tools_count': int(meta.get('tools_count') or item.get('tools_count') or 0),
                'triggers_count': int(meta.get('triggers_count') or item.get('triggers_count') or 0),
                'metadata': scrub(item),
                'last_synced_at': timezone.now(),
            }
            ComposioToolkit.objects.update_or_create(slug=slug, defaults=defaults)
            touched += 1

        cursor = data.get('next_cursor') or data.get('nextCursor')
        if not cursor:
            break

    logger.info('Composio toolkit catalogue sync touched %s rows', touched)
    return touched


# ---------------------------------------------------------------------------
# Auth configs
# ---------------------------------------------------------------------------

def ensure_auth_config(toolkit_slug, tenant_id=None, name=None):
    """
    Return the ``ComposioAuthConfig`` for a toolkit, creating the platform-wide
    Composio-managed one on demand.

    Idempotent: an existing row (tenant-specific first, then global) wins and
    no Composio call is made. Creation only happens for ``tenant_id=None``,
    i.e. the platform default - a tenant BYO-OAuth config is always created
    explicitly through the admin API, never implicitly.

    Raises ``ComposioError`` when the toolkit does not support Composio-managed
    auth, because then we would need credentials we do not have.
    """
    slug = str(toolkit_slug).upper()

    existing = ComposioAuthConfig.resolve(slug, tenant_id)
    if existing:
        return existing

    if tenant_id is not None:
        raise ComposioError(
            f'No auth config for {slug}; a tenant-specific config must be created explicitly'
        )

    toolkit = ComposioToolkit.objects.filter(slug=slug).first()
    if toolkit and not toolkit.supports_managed_auth and not toolkit.no_auth:
        raise ComposioError(
            f'{slug} does not support Composio-managed auth; a BYO-OAuth auth config is required'
        )

    client = get_composio_client()
    created = client.create_managed_auth_config(slug, name or (toolkit.name if toolkit else slug))
    payload = to_plain(created) or {}
    auth_config_id = payload.get('id') or getattr(created, 'id', None)
    if not auth_config_id:
        raise ComposioError(f'Composio did not return an auth config id for {slug}')

    config, _ = ComposioAuthConfig.objects.update_or_create(
        auth_config_id=auth_config_id,
        defaults={
            'tenant_id': None,
            'toolkit_slug': slug,
            'auth_scheme': (payload.get('auth_scheme') or 'OAUTH2'),
            'is_composio_managed': True,
            'name': name or (toolkit.name if toolkit else slug),
            'is_active': True,
            'metadata': scrub(payload),
            'last_synced_at': timezone.now(),
        },
    )
    logger.info('Created Composio auth config %s for %s', auth_config_id, slug)
    return config


# ---------------------------------------------------------------------------
# Drift detection
# ---------------------------------------------------------------------------

def reconcile_tenant_connections(tenant_id):
    """
    Compare our rows against Composio for one tenant and report drift.

    Only ever lists by the tenant's own entity ids - there is no code path here
    that can issue an unscoped ``connected_accounts.list()``.

    Returns ``{'checked', 'local_only', 'remote_only', 'status_drift'}`` where
    the list values are identifiers, not payloads.
    """
    local = list(
        ComposioConnection.objects
        .filter(tenant_id=tenant_id)
        .exclude(status=ComposioConnectionStatusEnum.DELETED)
    )
    entity_ids = sorted({c.composio_user_id for c in local if c.composio_user_id})
    result = {'checked': len(local), 'local_only': [], 'remote_only': [], 'status_drift': []}
    if not entity_ids:
        return result

    client = get_composio_client()
    remote_items = []
    try:
        page = client.list_connections(composio_user_ids=entity_ids)
        data = to_plain(page) or {}
        remote_items = data.get('items') or data.get('results') or data.get('data') or []
    except ComposioError as exc:
        logger.error('Composio reconcile failed for tenant %s: %s', tenant_id, exc)
        result['error'] = str(exc)
        return result

    remote_by_id = {}
    for item in remote_items:
        item = to_plain(item) if not isinstance(item, dict) else item
        if isinstance(item, dict) and item.get('id'):
            remote_by_id[item['id']] = item

    for conn in local:
        if not conn.connected_account_id:
            continue
        remote = remote_by_id.pop(conn.connected_account_id, None)
        if remote is None:
            result['local_only'].append(str(conn.public_id))
            continue
        mapped = map_status(remote.get('status'))
        if mapped and mapped != conn.status:
            result['status_drift'].append({
                'connection': str(conn.public_id),
                'local': conn.status,
                'remote': mapped,
            })

    result['remote_only'] = sorted(remote_by_id.keys())
    return result


# ---------------------------------------------------------------------------
# Entity id helper for connection creation
# ---------------------------------------------------------------------------

def entity_id_for(tenant_id, user_id, scope):
    """
    Entity id for a new connection, honouring its scope.

    TENANT-scoped connections all share one Composio entity per tenant so the
    whole tenant reuses a single connected account; USER-scoped connections get
    their own.
    """
    if scope == ComposioConnectionScopeEnum.TENANT:
        return build_composio_user_id(tenant_id, None)
    return build_composio_user_id(tenant_id, user_id)
