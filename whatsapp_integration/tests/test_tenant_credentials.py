"""
The tenant's own WhatsApp credential must beat the global one.

Until this landed, `_adapter_from_request` used a single global
WA_VENDOR_UID/WA_API_TOKEN for every tenant, so the Admin Settings screen — the
only self-serve place to configure WhatsApp — had no effect at all, and one
stale value broke WhatsApp for everybody at once. That is exactly what happened.

The tests that matter here are the priority order and the fallbacks: a
credential lookup that is right most of the time is a cross-tenant bug.
"""
import uuid
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings

from whatsapp_integration.models import WhatsAppVendorConfig
from whatsapp_integration.services import realtime
from whatsapp_integration.services.tenant_credentials import (
    TenantCredentialsError,
    fetch_tenant_whatsapp_credentials,
    invalidate_tenant_whatsapp_credentials,
)

TENANT = uuid.UUID('fe81423b-a5bc-41d0-93bf-e311b7b71e1c')

TENANT_CREDS = {'vendor_uid': 'tenant-vendor-uid', 'api_token': 'tenant-token', 'base_url': None}

SETTINGS = dict(SUPERADMIN_URL='https://admin.example.com', MCP_SERVICE_JWT='service-jwt')


class _Resp:
    def __init__(self, payload, status_code=200, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError('not json')
        return self._payload


@override_settings(**SETTINGS)
class FetchTenantCredentialsTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_returns_the_stored_credential(self):
        body = {'vendor_uid': 'v-1', 'api_token': 't-1', 'base_url': None, 'configured': True}
        with patch('whatsapp_integration.services.tenant_credentials.requests.get',
                   return_value=_Resp(body)) as get:
            creds = fetch_tenant_whatsapp_credentials(TENANT)
        self.assertEqual(creds['vendor_uid'], 'v-1')
        self.assertEqual(creds['api_token'], 't-1')
        # Never follow a redirect with the service credential attached.
        self.assertIs(get.call_args.kwargs['allow_redirects'], False)

    def test_an_unconfigured_tenant_is_none_not_an_error(self):
        body = {'vendor_uid': None, 'api_token': None, 'configured': False}
        with patch('whatsapp_integration.services.tenant_credentials.requests.get',
                   return_value=_Resp(body)):
            self.assertIsNone(fetch_tenant_whatsapp_credentials(TENANT))

    def test_an_unconfigured_tenant_is_cached_so_it_does_not_re_hit_superadmin(self):
        body = {'vendor_uid': '', 'api_token': '', 'configured': False}
        with patch('whatsapp_integration.services.tenant_credentials.requests.get',
                   return_value=_Resp(body)) as get:
            fetch_tenant_whatsapp_credentials(TENANT)
            fetch_tenant_whatsapp_credentials(TENANT)
        self.assertEqual(get.call_count, 1, 'the "not configured" answer was not cached')

    def test_a_configured_tenant_is_cached_too(self):
        body = {'vendor_uid': 'v-1', 'api_token': 't-1', 'configured': True}
        with patch('whatsapp_integration.services.tenant_credentials.requests.get',
                   return_value=_Resp(body)) as get:
            fetch_tenant_whatsapp_credentials(TENANT)
            fetch_tenant_whatsapp_credentials(TENANT)
        self.assertEqual(get.call_count, 1)

    def test_invalidate_forces_a_refetch_so_a_rotated_token_takes_effect(self):
        body = {'vendor_uid': 'v-1', 'api_token': 't-1', 'configured': True}
        with patch('whatsapp_integration.services.tenant_credentials.requests.get',
                   return_value=_Resp(body)) as get:
            fetch_tenant_whatsapp_credentials(TENANT)
            invalidate_tenant_whatsapp_credentials(TENANT)
            fetch_tenant_whatsapp_credentials(TENANT)
        self.assertEqual(get.call_count, 2)

    def test_no_tenant_means_no_call_at_all(self):
        with patch('whatsapp_integration.services.tenant_credentials.requests.get') as get:
            self.assertIsNone(fetch_tenant_whatsapp_credentials(None))
        get.assert_not_called()

    def test_a_redirect_is_refused_rather_than_replaying_the_service_jwt(self):
        with patch('whatsapp_integration.services.tenant_credentials.requests.get',
                   return_value=_Resp({}, 302, {'Location': 'https://evil.example/'})):
            with self.assertRaises(TenantCredentialsError):
                fetch_tenant_whatsapp_credentials(TENANT)

    def test_a_404_is_not_configured_not_a_failure(self):
        """An older SuperAdmin without the endpoint must not break WhatsApp."""
        with patch('whatsapp_integration.services.tenant_credentials.requests.get',
                   return_value=_Resp({}, 404)):
            self.assertIsNone(fetch_tenant_whatsapp_credentials(TENANT))

    def test_a_server_error_raises(self):
        with patch('whatsapp_integration.services.tenant_credentials.requests.get',
                   return_value=_Resp({}, 500)):
            with self.assertRaises(TenantCredentialsError):
                fetch_tenant_whatsapp_credentials(TENANT)

    def test_a_failure_is_remembered_so_an_outage_costs_one_timeout_not_thousands(self):
        import requests as _requests
        with patch('whatsapp_integration.services.tenant_credentials.requests.get',
                   side_effect=_requests.ConnectionError('down')) as get:
            for _ in range(5):
                with self.assertRaises(TenantCredentialsError):
                    fetch_tenant_whatsapp_credentials(TENANT)
        self.assertEqual(
            get.call_count, 1,
            'every call re-attempted a dead SuperAdmin; each one costs the caller a '
            'full timeout before it falls back',
        )

    @override_settings(MCP_SERVICE_JWT='')
    def test_no_service_token_fails_closed_rather_than_calling_unauthenticated(self):
        with patch('whatsapp_integration.services.tenant_credentials.requests.get') as get, \
             patch.dict('os.environ', {'DIGICRM_JWT_TOKEN': '', 'MCP_SERVICE_JWT': ''}, clear=False):
            with self.assertRaises(TenantCredentialsError):
                fetch_tenant_whatsapp_credentials(TENANT)
        get.assert_not_called()

    def test_a_forwarded_caller_auth_header_is_used_instead_of_the_service_token(self):
        """The caller's own JWT, forwarded verbatim, wins over MCP_SERVICE_JWT."""
        body = {'vendor_uid': 'v-1', 'api_token': 't-1', 'configured': True}
        with patch('whatsapp_integration.services.tenant_credentials.requests.get',
                   return_value=_Resp(body)) as get:
            fetch_tenant_whatsapp_credentials(TENANT, auth_header='Bearer caller-own-jwt')
        self.assertEqual(get.call_args.kwargs['headers']['Authorization'], 'Bearer caller-own-jwt')

    @override_settings(MCP_SERVICE_JWT='')
    def test_a_forwarded_auth_header_needs_no_service_token_configured_at_all(self):
        """The whole point: this must work with MCP_SERVICE_JWT unset."""
        body = {'vendor_uid': 'v-1', 'api_token': 't-1', 'configured': True}
        with patch('whatsapp_integration.services.tenant_credentials.requests.get',
                   return_value=_Resp(body)) as get, \
             patch.dict('os.environ', {'DIGICRM_JWT_TOKEN': '', 'MCP_SERVICE_JWT': ''}, clear=False):
            creds = fetch_tenant_whatsapp_credentials(TENANT, auth_header='Bearer caller-own-jwt')
        self.assertEqual(creds['vendor_uid'], 'v-1')
        self.assertEqual(get.call_args.kwargs['headers']['Authorization'], 'Bearer caller-own-jwt')

    def test_falls_back_to_the_service_token_when_no_auth_header_is_forwarded(self):
        body = {'vendor_uid': 'v-1', 'api_token': 't-1', 'configured': True}
        with patch('whatsapp_integration.services.tenant_credentials.requests.get',
                   return_value=_Resp(body)) as get:
            fetch_tenant_whatsapp_credentials(TENANT)
        self.assertEqual(get.call_args.kwargs['headers']['Authorization'], 'Bearer service-jwt')

    def test_the_token_never_appears_in_a_cache_key(self):
        from whatsapp_integration.services.tenant_credentials import _cache_key
        self.assertNotIn('tenant-token', _cache_key(str(TENANT)))
        self.assertIn(str(TENANT), _cache_key(str(TENANT)))


@override_settings(**SETTINGS)
class AdapterCredentialPriorityTests(TestCase):
    """The order is the feature."""

    def setUp(self):
        cache.clear()

    def _build(self, headers=None, stored=None, env=('env-vendor', 'env-token'), auth_header=None):
        from whatsapp_integration.views import _adapter_from_request

        class _Req:
            def __init__(self, hdrs, tenant, auth):
                self.headers = hdrs or {}
                self.META = {'HTTP_AUTHORIZATION': auth} if auth else {}
                self.tenant_id = tenant
                self.path = '/api/whatsapp/chat/conversations/'

        def fake_env(key, default=None):
            return {'WA_VENDOR_UID': env[0], 'WA_API_TOKEN': env[1], 'WA_BASE_URL': 'https://gw.example/api'}.get(key, default)

        with patch('whatsapp_integration.views.fetch_tenant_whatsapp_credentials',
                   return_value=stored) as fetch, \
             patch('whatsapp_integration.views.env_config', side_effect=fake_env):
            adapter = _adapter_from_request(_Req(headers, TENANT, auth_header))
            return adapter, fetch

    def test_the_tenant_credential_beats_the_global_env(self):
        adapter, _fetch = self._build(stored=TENANT_CREDS)
        self.assertEqual(adapter.vendor_uid, 'tenant-vendor-uid')
        self.assertEqual(adapter.api_token, 'tenant-token')

    def test_env_is_used_only_when_the_tenant_has_nothing_stored(self):
        adapter, _fetch = self._build(stored=None)
        self.assertEqual(adapter.vendor_uid, 'env-vendor')
        self.assertEqual(adapter.api_token, 'env-token')

    def test_headers_still_win_for_backward_compatibility(self):
        adapter, _fetch = self._build(
            headers={'X-WA-Vendor-Uid': 'hdr-vendor', 'X-WA-Api-Token': 'hdr-token'},
            stored=TENANT_CREDS,
        )
        self.assertEqual(adapter.vendor_uid, 'hdr-vendor')

    def test_the_callers_own_auth_header_is_forwarded_to_the_tenant_lookup(self):
        """This is the whole feature: no MCP_SERVICE_JWT needed, the caller's
        own already-verified token is what SuperAdmin sees."""
        _adapter, fetch = self._build(stored=TENANT_CREDS, auth_header='Bearer caller-own-jwt')
        self.assertEqual(fetch.call_args.kwargs['auth_header'], 'Bearer caller-own-jwt')

    def test_a_superadmin_outage_falls_back_to_env_instead_of_breaking_whatsapp(self):
        from whatsapp_integration.views import _adapter_from_request

        class _Req:
            headers = {}
            META = {}
            tenant_id = TENANT
            path = '/api/whatsapp/chat/conversations/'

        def fake_env(key, default=None):
            return {'WA_VENDOR_UID': 'env-vendor', 'WA_API_TOKEN': 'env-token'}.get(key, default)

        with patch('whatsapp_integration.views.fetch_tenant_whatsapp_credentials',
                   side_effect=TenantCredentialsError('superadmin down')), \
             patch('whatsapp_integration.views.env_config', side_effect=fake_env):
            adapter = _adapter_from_request(_Req())

        self.assertEqual(adapter.vendor_uid, 'env-vendor')


@override_settings(**SETTINGS)
class RealtimeChannelFollowsTheSameCredentialTests(TestCase):
    """
    The realtime channel and the API token must name the SAME vendor account.

    If they diverge, history loads over HTTP from one vendor while the socket
    subscribes to another's channel: messages appear on refresh and never live.
    That reads as a flaky socket, not a misconfiguration, which is why this is
    pinned rather than left to follow by convention.
    """

    def setUp(self):
        cache.clear()
        WhatsAppVendorConfig.objects.create(
            tenant_id=TENANT, vendor_uid='legacy-local-uid', api_token='x', is_active=True,
        )

    def test_the_channel_uses_the_tenant_stored_vendor_uid(self):
        with patch('whatsapp_integration.services.realtime.fetch_tenant_whatsapp_credentials',
                   return_value=TENANT_CREDS):
            self.assertEqual(realtime.resolve_vendor_uid(TENANT), 'tenant-vendor-uid')

    def test_it_falls_back_to_the_local_row_when_nothing_is_stored(self):
        with patch('whatsapp_integration.services.realtime.fetch_tenant_whatsapp_credentials',
                   return_value=None):
            self.assertEqual(realtime.resolve_vendor_uid(TENANT), 'legacy-local-uid')

    def test_a_superadmin_outage_falls_back_to_the_local_row(self):
        with patch('whatsapp_integration.services.realtime.fetch_tenant_whatsapp_credentials',
                   side_effect=TenantCredentialsError('down')):
            self.assertEqual(realtime.resolve_vendor_uid(TENANT), 'legacy-local-uid')

    def test_neither_source_raises_not_configured(self):
        WhatsAppVendorConfig.objects.filter(tenant_id=TENANT).delete()
        with patch('whatsapp_integration.services.realtime.fetch_tenant_whatsapp_credentials',
                   return_value=None):
            with self.assertRaises(realtime.RealtimeNotConfigured):
                realtime.resolve_vendor_uid(TENANT)
