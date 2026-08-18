"""
API views for the Composio integration.

Composio brokers OAuth for hundreds of third-party toolkits (Notion, Gmail,
Google Drive, Google Calendar, ...). Unlike the native Google Sheets flow in
views.py, we never hold the end user's tokens - Composio does. What we persist
is the set of identifiers needed to address those tokens, scoped hard to
(tenant, user).

Kept in a separate module from views.py (1429 lines) purely for reviewability;
it is wired into the same router in integrations/urls.py.

Isolation, in four independent layers (a bug in any one is not enough to leak):

 1. JWTAuthenticationMiddleware supplies tenant_id / user_id. They are the ONLY
    accepted source of identity - never a body, query param or header.
 2. Every queryset starts filter(tenant_id=...) and returns .none() without it.
    Connection querysets additionally filter Q(user_id=...) | Q(scope=TENANT),
    which is STRICTER than the legacy ConnectionViewSet.
 3. assert_connection_identity() re-derives the Composio entity id from the row
    before every outbound call and refuses to proceed on a mismatch.
 4. ComposioClient.list_connections() refuses an unscoped list.

Objects are addressed by public_id (UUID), never the sequential id.
"""

import logging
import secrets
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.db import models, transaction
from django.shortcuts import redirect
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from common.authentication import JWTRequestAuthentication
from common.permissions import HasDigiPermission, is_admin_request
from integrations.models import (
    ComposioAuthConfig,
    ComposioConnection,
    ComposioConnectionEvent,
    ComposioConnectionScopeEnum,
    ComposioConnectionStatusEnum,
    ComposioEventTypeEnum,
    ComposioLinkState,
    ComposioToolkit,
)
from integrations.serializers_composio import (
    ComposioAdminConnectionSerializer,
    ComposioAuthConfigCreateSerializer,
    ComposioAuthConfigSerializer,
    ComposioConnectionDetailSerializer,
    ComposioConnectionEventSerializer,
    ComposioConnectionListSerializer,
    ComposioExecuteSerializer,
    ComposioInitiateSerializer,
    ComposioRefreshSerializer,
    ComposioRevokeSerializer,
    ComposioToolkitDetailSerializer,
    ComposioToolkitSerializer,
)
from integrations.services.composio_client import (
    ComposioError,
    ComposioIdentityMismatch,
    ComposioNotConfigured,
    ComposioNotFound,
    ComposioRateLimited,
    ComposioWebhookVerificationError,
    build_composio_user_id,
    get_composio_client,
    scrub,
    verify_webhook_signature,
)
from integrations.services.composio_sync import (
    ensure_auth_config,
    entity_id_for,
    sync_connection_status,
    sync_toolkit_catalogue,
)

logger = logging.getLogger(__name__)

HTTP_424_FAILED_DEPENDENCY = 424


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _client_ip(request):
    """Best-effort client IP for the audit trail."""
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[0].strip()[:45]
    return request.META.get('REMOTE_ADDR')


def _safe_return_to(value):
    """
    Validate a caller-supplied ``return_to``.

    Must be a relative path (starts with a single '/'), and must prefix-match
    ``COMPOSIO_RETURN_TO_ALLOWLIST``. Anything else - absolute URLs,
    protocol-relative '//evil.com', backslash tricks - falls back to
    '/integrations'. This is the open-redirect guard.
    """
    default = '/integrations'
    if not value or not isinstance(value, str):
        return default
    candidate = value.strip()
    if not candidate.startswith('/') or candidate.startswith('//') or candidate.startswith('/\\'):
        return default
    if '\\' in candidate or '://' in candidate:
        return default
    allowlist = getattr(settings, 'COMPOSIO_RETURN_TO_ALLOWLIST', [default])
    for allowed in allowlist:
        if candidate == allowed or candidate.startswith(allowed + '/') or candidate.startswith(allowed + '?'):
            return candidate
    return default


def composio_error_response(exc):
    """
    Translate a service-layer exception into the HTTP response the plan
    specifies, without ever echoing a Composio error body to the client.
    """
    if isinstance(exc, ComposioNotConfigured):
        # 424 is the agreed "integration not set up" signal; the frontend
        # renders a setup panel for it instead of toasting an error.
        return Response(
            {'error': 'Composio is not configured for this deployment.',
             'code': 'composio_not_configured'},
            status=HTTP_424_FAILED_DEPENDENCY,
        )
    if isinstance(exc, ComposioIdentityMismatch):
        return Response({'error': 'Connection does not belong to this tenant.'},
                        status=status.HTTP_403_FORBIDDEN)
    if isinstance(exc, ComposioRateLimited):
        response = Response({'error': 'Composio is rate limiting this deployment. Try again shortly.',
                             'code': 'composio_rate_limited'},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        response['Retry-After'] = '30'
        return response
    if isinstance(exc, ComposioNotFound):
        return Response({'error': 'The requested Composio resource no longer exists.',
                         'code': 'composio_not_found'},
                        status=status.HTTP_404_NOT_FOUND)
    status_code = getattr(exc, 'status_code', None)
    if status_code == 400:
        logger.error('Composio rejected the request (400): %s', exc)
        return Response({'error': 'Composio rejected the request. Check the auth config for this toolkit.',
                         'code': 'composio_bad_request'},
                        status=status.HTTP_400_BAD_REQUEST)
    logger.error('Composio call failed: %s', exc)
    return Response({'error': 'Composio is unavailable right now. Please try again.',
                     'code': 'composio_unavailable'},
                    status=status.HTTP_502_BAD_GATEWAY)


class _ComposioViewMixin:
    """Shared identity plumbing for every Composio viewset."""

    authentication_classes = [JWTRequestAuthentication]
    permission_classes = [HasDigiPermission]
    permission_module = 'integrations'

    @property
    def tenant_id(self):
        return getattr(self.request, 'tenant_id', None)

    @property
    def user_id(self):
        return getattr(self.request, 'user_id', None)

    def require_admin(self):
        if not is_admin_request(self.request):
            raise PermissionDenied('Tenant administrator rights are required for this action.')


# ---------------------------------------------------------------------------
# 1-3. Toolkit catalogue
# ---------------------------------------------------------------------------

class ComposioToolkitViewSet(_ComposioViewMixin, viewsets.ReadOnlyModelViewSet):
    """
    Browse the catalogue of third-party apps a tenant can connect through Composio.

    Use this endpoint to discover connectable toolkits (Gmail, Notion, Google
    Drive, Google Calendar, ...), see whether the calling user already has a
    live connection for each, and filter to only the ones they have connected.

    The catalogue is a locally cached mirror of Composio's, refreshed nightly.
    Only toolkits an operator has opted in (``is_enabled``) are visible.
    """
    permission_resource = 'providers'
    lookup_field = 'slug'
    lookup_value_regex = '[A-Za-z0-9_-]+'
    serializer_class = ComposioToolkitSerializer
    action_permission_map = {'sync': 'view'}
    search_fields = ['slug', 'name', 'description']
    ordering_fields = ['sort_order', 'name', 'tools_count']

    def get_queryset(self):
        if not self.tenant_id:
            logger.warning('ComposioToolkitViewSet.get_queryset called without tenant_id')
            return ComposioToolkit.objects.none()

        queryset = ComposioToolkit.objects.filter(is_enabled=True)

        search = self.request.query_params.get('search')
        if search:
            # Local search: composio==0.19.0's toolkits.list() has no search
            # parameter, and hitting Composio per keystroke would burn quota.
            queryset = queryset.filter(
                models.Q(slug__icontains=search)
                | models.Q(name__icontains=search)
                | models.Q(description__icontains=search)
            )

        category = self.request.query_params.get('category')
        if category:
            queryset = queryset.filter(categories__icontains=category)

        connected = self.request.query_params.get('connected')
        if connected in ('true', 'false'):
            slugs = set(self._live_connections().values_list('toolkit_slug', flat=True))
            queryset = queryset.filter(slug__in=slugs) if connected == 'true' \
                else queryset.exclude(slug__in=slugs)

        return queryset

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return ComposioToolkitDetailSerializer
        return ComposioToolkitSerializer

    def _live_connections(self):
        """The caller's own non-terminal connections, plus tenant-shared ones."""
        return ComposioConnection.objects.filter(
            tenant_id=self.tenant_id,
        ).filter(
            models.Q(user_id=self.user_id) | models.Q(scope=ComposioConnectionScopeEnum.TENANT)
        ).exclude(
            status__in=ComposioConnectionStatusEnum.terminal()
        ).order_by('-connected_at', '-created_at')

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if not self.tenant_id or not self.user_id:
            context['connectable_slugs'] = set()
            context['connections_by_toolkit'] = {}
            return context

        by_toolkit = {}
        for conn in self._live_connections():
            by_toolkit.setdefault(conn.toolkit_slug, []).append(conn)
        context['connections_by_toolkit'] = by_toolkit

        configs = ComposioAuthConfig.objects.filter(is_active=True).filter(
            models.Q(tenant_id=self.tenant_id) | models.Q(tenant_id__isnull=True)
        )
        context['connectable_slugs'] = set(configs.values_list('toolkit_slug', flat=True))
        return context

    @action(detail=False, methods=['post'])
    def sync(self, request):
        """
        Refresh the toolkit catalogue from Composio. Tenant admins only.

        POST /api/integrations/composio/toolkits/sync/
        """
        self.require_admin()
        try:
            synced = sync_toolkit_catalogue()
        except (ComposioNotConfigured, ComposioError) as exc:
            return composio_error_response(exc)
        return Response({'synced': synced, 'message': f'Synced {synced} toolkits from Composio.'})


# ---------------------------------------------------------------------------
# 4-14. Connections
# ---------------------------------------------------------------------------

class ComposioConnectionViewSet(_ComposioViewMixin, viewsets.ModelViewSet):
    """
    Manage a user's connections to third-party tools brokered by Composio.

    Use this endpoint to start a hosted-auth flow for a toolkit (Notion, Gmail,
    Google Drive, Google Calendar, ...), poll whether the user finished
    authorising, list the caller's own connections, re-authorise, enable,
    disable and disconnect.

    Connections are scoped to (tenant, user). No credentials are stored or
    returned by this API - Composio holds them.
    """
    permission_resource = 'connections'
    lookup_field = 'public_id'
    serializer_class = ComposioConnectionListSerializer
    action_permission_map = {
        'initiate': 'create',
        'status': 'view',
        'events': 'view',
        'refresh': 'edit',
        'enable': 'edit',
        'disable': 'edit',
        'disconnect': 'delete',
        'execute': 'edit',
    }
    http_method_names = ['get', 'post', 'delete', 'head', 'options']
    throttle_classes = [ScopedRateThrottle]

    def get_throttles(self):
        scopes = {'initiate': 'composio-initiate', 'status': 'composio-status',
                  'execute': 'composio-execute'}
        self.throttle_scope = scopes.get(getattr(self, 'action', None))
        if not self.throttle_scope:
            return []
        return super().get_throttles()

    def get_queryset(self):
        """
        Own connections only, plus TENANT-scoped ones shared with the caller.

        This is deliberately STRICTER than the legacy ConnectionViewSet: a
        Composio connection is a live token into someone's personal Gmail or
        Notion, so tenant-wide visibility is not acceptable. Tenant-wide
        oversight lives on ComposioAdminConnectionViewSet, behind
        is_admin_request.
        """
        if not self.tenant_id or not self.user_id:
            logger.warning('ComposioConnectionViewSet.get_queryset called without tenant/user')
            return ComposioConnection.objects.none()

        queryset = ComposioConnection.objects.filter(
            tenant_id=self.tenant_id
        ).select_related('auth_config').filter(
            models.Q(user_id=self.user_id) | models.Q(scope=ComposioConnectionScopeEnum.TENANT)
        )

        if self.request.query_params.get('include_history') != 'true':
            queryset = queryset.exclude(status=ComposioConnectionStatusEnum.DELETED)

        toolkit_slug = self.request.query_params.get('toolkit_slug')
        if toolkit_slug:
            queryset = queryset.filter(toolkit_slug=toolkit_slug.upper())

        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter.upper())

        return queryset

    def get_serializer_class(self):
        if self.action in ('retrieve', 'create'):
            return ComposioConnectionDetailSerializer
        return ComposioConnectionListSerializer

    def create(self, request, *args, **kwargs):
        """
        Plain POST to the collection is not a thing here - a Composio
        connection can only come into existence through the hosted-auth flow.
        """
        return Response(
            {'error': 'Use POST /api/integrations/composio/connections/initiate/ to create a connection.'},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    # -- 4. initiate ------------------------------------------------------
    @action(detail=False, methods=['post'])
    def initiate(self, request):
        """
        Start a Composio hosted-auth flow.

        POST /api/integrations/composio/connections/initiate/
            {"toolkit_slug": "NOTION", "alias": "Team wiki",
             "scope": "USER", "return_to": "/integrations"}

        Returns 201 with the connection row, the Composio ``redirect_url`` to
        send the browser to, the one-time ``state`` nonce, and when the link
        stops being valid.

        The Composio entity id is derived server-side from the JWT; the caller
        can never influence which entity is addressed.
        """
        serializer = ComposioInitiateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        toolkit_slug = data['toolkit_slug']
        scope = data.get('scope') or ComposioConnectionScopeEnum.USER
        if scope == ComposioConnectionScopeEnum.TENANT:
            self.require_admin()

        toolkit = ComposioToolkit.objects.filter(slug=toolkit_slug).first()
        if toolkit and not toolkit.is_enabled:
            return Response({'error': f'{toolkit_slug} is not available on this deployment.'},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            auth_config = ComposioAuthConfig.resolve(toolkit_slug, self.tenant_id)
            if auth_config is None:
                auth_config = ensure_auth_config(toolkit_slug)
        except (ComposioNotConfigured, ComposioError) as exc:
            return composio_error_response(exc)

        alias = data.get('alias')
        composio_user_id = entity_id_for(self.tenant_id, self.user_id, scope)

        existing = ComposioConnection.objects.filter(
            tenant_id=self.tenant_id, user_id=self.user_id,
            toolkit_slug=toolkit_slug, alias=alias,
        ).exclude(status__in=ComposioConnectionStatusEnum.terminal()).first()

        if existing and existing.status == ComposioConnectionStatusEnum.ACTIVE:
            return Response(
                {'error': 'You already have an active connection for this toolkit. '
                          'Disconnect it first, or supply a different alias.',
                 'connection': ComposioConnectionDetailSerializer(existing).data},
                status=status.HTTP_409_CONFLICT,
            )

        connection = existing or ComposioConnection.objects.create(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            scope=scope,
            composio_user_id=composio_user_id,
            auth_config=auth_config,
            toolkit_slug=toolkit_slug,
            alias=alias,
            status=ComposioConnectionStatusEnum.PENDING,
            created_by_user_id=self.user_id,
        )

        state = secrets.token_urlsafe(32)
        ttl = int(getattr(settings, 'COMPOSIO_LINK_TTL_SECONDS', 900))
        expires_at = timezone.now() + timedelta(seconds=ttl)

        callback_url = getattr(settings, 'COMPOSIO_CALLBACK_URL', '')
        separator = '&' if '?' in callback_url else '?'
        callback_with_state = f"{callback_url}{separator}{urlencode({'state': state})}"

        try:
            link = get_composio_client().initiate_connection(
                composio_user_id=composio_user_id,
                auth_config_id=auth_config.auth_config_id,
                callback_url=callback_with_state,
                alias=alias,
                allow_multiple=True,
            )
        except (ComposioNotConfigured, ComposioError) as exc:
            connection.mark_failed(str(exc))
            connection.record_event(
                ComposioEventTypeEnum.FAILED, actor_user_id=self.user_id,
                message='Composio refused to create the auth link', source_ip=_client_ip(request),
            )
            return composio_error_response(exc)

        connection.mark_initializing(
            connected_account_id=link.get('id'),
            metadata=scrub({'link': {'status': link.get('status'),
                                     'expires_at': str(link.get('expires_at') or '')}}),
        )

        ComposioLinkState.objects.create(
            state=state,
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            connection=connection,
            toolkit_slug=toolkit_slug,
            return_to=_safe_return_to(data.get('return_to')),
            link_expires_at=None,
            expires_at=expires_at,
        )

        connection.record_event(
            ComposioEventTypeEnum.INITIATED, actor_user_id=self.user_id,
            message=f'Hosted auth started for {toolkit_slug}',
            payload={'toolkit_slug': toolkit_slug, 'scope': scope},
            source_ip=_client_ip(request),
        )

        # Safety net: the callback normally resolves the status, but the user
        # may close the tab before Composio redirects.
        try:
            from integrations.tasks import poll_composio_connection
            poll_composio_connection.apply_async(args=[connection.id], countdown=5)
        except Exception as exc:  # noqa: BLE001 - a missing broker must not fail the request
            logger.warning('Could not schedule poll_composio_connection: %s', exc)

        return Response(
            {
                'connection': ComposioConnectionDetailSerializer(connection).data,
                'redirect_url': link.get('redirect_url'),
                'state': state,
                'expires_at': expires_at,
            },
            status=status.HTTP_201_CREATED,
        )

    # -- 7. status --------------------------------------------------------
    @action(detail=True, methods=['get'])
    def status(self, request, public_id=None):
        """
        Report the current status of a connection.

        GET /api/integrations/composio/connections/{public_id}/status/?force=true

        Short-circuits to the cached row when it was checked less than
        COMPOSIO_STATUS_MIN_INTERVAL ago, so a 2-second frontend poll costs at
        most one Composio call every 10 seconds.
        """
        connection = self.get_object()
        force = request.query_params.get('force') == 'true'
        try:
            connection = sync_connection_status(connection, force=force, actor_user_id=self.user_id)
        except (ComposioNotConfigured, ComposioError) as exc:
            if not isinstance(exc, ComposioNotConfigured):
                logger.warning('status sync failed for %s: %s', public_id, exc)

        # Guarantee the poll terminates: a row still PENDING/INITIALIZING past
        # the link TTL can never become ACTIVE, so report it as FAILED rather
        # than leaving the client polling forever.
        pending = (ComposioConnectionStatusEnum.PENDING, ComposioConnectionStatusEnum.INITIALIZING)
        ttl = int(getattr(settings, 'COMPOSIO_LINK_TTL_SECONDS', 900))
        if (connection.status in pending
                and timezone.now() - connection.created_at > timedelta(seconds=ttl)):
            connection.mark_failed('Authorisation was not completed before the link expired')
            connection.record_event(
                ComposioEventTypeEnum.FAILED,
                actor_user_id=self.user_id,
                message='Hosted-auth link expired before the user finished authorising',
            )

        return Response({
            'public_id': str(connection.public_id),
            'status': connection.status,
            'connected_at': connection.connected_at,
            'account_label': connection.account_label,
            'last_error': connection.last_error,
            'checked_at': connection.last_status_check_at,
        })

    # -- 8. refresh -------------------------------------------------------
    @action(detail=True, methods=['post'])
    def refresh(self, request, public_id=None):
        """
        Re-authorise a connection by starting a fresh hosted-auth round trip.

        POST /api/integrations/composio/connections/{public_id}/refresh/
        """
        connection = self.get_object()
        serializer = ComposioRefreshSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            composio_user_id = self._assert_identity(connection)
        except ComposioIdentityMismatch as exc:
            return composio_error_response(exc)

        state = secrets.token_urlsafe(32)
        ttl = int(getattr(settings, 'COMPOSIO_LINK_TTL_SECONDS', 900))
        expires_at = timezone.now() + timedelta(seconds=ttl)

        callback_url = getattr(settings, 'COMPOSIO_CALLBACK_URL', '')
        separator = '&' if '?' in callback_url else '?'
        callback_with_state = f"{callback_url}{separator}{urlencode({'state': state})}"

        try:
            link = get_composio_client().initiate_connection(
                composio_user_id=composio_user_id,
                auth_config_id=connection.auth_config.auth_config_id,
                callback_url=callback_with_state,
                alias=connection.alias,
                allow_multiple=True,
            )
        except (ComposioNotConfigured, ComposioError) as exc:
            return composio_error_response(exc)

        if link.get('id'):
            connection.connected_account_id = link['id']
        connection.status = ComposioConnectionStatusEnum.INITIALIZING
        connection.save(update_fields=['connected_account_id', 'status', 'updated_at'])

        ComposioLinkState.objects.create(
            state=state,
            tenant_id=connection.tenant_id,
            user_id=connection.user_id,
            connection=connection,
            toolkit_slug=connection.toolkit_slug,
            return_to=_safe_return_to(serializer.validated_data.get('return_to')),
            expires_at=expires_at,
        )
        connection.record_event(
            ComposioEventTypeEnum.REFRESHED, actor_user_id=self.user_id,
            message='Re-authorisation started', source_ip=_client_ip(request),
        )

        return Response({'redirect_url': link.get('redirect_url'),
                         'state': state,
                         'expires_at': expires_at})

    # -- 9/10. disable / enable -------------------------------------------
    @action(detail=True, methods=['post'])
    def disable(self, request, public_id=None):
        """
        Disable a connection at Composio without deleting it.

        POST /api/integrations/composio/connections/{public_id}/disable/
        """
        return self._toggle(request, enable=False)

    @action(detail=True, methods=['post'])
    def enable(self, request, public_id=None):
        """
        Re-enable a previously disabled connection.

        POST /api/integrations/composio/connections/{public_id}/enable/
        """
        return self._toggle(request, enable=True)

    def _toggle(self, request, enable):
        connection = self.get_object()
        try:
            self._assert_identity(connection)
        except ComposioIdentityMismatch as exc:
            return composio_error_response(exc)

        if not connection.connected_account_id:
            return Response({'error': 'This connection has not been authorised yet.'},
                            status=status.HTTP_400_BAD_REQUEST)

        client_call = 'enable_connection' if enable else 'disable_connection'
        try:
            getattr(get_composio_client(), client_call)(connection.connected_account_id)
        except (ComposioNotConfigured, ComposioError) as exc:
            return composio_error_response(exc)

        connection.status = (ComposioConnectionStatusEnum.ACTIVE if enable
                             else ComposioConnectionStatusEnum.INACTIVE)
        connection.save(update_fields=['status', 'updated_at'])
        connection.record_event(
            ComposioEventTypeEnum.ENABLED if enable else ComposioEventTypeEnum.DISABLED,
            actor_user_id=self.user_id,
            message='Connection enabled' if enable else 'Connection disabled',
            source_ip=_client_ip(request),
        )
        return Response({'status': connection.status,
                         'connection': ComposioConnectionDetailSerializer(connection).data})

    # -- 11/12. disconnect / destroy --------------------------------------
    @action(detail=True, methods=['post'])
    def disconnect(self, request, public_id=None):
        """
        Disconnect: delete the account at Composio, then tombstone the row.

        POST /api/integrations/composio/connections/{public_id}/disconnect/

        Unlike the legacy soft-disconnect there are no local secrets to null
        out - but we DO revoke at Composio first. A local-only disconnect that
        left the account live at Composio would be a security lie.
        """
        connection = self.get_object()
        error = self._revoke(connection, actor_user_id=self.user_id, request=request)
        if error is not None:
            return error
        return Response({'message': 'Connection disconnected successfully',
                         'connection': ComposioConnectionDetailSerializer(connection).data})

    def destroy(self, request, *args, **kwargs):
        """
        DELETE /api/integrations/composio/connections/{public_id}/

        Same semantics as POST /disconnect/ - REST clients expect DELETE to
        exist, and both must revoke at Composio.
        """
        connection = self.get_object()
        error = self._revoke(connection, actor_user_id=self.user_id, request=request)
        if error is not None:
            return error
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _revoke(self, connection, actor_user_id, request, reason=None):
        """Shared revoke path. Returns an error Response, or None on success."""
        try:
            self._assert_identity(connection)
        except ComposioIdentityMismatch as exc:
            return composio_error_response(exc)

        if connection.connected_account_id:
            try:
                get_composio_client().delete_connection(connection.connected_account_id)
            except ComposioNotConfigured as exc:
                return composio_error_response(exc)
            except ComposioError as exc:
                # Do not tombstone locally if the revoke failed - that would
                # leave a live third-party grant we no longer track.
                logger.error('Composio revoke failed for %s: %s', connection.public_id, exc)
                return composio_error_response(exc)

        connection.mark_disconnected(ComposioConnectionStatusEnum.DELETED)
        connection.record_event(
            ComposioEventTypeEnum.DISCONNECTED, actor_user_id=actor_user_id,
            message=reason or 'Connection disconnected',
            source_ip=_client_ip(request) if request else None,
        )
        return None

    # -- 13. events -------------------------------------------------------
    @action(detail=True, methods=['get'])
    def events(self, request, public_id=None):
        """
        List the audit trail for a connection, newest first.

        GET /api/integrations/composio/connections/{public_id}/events/
        """
        connection = self.get_object()
        queryset = ComposioConnectionEvent.objects.filter(
            connection=connection, tenant_id=connection.tenant_id
        )
        page = self.paginate_queryset(queryset)
        if page is not None:
            return self.get_paginated_response(ComposioConnectionEventSerializer(page, many=True).data)
        return Response(ComposioConnectionEventSerializer(queryset, many=True).data)

    # -- 14. execute (ships dark) -----------------------------------------
    @action(detail=True, methods=['post'])
    def execute(self, request, public_id=None):
        """
        Execute a Composio tool through this connection.

        POST /api/integrations/composio/connections/{public_id}/execute/
            {"tool_slug": "GMAIL_GET_PROFILE", "arguments": {}}

        Disabled unless COMPOSIO_EXECUTE_ENABLED is true, and the slug must be
        on the auth config's restrict_to_tools allowlist. Never accepts an
        arbitrary tool slug from a browser.
        """
        if not getattr(settings, 'COMPOSIO_EXECUTE_ENABLED', False):
            return Response({'error': 'Tool execution is disabled on this deployment.',
                             'code': 'composio_execute_disabled'},
                            status=status.HTTP_403_FORBIDDEN)

        connection = self.get_object()
        serializer = ComposioExecuteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tool_slug = serializer.validated_data['tool_slug']

        allowlist = connection.auth_config.restrict_to_tools or []
        if tool_slug not in [str(s).upper() for s in allowlist]:
            return Response({'error': f'{tool_slug} is not in the allowlist for this connection.'},
                            status=status.HTTP_400_BAD_REQUEST)

        if not connection.is_usable:
            return Response({'error': 'This connection is not active.'},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            composio_user_id = self._assert_identity(connection)
        except ComposioIdentityMismatch as exc:
            return composio_error_response(exc)

        versions = connection.auth_config.default_tool_versions or {}
        try:
            result = get_composio_client().execute_tool(
                slug=tool_slug,
                composio_user_id=composio_user_id,
                connected_account_id=connection.connected_account_id,
                arguments=serializer.validated_data.get('arguments') or {},
                version=versions.get(tool_slug),
            )
        except (ComposioNotConfigured, ComposioError) as exc:
            connection.record_event(
                ComposioEventTypeEnum.ERROR, actor_user_id=self.user_id,
                message=f'{tool_slug} failed', payload={'tool_slug': tool_slug},
                source_ip=_client_ip(request),
            )
            return composio_error_response(exc)

        connection.last_used_at = timezone.now()
        connection.save(update_fields=['last_used_at', 'updated_at'])
        # Only the slug and the outcome are audited - never the payload.
        connection.record_event(
            ComposioEventTypeEnum.TOOL_EXECUTED, actor_user_id=self.user_id,
            message=f'{tool_slug} executed', payload={'tool_slug': tool_slug},
            source_ip=_client_ip(request),
        )

        plain = result if isinstance(result, dict) else getattr(result, '__dict__', {})
        return Response({
            'successful': bool(plain.get('successful', True)),
            'data': scrub(plain.get('data')),
            'error': plain.get('error'),
        })

    # -- identity assertion ------------------------------------------------
    def _assert_identity(self, connection):
        """
        Layer 3 of the isolation stack: re-derive the Composio entity id from
        the row and the request tenant, and refuse to proceed on a mismatch.
        """
        from integrations.services.composio_client import assert_connection_identity
        return assert_connection_identity(connection, tenant_id=self.tenant_id)


# ---------------------------------------------------------------------------
# 15-17. Auth configs (tenant admin)
# ---------------------------------------------------------------------------

class ComposioAuthConfigViewSet(_ComposioViewMixin, viewsets.ModelViewSet):
    """
    Inspect and manage the Composio auth configs available to a tenant.

    Tenant administrators use this to see which toolkits are wired up and, if
    they bring their own OAuth app, to register a tenant-specific config that
    takes precedence over the platform-wide one.

    Reads see the tenant's own configs plus the platform-wide defaults; writes
    may only ever touch the tenant's own rows.
    """
    permission_resource = 'connections'
    lookup_field = 'public_id'
    serializer_class = ComposioAuthConfigSerializer
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    def get_queryset(self):
        if not self.tenant_id:
            logger.warning('ComposioAuthConfigViewSet.get_queryset called without tenant_id')
            return ComposioAuthConfig.objects.none()

        if self.request.method in ('POST', 'PATCH', 'PUT', 'DELETE'):
            # Writes never reach the platform-global row.
            queryset = ComposioAuthConfig.objects.filter(tenant_id=self.tenant_id)
        else:
            queryset = ComposioAuthConfig.objects.filter(
                models.Q(tenant_id=self.tenant_id) | models.Q(tenant_id__isnull=True)
            )

        toolkit_slug = self.request.query_params.get('toolkit_slug')
        if toolkit_slug:
            queryset = queryset.filter(toolkit_slug=toolkit_slug.upper())
        return queryset

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.require_admin()

    def create(self, request, *args, **kwargs):
        """
        POST /api/integrations/composio/auth-configs/
            {"toolkit_slug": "NOTION", "name": "Notion", "use_composio_managed": true}
        """
        serializer = ComposioAuthConfigCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if not data.get('use_composio_managed', True):
            return Response(
                {'error': 'Bring-your-own OAuth apps are not supported through this endpoint. '
                          'A client secret must never transit this API.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        toolkit_slug = data['toolkit_slug']
        if ComposioAuthConfig.objects.filter(tenant_id=self.tenant_id,
                                             toolkit_slug=toolkit_slug).exists():
            return Response({'error': f'This tenant already has an auth config for {toolkit_slug}.'},
                            status=status.HTTP_409_CONFLICT)

        try:
            client = get_composio_client()
            created = client.create_managed_auth_config(toolkit_slug, data.get('name') or toolkit_slug)
        except (ComposioNotConfigured, ComposioError) as exc:
            return composio_error_response(exc)

        auth_config_id = getattr(created, 'id', None) or (
            created.get('id') if isinstance(created, dict) else None)
        if not auth_config_id:
            return Response({'error': 'Composio did not return an auth config id.'},
                            status=status.HTTP_502_BAD_GATEWAY)

        config = ComposioAuthConfig.objects.create(
            tenant_id=self.tenant_id,
            toolkit_slug=toolkit_slug,
            auth_config_id=auth_config_id,
            name=data.get('name') or toolkit_slug,
            is_composio_managed=True,
            is_active=True,
            metadata=scrub(created if isinstance(created, dict) else {}),
            last_synced_at=timezone.now(),
        )
        return Response(ComposioAuthConfigSerializer(config).data, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        # The queryset already excludes the global row for write methods, but
        # assert here too - defence in depth against a future queryset change.
        instance = serializer.instance
        if instance.tenant_id is None or str(instance.tenant_id) != str(self.tenant_id):
            raise PermissionDenied('The platform-wide auth config cannot be modified by a tenant.')
        serializer.save()


# ---------------------------------------------------------------------------
# 18. Tenant admin oversight
# ---------------------------------------------------------------------------

class ComposioAdminConnectionViewSet(_ComposioViewMixin, viewsets.ReadOnlyModelViewSet):
    """
    Tenant-administrator oversight of every Composio connection in the tenant.

    "Admin" here means TENANT admin, never platform admin: the queryset is
    still filtered to the caller's own tenant. This is the only Composio
    surface that can see another user's connection, and it is gated on
    common.permissions.is_admin_request.
    """
    permission_resource = 'connections'
    lookup_field = 'public_id'
    serializer_class = ComposioAdminConnectionSerializer
    action_permission_map = {'revoke': 'delete'}
    search_fields = ['alias', 'account_label', 'toolkit_slug']

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.require_admin()

    def get_queryset(self):
        if not self.tenant_id:
            logger.warning('ComposioAdminConnectionViewSet.get_queryset called without tenant_id')
            return ComposioConnection.objects.none()

        queryset = (ComposioConnection.objects
                    .filter(tenant_id=self.tenant_id)
                    .select_related('auth_config')
                    .annotate(events_count=models.Count('events')))

        for param, field in (('user_id', 'user_id'), ('toolkit_slug', 'toolkit_slug'),
                             ('status', 'status')):
            value = self.request.query_params.get(param)
            if value:
                queryset = queryset.filter(**{field: value.upper() if field != 'user_id' else value})

        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                models.Q(alias__icontains=search)
                | models.Q(account_label__icontains=search)
                | models.Q(toolkit_slug__icontains=search)
            )
        return queryset

    @action(detail=True, methods=['post'])
    def revoke(self, request, public_id=None):
        """
        Revoke another user's connection as a tenant administrator.

        POST /api/integrations/composio/admin/connections/{public_id}/revoke/
            {"reason": "Offboarding"}
        """
        connection = self.get_object()
        serializer = ComposioRevokeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data.get('reason') or 'Revoked by tenant administrator'

        from integrations.services.composio_client import assert_connection_identity
        try:
            assert_connection_identity(connection, tenant_id=self.tenant_id)
        except ComposioIdentityMismatch as exc:
            return composio_error_response(exc)

        if connection.connected_account_id:
            try:
                get_composio_client().delete_connection(connection.connected_account_id)
            except (ComposioNotConfigured, ComposioError) as exc:
                return composio_error_response(exc)

        connection.mark_disconnected(ComposioConnectionStatusEnum.REVOKED)
        connection.record_event(
            ComposioEventTypeEnum.DISCONNECTED, actor_user_id=self.user_id,
            message=reason, source_ip=_client_ip(request),
        )
        return Response({'message': 'Connection revoked',
                         'connection': ComposioAdminConnectionSerializer(connection).data})


# ---------------------------------------------------------------------------
# 19a. Public hosted-auth callback
# ---------------------------------------------------------------------------

class ComposioCallbackView(APIView):
    """
    GET /api/integrations/composio/callback/?state=...

    Where Composio's hosted-auth page sends the browser once the user has
    finished (or abandoned) authorising. There is no Authorization header on
    this request - the one-time ``state`` nonce IS the authenticator, and the
    tenant/user are read from the ComposioLinkState row, never from a query
    parameter.

    Always answers with a 302 back to the frontend, never a JSON error: a
    browser is on the other end.
    """
    authentication_classes = []
    permission_classes = []

    # Provider/Composio error codes we pass straight through so the SPA can
    # tell "the user clicked Cancel" apart from "something broke".
    CANCELLATION_REASONS = {
        'access_denied': 'access_denied',
        'user_denied': 'access_denied',
        'consent_required': 'access_denied',
        'user_cancelled': 'user_cancelled',
        'user_canceled': 'user_cancelled',
        'cancelled': 'user_cancelled',
        'canceled': 'user_cancelled',
    }

    def _provider_error(self, request):
        """Normalise a provider error query param into a UI-mappable reason."""
        raw = (request.query_params.get('error')
               or request.query_params.get('error_code')
               or request.query_params.get('status'))
        if not raw:
            return None
        key = str(raw).strip().lower()
        if key in ('success', 'connected', 'active', 'ok'):
            return None
        return self.CANCELLATION_REASONS.get(key, 'auth_failed')

    def get(self, request):
        state = request.query_params.get('state')
        if not state:
            return self._bounce(status_value='error', reason='invalid_state')

        try:
            with transaction.atomic():
                link_state = (ComposioLinkState.objects
                              .select_for_update()
                              .select_related('connection', 'connection__auth_config')
                              .filter(state=state)
                              .first())
                if link_state is None or not link_state.is_valid():
                    logger.warning('Composio callback with unknown/spent/expired state')
                    return self._bounce(status_value='error', reason='invalid_state')
                link_state.consume()
        except Exception as exc:  # noqa: BLE001 - never 500 into a browser
            logger.error('Composio callback state handling failed: %s', exc, exc_info=True)
            return self._bounce(status_value='error', reason='invalid_state')

        connection = link_state.connection
        return_to = _safe_return_to(link_state.return_to)
        toolkit = link_state.toolkit_slug

        connection.record_event(
            ComposioEventTypeEnum.CALLBACK,
            message='Hosted-auth callback received',
            source_ip=_client_ip(request),
        )

        # The user declined on the provider's consent screen. Composio bounces
        # back with an error code rather than an authorised account; fail the
        # row immediately so the SPA's status poll terminates at once.
        provider_error = self._provider_error(request)
        if provider_error:
            connection.mark_failed(f'Authorisation not granted ({provider_error})')
            connection.record_event(
                ComposioEventTypeEnum.FAILED,
                message=f'User did not complete authorisation: {provider_error}',
                source_ip=_client_ip(request),
            )
            return self._bounce(status_value='error', reason=provider_error,
                                toolkit=toolkit, connection=connection, return_to=return_to)

        try:
            connection = sync_connection_status(connection, force=True)
        except ComposioNotConfigured:
            return self._bounce(status_value='pending', toolkit=toolkit,
                                connection=connection, return_to=return_to)
        except (ComposioError, ComposioIdentityMismatch) as exc:
            logger.warning('Composio callback status sync failed for %s: %s',
                           connection.public_id, exc)
            return self._bounce(status_value='pending', toolkit=toolkit,
                                connection=connection, return_to=return_to)

        if connection.status == ComposioConnectionStatusEnum.ACTIVE:
            connection.record_event(
                ComposioEventTypeEnum.ACTIVATED,
                message=f'{toolkit} connected', source_ip=_client_ip(request),
            )
            return self._bounce(status_value='connected', toolkit=toolkit,
                                connection=connection, return_to=return_to)

        if connection.status in (ComposioConnectionStatusEnum.FAILED,
                                 ComposioConnectionStatusEnum.EXPIRED,
                                 ComposioConnectionStatusEnum.REVOKED):
            return self._bounce(status_value='error', reason='auth_failed',
                                toolkit=toolkit, connection=connection, return_to=return_to)

        return self._bounce(status_value='pending', toolkit=toolkit,
                            connection=connection, return_to=return_to)

    @staticmethod
    def _bounce(status_value, reason=None, toolkit=None, connection=None, return_to=None):
        """Build the 302 back to the SPA. Only whitelisted params are echoed."""
        params = {'status': status_value}
        if reason:
            params['reason'] = reason
        if toolkit:
            params['toolkit'] = toolkit
        if connection is not None:
            params['connection'] = str(connection.public_id)
        params['return_to'] = _safe_return_to(return_to)

        base = getattr(settings, 'COMPOSIO_FRONTEND_RETURN_URL', '') or '/'
        separator = '&' if '?' in base else '?'
        return redirect(f"{base}{separator}{urlencode(params)}")


# ---------------------------------------------------------------------------
# 19b. Public trigger webhook
# ---------------------------------------------------------------------------

class ComposioWebhookView(APIView):
    """
    POST /api/integrations/composio/webhook/

    Receives Composio trigger events. Unauthenticated in the JWT sense, but
    every request must carry a valid HMAC-SHA256 signature over
    "{webhook-id}.{webhook-timestamp}.{raw body}" computed with
    COMPOSIO_WEBHOOK_SECRET, and a webhook-timestamp inside the replay window.
    An unset secret rejects everything.

    Triggers themselves are out of scope for v1: a verified delivery is
    recorded on the matching connection's audit trail and acknowledged with
    200, nothing more.
    """
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        secret = getattr(settings, 'COMPOSIO_WEBHOOK_SECRET', '')
        try:
            verify_webhook_signature(
                webhook_id=request.headers.get('webhook-id'),
                webhook_timestamp=request.headers.get('webhook-timestamp'),
                body=request.body,
                signature=request.headers.get('webhook-signature'),
                secret=secret,
            )
        except ComposioWebhookVerificationError as exc:
            logger.warning('Rejected Composio webhook: %s', exc)
            return Response({'error': 'Invalid signature'}, status=status.HTTP_401_UNAUTHORIZED)

        payload = request.data if isinstance(request.data, dict) else {}
        metadata = payload.get('metadata') or {}
        connected_account_id = metadata.get('connected_account_id')

        if connected_account_id:
            connection = ComposioConnection.objects.filter(
                connected_account_id=connected_account_id
            ).first()
            if connection is not None:
                connection.record_event(
                    ComposioEventTypeEnum.WEBHOOK,
                    message=f"Trigger {metadata.get('trigger_slug') or 'event'} received",
                    payload=scrub({'trigger_slug': metadata.get('trigger_slug'),
                                   'trigger_id': metadata.get('trigger_id'),
                                   'type': payload.get('type')}),
                )

        return Response({'ok': True})
