"""
Tests for the Composio integration.

Focus is the security surface, because that is what the design turns on:

  * the entity-id scheme, including that a forged row cannot escape its tenant;
  * tenant + user queryset isolation across every Composio endpoint;
  * the initiate -> link() -> callback -> poll flow;
  * callback nonce replay and expiry rejection;
  * webhook HMAC verification and replay rejection;
  * that no token, secret or Composio entity id is ever serialized to a client;
  * the tightened legacy ConnectionViewSet queryset.

Every Composio network call is mocked - these tests need no COMPOSIO_API_KEY,
no network, and do not require the composio package to be importable.
"""

import base64
import hashlib
import hmac
import json
import time
import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

import jwt
from django.conf import settings
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from integrations.models import (
    ComposioAuthConfig,
    ComposioConnection,
    ComposioConnectionEvent,
    ComposioConnectionScopeEnum,
    ComposioConnectionStatusEnum,
    ComposioLinkState,
    ComposioToolkit,
    Connection,
    ConnectionStatusEnum,
    Integration,
    IntegrationTypeEnum,
)
from integrations.services.composio_client import (
    ComposioIdentityMismatch,
    ComposioWebhookVerificationError,
    assert_connection_identity,
    build_composio_user_id,
    scrub,
    verify_webhook_signature,
)

COMPOSIO_URL = '/api/integrations/composio'

FULL_PERMISSIONS = {
    'integrations.providers.view': True,
    'integrations.connections.view': True,
    'integrations.connections.create': True,
    'integrations.connections.edit': True,
    'integrations.connections.delete': True,
}


def make_token(tenant_id, user_id, permissions=None, is_admin=False, modules=None):
    """Mint a JWT the way superadmin does, so the real middleware accepts it."""
    payload = {
        'user_id': str(user_id),
        'email': f'{user_id}@example.test',
        'tenant_id': str(tenant_id),
        'tenant_slug': 'test-tenant',
        'is_super_admin': False,
        'permissions': dict(permissions if permissions is not None else FULL_PERMISSIONS),
        'enabled_modules': modules if modules is not None else ['integrations'],
        'roles': [],
        'exp': int(time.time()) + 3600,
    }
    if is_admin:
        payload['permissions']['admin.full_access'] = True
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def client_for(tenant_id, user_id, **kwargs):
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f'Bearer {make_token(tenant_id, user_id, **kwargs)}')
    return api


class ComposioTestMixin:
    """Shared fixtures: two tenants, two users in tenant A, one auth config."""

    def setUp(self):
        super().setUp()
        self.tenant_a = uuid.uuid4()
        self.tenant_b = uuid.uuid4()
        self.user_a1 = uuid.uuid4()
        self.user_a2 = uuid.uuid4()
        self.user_b1 = uuid.uuid4()

        self.auth_config = ComposioAuthConfig.objects.create(
            tenant_id=None,
            toolkit_slug='NOTION',
            auth_config_id='ac_notion_global',
            name='Notion',
        )
        ComposioToolkit.objects.create(
            slug='NOTION', name='Notion', is_enabled=True,
            composio_managed_auth_schemes=['OAUTH2'],
        )

    def make_connection(self, tenant_id, user_id, **kwargs):
        scope = kwargs.pop('scope', ComposioConnectionScopeEnum.USER)
        entity_user = None if scope == ComposioConnectionScopeEnum.TENANT else user_id
        defaults = {
            'tenant_id': tenant_id,
            'user_id': user_id,
            'scope': scope,
            'composio_user_id': build_composio_user_id(tenant_id, entity_user),
            'auth_config': self.auth_config,
            'toolkit_slug': 'NOTION',
            'connected_account_id': f'ca_{uuid.uuid4().hex[:12]}',
            'status': ComposioConnectionStatusEnum.ACTIVE,
            'created_by_user_id': user_id,
        }
        defaults.update(kwargs)
        return ComposioConnection.objects.create(**defaults)


# ---------------------------------------------------------------------------
# Identity scheme
# ---------------------------------------------------------------------------

class BuildComposioUserIdTests(TestCase):
    """The entity id is the only isolation boundary Composio enforces."""

    def test_per_user_format(self):
        result = build_composio_user_id('tenant-1', 'user-1')
        self.assertEqual(result, f'{settings.COMPOSIO_USER_NAMESPACE}:tenant-1:user-1')

    def test_tenant_scope_uses_sentinel(self):
        result = build_composio_user_id('tenant-1')
        self.assertTrue(result.endswith(':tenant'))

    def test_tenant_sentinel_cannot_collide_with_a_user_uuid(self):
        user_form = build_composio_user_id('tenant-1', uuid.uuid4())
        tenant_form = build_composio_user_id('tenant-1')
        self.assertNotEqual(user_form, tenant_form)

    def test_namespace_isolates_environments(self):
        with override_settings(COMPOSIO_USER_NAMESPACE='celiyo-staging'):
            staging = build_composio_user_id('t', 'u')
        with override_settings(COMPOSIO_USER_NAMESPACE='celiyo-prod'):
            prod = build_composio_user_id('t', 'u')
        self.assertNotEqual(staging, prod)
        self.assertTrue(staging.startswith('celiyo-staging:'))
        self.assertTrue(prod.startswith('celiyo-prod:'))

    def test_two_tenants_of_the_same_user_get_different_entities(self):
        user = uuid.uuid4()
        self.assertNotEqual(
            build_composio_user_id(uuid.uuid4(), user),
            build_composio_user_id(uuid.uuid4(), user),
        )

    def test_missing_tenant_is_rejected(self):
        with self.assertRaises(Exception):
            build_composio_user_id(None, 'user-1')


class AssertConnectionIdentityTests(ComposioTestMixin, TestCase):
    """A row that has been tampered with must never be actionable."""

    def test_consistent_row_passes(self):
        connection = self.make_connection(self.tenant_a, self.user_a1)
        self.assertEqual(
            assert_connection_identity(connection, tenant_id=self.tenant_a),
            build_composio_user_id(self.tenant_a, self.user_a1),
        )

    def test_forged_entity_id_pointing_at_another_tenant_is_rejected(self):
        connection = self.make_connection(self.tenant_a, self.user_a1)
        # Hand-edit the row to name tenant B's entity, the exact attack the
        # assertion exists to stop.
        ComposioConnection.objects.filter(pk=connection.pk).update(
            composio_user_id=build_composio_user_id(self.tenant_b, self.user_b1)
        )
        connection.refresh_from_db()
        with self.assertRaises(ComposioIdentityMismatch):
            assert_connection_identity(connection, tenant_id=self.tenant_a)

    def test_forged_entity_id_is_rejected_even_without_a_request_tenant(self):
        connection = self.make_connection(self.tenant_a, self.user_a1)
        ComposioConnection.objects.filter(pk=connection.pk).update(
            composio_user_id=build_composio_user_id(self.tenant_b, self.user_b1)
        )
        connection.refresh_from_db()
        with self.assertRaises(ComposioIdentityMismatch):
            assert_connection_identity(connection)

    def test_row_from_another_tenant_is_rejected(self):
        connection = self.make_connection(self.tenant_b, self.user_b1)
        with self.assertRaises(ComposioIdentityMismatch):
            assert_connection_identity(connection, tenant_id=self.tenant_a)

    def test_namespace_change_invalidates_a_stale_row(self):
        connection = self.make_connection(self.tenant_a, self.user_a1)
        with override_settings(COMPOSIO_USER_NAMESPACE='celiyo-prod'):
            with self.assertRaises(ComposioIdentityMismatch):
                assert_connection_identity(connection, tenant_id=self.tenant_a)

    def test_tenant_scoped_row_uses_the_tenant_sentinel(self):
        connection = self.make_connection(
            self.tenant_a, self.user_a1, scope=ComposioConnectionScopeEnum.TENANT
        )
        self.assertEqual(
            assert_connection_identity(connection, tenant_id=self.tenant_a),
            build_composio_user_id(self.tenant_a),
        )


# ---------------------------------------------------------------------------
# Queryset isolation
# ---------------------------------------------------------------------------

class ComposioConnectionIsolationTests(ComposioTestMixin, TestCase):

    def test_other_tenant_connection_is_404_not_403(self):
        """404, so we never confirm that the row exists."""
        other = self.make_connection(self.tenant_b, self.user_b1)
        api = client_for(self.tenant_a, self.user_a1)
        response = api.get(f'{COMPOSIO_URL}/connections/{other.public_id}/')
        self.assertEqual(response.status_code, 404)

    def test_colleague_user_scoped_connection_is_404(self):
        colleague = self.make_connection(self.tenant_a, self.user_a2)
        api = client_for(self.tenant_a, self.user_a1)
        response = api.get(f'{COMPOSIO_URL}/connections/{colleague.public_id}/')
        self.assertEqual(response.status_code, 404)

    def test_colleague_tenant_scoped_connection_is_visible(self):
        shared = self.make_connection(
            self.tenant_a, self.user_a2, scope=ComposioConnectionScopeEnum.TENANT
        )
        api = client_for(self.tenant_a, self.user_a1)
        response = api.get(f'{COMPOSIO_URL}/connections/{shared.public_id}/')
        self.assertEqual(response.status_code, 200)

    def test_list_shows_only_own_and_shared(self):
        mine = self.make_connection(self.tenant_a, self.user_a1)
        shared = self.make_connection(
            self.tenant_a, self.user_a2, scope=ComposioConnectionScopeEnum.TENANT
        )
        self.make_connection(self.tenant_a, self.user_a2)          # colleague, private
        self.make_connection(self.tenant_b, self.user_b1)          # other tenant

        api = client_for(self.tenant_a, self.user_a1)
        response = api.get(f'{COMPOSIO_URL}/connections/')
        self.assertEqual(response.status_code, 200)
        returned = {row['public_id'] for row in response.data['results']}
        self.assertEqual(returned, {str(mine.public_id), str(shared.public_id)})

    def test_disconnect_on_another_users_connection_is_404(self):
        colleague = self.make_connection(self.tenant_a, self.user_a2)
        api = client_for(self.tenant_a, self.user_a1)
        response = api.post(f'{COMPOSIO_URL}/connections/{colleague.public_id}/disconnect/')
        self.assertEqual(response.status_code, 404)
        colleague.refresh_from_db()
        self.assertEqual(colleague.status, ComposioConnectionStatusEnum.ACTIVE)

    def test_missing_authorization_header_is_401(self):
        response = APIClient().get(f'{COMPOSIO_URL}/connections/')
        self.assertEqual(response.status_code, 401)

    def test_non_admin_cannot_reach_the_admin_oversight_endpoint(self):
        api = client_for(self.tenant_a, self.user_a1)
        response = api.get(f'{COMPOSIO_URL}/admin/connections/')
        self.assertEqual(response.status_code, 403)

    def test_tenant_admin_sees_only_their_own_tenant(self):
        mine = self.make_connection(self.tenant_a, self.user_a1)
        colleague = self.make_connection(self.tenant_a, self.user_a2)
        self.make_connection(self.tenant_b, self.user_b1)

        api = client_for(self.tenant_a, self.user_a1, is_admin=True)
        response = api.get(f'{COMPOSIO_URL}/admin/connections/')
        self.assertEqual(response.status_code, 200)
        returned = {row['public_id'] for row in response.data['results']}
        self.assertEqual(returned, {str(mine.public_id), str(colleague.public_id)})

    def test_auth_config_writes_cannot_touch_the_global_row(self):
        api = client_for(self.tenant_a, self.user_a1, is_admin=True)
        response = api.patch(
            f'{COMPOSIO_URL}/auth-configs/{self.auth_config.public_id}/',
            {'name': 'hijacked'}, format='json',
        )
        self.assertEqual(response.status_code, 404)
        self.auth_config.refresh_from_db()
        self.assertEqual(self.auth_config.name, 'Notion')

    def test_auth_config_reads_include_the_global_row(self):
        api = client_for(self.tenant_a, self.user_a1, is_admin=True)
        response = api.get(f'{COMPOSIO_URL}/auth-configs/')
        self.assertEqual(response.status_code, 200)
        slugs = {row['toolkit_slug'] for row in response.data['results']}
        self.assertIn('NOTION', slugs)


class LegacyConnectionViewSetIsolationTests(TestCase):
    """
    The pre-existing leak: ConnectionViewSet filtered on tenant_id alone, so a
    colleague could read and disconnect another user's Google connection - and
    those rows hold that colleague's encrypted OAuth tokens.
    """

    def setUp(self):
        self.tenant = uuid.uuid4()
        self.user_1 = uuid.uuid4()
        self.user_2 = uuid.uuid4()
        self.integration = Integration.objects.create(
            name='Google Sheets Test', type=IntegrationTypeEnum.GOOGLE_SHEETS,
        )
        self.other_connection = Connection.objects.create(
            tenant_id=self.tenant, user_id=self.user_2,
            integration=self.integration, name="Colleague's sheet",
            status=ConnectionStatusEnum.CONNECTED,
        )

    def test_colleague_connection_is_not_listed(self):
        api = client_for(self.tenant, self.user_1)
        response = api.get('/api/integrations/connections/')
        self.assertEqual(response.status_code, 200)
        ids = {row['id'] for row in response.data['results']}
        self.assertNotIn(self.other_connection.id, ids)

    def test_colleague_connection_cannot_be_disconnected(self):
        api = client_for(self.tenant, self.user_1)
        response = api.post(f'/api/integrations/connections/{self.other_connection.id}/disconnect/')
        self.assertEqual(response.status_code, 404)
        self.other_connection.refresh_from_db()
        self.assertEqual(self.other_connection.status, ConnectionStatusEnum.CONNECTED)

    def test_tenant_admin_retains_tenant_wide_visibility(self):
        api = client_for(self.tenant, self.user_1, is_admin=True)
        response = api.get('/api/integrations/connections/')
        self.assertEqual(response.status_code, 200)
        ids = {row['id'] for row in response.data['results']}
        self.assertIn(self.other_connection.id, ids)


# ---------------------------------------------------------------------------
# initiate -> link() -> callback
# ---------------------------------------------------------------------------

@override_settings(
    COMPOSIO_ENABLED=True,
    COMPOSIO_API_KEY='test-key-never-serialized',
    COMPOSIO_CALLBACK_URL='https://crm.example.test/api/integrations/composio/callback/',
    COMPOSIO_FRONTEND_RETURN_URL='https://app.example.test/integrations/composio/callback',
)
class ComposioInitiateFlowTests(ComposioTestMixin, TestCase):

    def _mock_client(self, redirect_url='https://backend.composio.dev/hosted/abc'):
        client = MagicMock()
        client.initiate_connection.return_value = {
            'id': 'ca_test_1234',
            'status': 'INITIATED',
            'redirect_url': redirect_url,
            'expires_at': None,
        }
        return client

    def test_initiate_uses_link_and_returns_the_pinned_shape(self):
        mock_client = self._mock_client()
        api = client_for(self.tenant_a, self.user_a1)
        with patch('integrations.views_composio.get_composio_client', return_value=mock_client):
            response = api.post(f'{COMPOSIO_URL}/connections/initiate/',
                                {'toolkit_slug': 'notion', 'return_to': '/integrations?tab=apps'},
                                format='json')

        self.assertEqual(response.status_code, 201)
        self.assertIn('connection', response.data)
        self.assertIn('public_id', response.data['connection'])
        self.assertEqual(response.data['redirect_url'], 'https://backend.composio.dev/hosted/abc')
        self.assertIn('state', response.data)
        # expires_at must be an absolute timestamp, not a duration.
        self.assertTrue(hasattr(response.data['expires_at'], 'isoformat'))
        self.assertGreater(response.data['expires_at'], timezone.now())

        # link() was called, initiate() was not.
        self.assertTrue(mock_client.initiate_connection.called)
        kwargs = mock_client.initiate_connection.call_args.kwargs
        self.assertEqual(kwargs['composio_user_id'],
                         build_composio_user_id(self.tenant_a, self.user_a1))
        self.assertEqual(kwargs['auth_config_id'], 'ac_notion_global')
        self.assertIn('state=', kwargs['callback_url'])
        self.assertTrue(kwargs['allow_multiple'])

    def test_initiate_persists_the_derived_entity_id_and_a_link_state(self):
        api = client_for(self.tenant_a, self.user_a1)
        with patch('integrations.views_composio.get_composio_client',
                   return_value=self._mock_client()):
            response = api.post(f'{COMPOSIO_URL}/connections/initiate/',
                                {'toolkit_slug': 'NOTION'}, format='json')

        connection = ComposioConnection.objects.get(public_id=response.data['connection']['public_id'])
        self.assertEqual(connection.composio_user_id,
                         build_composio_user_id(self.tenant_a, self.user_a1))
        self.assertEqual(connection.connected_account_id, 'ca_test_1234')
        self.assertEqual(connection.status, ComposioConnectionStatusEnum.INITIALIZING)

        link_state = ComposioLinkState.objects.get(state=response.data['state'])
        self.assertEqual(link_state.connection_id, connection.id)
        self.assertEqual(str(link_state.tenant_id), str(self.tenant_a))
        self.assertIsNone(link_state.consumed_at)

    def test_initiate_cannot_be_pointed_at_another_tenant_by_the_request_body(self):
        mock_client = self._mock_client()
        api = client_for(self.tenant_a, self.user_a1)
        with patch('integrations.views_composio.get_composio_client', return_value=mock_client):
            api.post(f'{COMPOSIO_URL}/connections/initiate/',
                     {'toolkit_slug': 'NOTION',
                      'tenant_id': str(self.tenant_b),
                      'user_id': str(self.user_b1),
                      'composio_user_id': build_composio_user_id(self.tenant_b, self.user_b1)},
                     format='json')
        kwargs = mock_client.initiate_connection.call_args.kwargs
        self.assertEqual(kwargs['composio_user_id'],
                         build_composio_user_id(self.tenant_a, self.user_a1))

    def test_return_to_allowlist_rejects_an_open_redirect(self):
        api = client_for(self.tenant_a, self.user_a1)
        with patch('integrations.views_composio.get_composio_client',
                   return_value=self._mock_client()):
            response = api.post(f'{COMPOSIO_URL}/connections/initiate/',
                                {'toolkit_slug': 'NOTION', 'return_to': '//evil.example.com/steal'},
                                format='json')
        link_state = ComposioLinkState.objects.get(state=response.data['state'])
        self.assertEqual(link_state.return_to, '/integrations')

    def test_return_to_allows_the_frontend_default_target(self):
        api = client_for(self.tenant_a, self.user_a1)
        with patch('integrations.views_composio.get_composio_client',
                   return_value=self._mock_client()):
            response = api.post(f'{COMPOSIO_URL}/connections/initiate/',
                                {'toolkit_slug': 'NOTION', 'return_to': '/integrations?tab=apps'},
                                format='json')
        link_state = ComposioLinkState.objects.get(state=response.data['state'])
        self.assertEqual(link_state.return_to, '/integrations?tab=apps')

    def test_tenant_scope_requires_admin(self):
        api = client_for(self.tenant_a, self.user_a1)
        with patch('integrations.views_composio.get_composio_client',
                   return_value=self._mock_client()):
            response = api.post(f'{COMPOSIO_URL}/connections/initiate/',
                                {'toolkit_slug': 'NOTION', 'scope': 'TENANT'}, format='json')
        self.assertEqual(response.status_code, 403)


@override_settings(
    COMPOSIO_ENABLED=True,
    COMPOSIO_API_KEY='test-key-never-serialized',
    COMPOSIO_FRONTEND_RETURN_URL='https://app.example.test/integrations/composio/callback',
)
class ComposioCallbackTests(ComposioTestMixin, TestCase):
    """The callback is public: the state nonce is the only authenticator."""

    def setUp(self):
        super().setUp()
        self.connection = self.make_connection(
            self.tenant_a, self.user_a1,
            status=ComposioConnectionStatusEnum.INITIALIZING,
        )
        self.link_state = ComposioLinkState.objects.create(
            state='nonce-under-test',
            tenant_id=self.tenant_a,
            user_id=self.user_a1,
            connection=self.connection,
            toolkit_slug='NOTION',
            return_to='/integrations?tab=apps',
            expires_at=timezone.now() + timedelta(minutes=15),
        )

    def _callback(self, **params):
        params.setdefault('state', 'nonce-under-test')
        return self.client.get(f'{COMPOSIO_URL}/callback/', params)

    def test_success_consumes_the_nonce_and_302s_with_the_pinned_params(self):
        with patch('integrations.views_composio.sync_connection_status') as sync:
            def _activate(connection, force=False, actor_user_id=None):
                connection.mark_active()
                return connection
            sync.side_effect = _activate
            response = self._callback()

        self.assertEqual(response.status_code, 302)
        location = response['Location']
        self.assertIn('status=connected', location)
        self.assertIn('toolkit=NOTION', location)
        self.assertIn(f'connection={self.connection.public_id}', location)
        self.assertIn('return_to=', location)

        self.link_state.refresh_from_db()
        self.assertIsNotNone(self.link_state.consumed_at)

    def test_replayed_state_is_rejected(self):
        with patch('integrations.views_composio.sync_connection_status',
                   side_effect=lambda c, **kw: c):
            first = self._callback()
        self.assertEqual(first.status_code, 302)

        second = self._callback()
        self.assertEqual(second.status_code, 302)
        self.assertIn('status=error', second['Location'])
        self.assertIn('reason=invalid_state', second['Location'])

    def test_expired_state_is_rejected(self):
        ComposioLinkState.objects.filter(pk=self.link_state.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        response = self._callback()
        self.assertIn('reason=invalid_state', response['Location'])

    def test_unknown_state_is_rejected(self):
        response = self.client.get(f'{COMPOSIO_URL}/callback/', {'state': 'not-a-real-nonce'})
        self.assertIn('reason=invalid_state', response['Location'])

    def test_missing_state_is_rejected(self):
        response = self.client.get(f'{COMPOSIO_URL}/callback/')
        self.assertIn('reason=invalid_state', response['Location'])

    def test_callback_needs_no_jwt(self):
        """It is reached with no Authorization header and must not 401."""
        response = self.client.get(f'{COMPOSIO_URL}/callback/', {'state': 'unknown'})
        self.assertEqual(response.status_code, 302)

    def test_callback_ignores_a_foreign_jwt_and_resolves_from_the_state_row(self):
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f'Bearer {make_token(self.tenant_b, self.user_b1)}')
        with patch('integrations.views_composio.sync_connection_status') as sync:
            sync.side_effect = lambda c, force=False, actor_user_id=None: c
            response = api.get(f'{COMPOSIO_URL}/callback/', {'state': 'nonce-under-test'})
        self.assertEqual(response.status_code, 302)
        self.connection.refresh_from_db()
        self.assertEqual(str(self.connection.tenant_id), str(self.tenant_a))

    def test_user_cancellation_maps_to_a_ui_readable_reason(self):
        response = self._callback(error='access_denied')
        self.assertIn('status=error', response['Location'])
        self.assertIn('reason=access_denied', response['Location'])
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.status, ComposioConnectionStatusEnum.FAILED)

    def test_open_redirect_in_stored_return_to_is_neutralised(self):
        ComposioLinkState.objects.filter(pk=self.link_state.pk).update(
            return_to='https://evil.example.com/'
        )
        with patch('integrations.views_composio.sync_connection_status',
                   side_effect=lambda c, **kw: c):
            response = self._callback()
        self.assertTrue(response['Location'].startswith(settings.COMPOSIO_FRONTEND_RETURN_URL))
        self.assertNotIn('evil.example.com', response['Location'])


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------

class WebhookSignatureTests(TestCase):
    """HMAC-SHA256 over "{webhook-id}.{webhook-timestamp}.{body}", base64, v1,."""

    SECRET = 'whsec_test_secret'

    def sign(self, webhook_id, timestamp, body, secret=None):
        signed = f'{webhook_id}.{timestamp}.{body}'
        digest = hmac.new((secret or self.SECRET).encode(), signed.encode(), hashlib.sha256).digest()
        return 'v1,' + base64.b64encode(digest).decode()

    def test_valid_signature_passes(self):
        ts = str(int(time.time()))
        body = '{"type":"composio.trigger.message"}'
        self.assertTrue(verify_webhook_signature(
            webhook_id='msg_1', webhook_timestamp=ts, body=body,
            signature=self.sign('msg_1', ts, body), secret=self.SECRET,
        ))

    def test_tampered_body_fails(self):
        ts = str(int(time.time()))
        signature = self.sign('msg_1', ts, '{"a":1}')
        with self.assertRaises(ComposioWebhookVerificationError):
            verify_webhook_signature(webhook_id='msg_1', webhook_timestamp=ts,
                                     body='{"a":2}', signature=signature, secret=self.SECRET)

    def test_wrong_secret_fails(self):
        ts = str(int(time.time()))
        body = '{}'
        signature = self.sign('msg_1', ts, body, secret='another-secret')
        with self.assertRaises(ComposioWebhookVerificationError):
            verify_webhook_signature(webhook_id='msg_1', webhook_timestamp=ts, body=body,
                                     signature=signature, secret=self.SECRET)

    def test_replayed_old_timestamp_is_rejected_even_when_correctly_signed(self):
        old = str(int(time.time()) - 3600)
        body = '{}'
        signature = self.sign('msg_1', old, body)
        with self.assertRaises(ComposioWebhookVerificationError):
            verify_webhook_signature(webhook_id='msg_1', webhook_timestamp=old, body=body,
                                     signature=signature, secret=self.SECRET,
                                     tolerance_seconds=300)

    def test_future_timestamp_is_rejected(self):
        future = str(int(time.time()) + 3600)
        body = '{}'
        signature = self.sign('msg_1', future, body)
        with self.assertRaises(ComposioWebhookVerificationError):
            verify_webhook_signature(webhook_id='msg_1', webhook_timestamp=future, body=body,
                                     signature=signature, secret=self.SECRET,
                                     tolerance_seconds=300)

    def test_blank_secret_rejects_everything(self):
        ts = str(int(time.time()))
        with self.assertRaises(ComposioWebhookVerificationError):
            verify_webhook_signature(webhook_id='msg_1', webhook_timestamp=ts, body='{}',
                                     signature=self.sign('msg_1', ts, '{}'), secret='')

    def test_multiple_rotated_signatures_are_accepted(self):
        ts = str(int(time.time()))
        body = '{}'
        good = self.sign('msg_1', ts, body)
        header = f"{self.sign('msg_1', ts, body, secret='old-secret')} {good}"
        self.assertTrue(verify_webhook_signature(
            webhook_id='msg_1', webhook_timestamp=ts, body=body,
            signature=header, secret=self.SECRET,
        ))


@override_settings(COMPOSIO_WEBHOOK_SECRET='whsec_test_secret')
class WebhookEndpointTests(ComposioTestMixin, TestCase):

    SECRET = 'whsec_test_secret'

    def _post(self, payload, sign=True, timestamp=None, webhook_id='msg_1'):
        body = json.dumps(payload)
        ts = timestamp or str(int(time.time()))
        headers = {'HTTP_WEBHOOK_ID': webhook_id, 'HTTP_WEBHOOK_TIMESTAMP': ts}
        if sign:
            digest = hmac.new(self.SECRET.encode(),
                              f'{webhook_id}.{ts}.{body}'.encode(), hashlib.sha256).digest()
            headers['HTTP_WEBHOOK_SIGNATURE'] = 'v1,' + base64.b64encode(digest).decode()
        return self.client.post(f'{COMPOSIO_URL}/webhook/', data=body,
                                content_type='application/json', **headers)

    def test_unsigned_webhook_is_rejected(self):
        self.assertEqual(self._post({'type': 'x'}, sign=False).status_code, 401)

    def test_signed_webhook_is_accepted_and_audited(self):
        connection = self.make_connection(self.tenant_a, self.user_a1,
                                          connected_account_id='ca_hooked')
        response = self._post({
            'type': 'composio.trigger.message',
            'metadata': {'connected_account_id': 'ca_hooked', 'trigger_slug': 'NOTION_PAGE_ADDED'},
            'data': {'secret': 'should-never-be-stored'},
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(ComposioConnectionEvent.objects.filter(
            connection=connection, event_type='WEBHOOK').exists())

    def test_replayed_webhook_is_rejected(self):
        old = str(int(time.time()) - 4000)
        self.assertEqual(self._post({'type': 'x'}, timestamp=old).status_code, 401)

    @override_settings(COMPOSIO_WEBHOOK_SECRET='')
    def test_unconfigured_secret_rejects_everything(self):
        self.assertEqual(self._post({'type': 'x'}).status_code, 401)


# ---------------------------------------------------------------------------
# Secrets must never be serialized
# ---------------------------------------------------------------------------

@override_settings(COMPOSIO_ENABLED=True, COMPOSIO_API_KEY='super-secret-composio-key')
class NoSecretLeakageTests(ComposioTestMixin, TestCase):

    def test_scrub_strips_every_credential_shape(self):
        payload = {
            'id': 'ca_1',
            'state': {'auth_scheme': 'OAUTH2',
                      'val': {'access_token': 'x', 'refresh_token': 'y'}},
            'auth': {'client_secret': 'z', 'api_key': 'k'},
            'nested': [{'token': 't'}],
        }
        cleaned = scrub(payload)
        as_text = json.dumps(cleaned)
        for secret in ('access_token": "x', 'refresh_token": "y', '"z"', '"k"', '"t"'):
            self.assertNotIn(secret, as_text)
        self.assertEqual(cleaned['state']['auth_scheme'], 'OAUTH2')
        self.assertEqual(cleaned['id'], 'ca_1')

    def test_connection_detail_never_exposes_the_entity_id_metadata_or_the_api_key(self):
        connection = self.make_connection(
            self.tenant_a, self.user_a1,
            metadata={'state': {'val': 'redact-me'}, 'access_token': 'leak'},
        )
        api = client_for(self.tenant_a, self.user_a1)
        response = api.get(f'{COMPOSIO_URL}/connections/{connection.public_id}/')
        self.assertEqual(response.status_code, 200)
        body = json.dumps(response.data, default=str)

        self.assertNotIn('composio_user_id', body)
        self.assertNotIn(connection.composio_user_id, body)
        self.assertNotIn('metadata', body)
        self.assertNotIn('super-secret-composio-key', body)
        self.assertNotIn('access_token', body)
        self.assertNotIn('leak', body)
        self.assertNotIn('connected_account_id', body)

    def test_connection_list_never_exposes_secrets(self):
        self.make_connection(self.tenant_a, self.user_a1)
        api = client_for(self.tenant_a, self.user_a1)
        body = json.dumps(api.get(f'{COMPOSIO_URL}/connections/').data, default=str)
        for forbidden in ('composio_user_id', 'metadata', 'access_token',
                          'super-secret-composio-key'):
            self.assertNotIn(forbidden, body)

    def test_auth_config_response_has_no_credential_fields(self):
        api = client_for(self.tenant_a, self.user_a1, is_admin=True)
        body = json.dumps(api.get(f'{COMPOSIO_URL}/auth-configs/').data, default=str)
        for forbidden in ('client_secret', 'api_key', 'super-secret-composio-key'):
            self.assertNotIn(forbidden, body)

    def test_toolkit_catalogue_has_no_credential_fields(self):
        api = client_for(self.tenant_a, self.user_a1)
        body = json.dumps(api.get(f'{COMPOSIO_URL}/toolkits/').data, default=str)
        for forbidden in ('client_secret', 'api_key', 'super-secret-composio-key'):
            self.assertNotIn(forbidden, body)


# ---------------------------------------------------------------------------
# Not-configured behaviour
# ---------------------------------------------------------------------------

@override_settings(COMPOSIO_ENABLED=False, COMPOSIO_API_KEY='')
class ComposioNotConfiguredTests(ComposioTestMixin, TestCase):

    def test_initiate_returns_424_when_composio_is_switched_off(self):
        api = client_for(self.tenant_a, self.user_a1)
        response = api.post(f'{COMPOSIO_URL}/connections/initiate/',
                            {'toolkit_slug': 'NOTION'}, format='json')
        self.assertEqual(response.status_code, 424)
        self.assertEqual(response.data['code'], 'composio_not_configured')

    def test_catalogue_still_renders_from_the_local_cache(self):
        api = client_for(self.tenant_a, self.user_a1)
        response = api.get(f'{COMPOSIO_URL}/toolkits/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['results'][0]['slug'], 'NOTION')


# ---------------------------------------------------------------------------
# Execute endpoint (ships dark)
# ---------------------------------------------------------------------------

@override_settings(COMPOSIO_ENABLED=True, COMPOSIO_API_KEY='k')
class ComposioExecuteTests(ComposioTestMixin, TestCase):

    def test_execute_is_disabled_by_default(self):
        connection = self.make_connection(self.tenant_a, self.user_a1)
        api = client_for(self.tenant_a, self.user_a1)
        response = api.post(f'{COMPOSIO_URL}/connections/{connection.public_id}/execute/',
                            {'tool_slug': 'NOTION_SEARCH'}, format='json')
        self.assertEqual(response.status_code, 403)

    @override_settings(COMPOSIO_EXECUTE_ENABLED=True)
    def test_tool_outside_the_allowlist_is_rejected(self):
        connection = self.make_connection(self.tenant_a, self.user_a1)
        api = client_for(self.tenant_a, self.user_a1)
        response = api.post(f'{COMPOSIO_URL}/connections/{connection.public_id}/execute/',
                            {'tool_slug': 'NOTION_DELETE_EVERYTHING'}, format='json')
        self.assertEqual(response.status_code, 400)

    @override_settings(COMPOSIO_EXECUTE_ENABLED=True)
    def test_allowlisted_tool_is_executed_with_the_derived_entity_id(self):
        self.auth_config.restrict_to_tools = ['NOTION_SEARCH']
        self.auth_config.save(update_fields=['restrict_to_tools'])
        connection = self.make_connection(self.tenant_a, self.user_a1)

        mock_client = MagicMock()
        mock_client.execute_tool.return_value = {'successful': True, 'data': {'ok': 1}}
        api = client_for(self.tenant_a, self.user_a1)
        with patch('integrations.views_composio.get_composio_client', return_value=mock_client):
            response = api.post(f'{COMPOSIO_URL}/connections/{connection.public_id}/execute/',
                                {'tool_slug': 'NOTION_SEARCH', 'arguments': {}}, format='json')

        self.assertEqual(response.status_code, 200)
        kwargs = mock_client.execute_tool.call_args.kwargs
        self.assertEqual(kwargs['composio_user_id'],
                         build_composio_user_id(self.tenant_a, self.user_a1))
