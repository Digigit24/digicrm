"""
Tests for the softphone config endpoint and the tenant-wide default extension.

These build every row they need inside the test database and never read or
write live tenant data.
"""
import uuid
from unittest.mock import patch

import jwt as pyjwt
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from integrations.utils.encryption import encrypt_token
from telephony.models import TeleCMICredential, TeleCMIAgent, SBCRegionEnum
from telephony.services.crypto import encrypt_secret
from telephony.services.softphone_service import (
    REASON_NO_AGENT, REASON_TENANT_NOT_CONFIGURED,
)

TEST_JWT_SECRET = 'test-jwt-secret-telephony-unit-tests'
TEST_JWT_ALGO = 'HS256'

TENANT = uuid.UUID('11111111-1111-1111-1111-111111111111')
OTHER_TENANT = uuid.UUID('22222222-2222-2222-2222-222222222222')
USER = uuid.UUID('33333333-3333-3333-3333-333333333333')

URL = '/api/telephony/webrtc-config/'

TENANT_EXT = '103_9990001'
TENANT_PASSWORD = 'shared-ext-pw'
USER_EXT = '103_9990002'
USER_PASSWORD = 'personal-ext-pw'


def _authed_client(tenant_id=TENANT, user_id=USER):
    payload = {
        'user_id': str(user_id),
        'email': 'test@example.com',
        'tenant_id': str(tenant_id),
        'tenant_slug': 'test-tenant',
        'is_super_admin': False,
        'permissions': {
            'telephony': {
                'calls': {'view': 'all', 'create': True, 'edit': True},
                'settings': {'view': 'all', 'create': True, 'edit': True, 'delete': True},
                'agents': {'view': 'all', 'create': True, 'edit': True, 'delete': True},
            },
        },
        'enabled_modules': ['telephony'],
    }
    token = pyjwt.encode(payload, TEST_JWT_SECRET, algorithm=TEST_JWT_ALGO)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
    return client


def _make_credential(tenant_id=TENANT, with_default_agent=True):
    """Create a tenant credential, optionally with a shared extension."""
    secret_encrypted, dek_wrapped = encrypt_secret('app-secret')
    kwargs = {}
    if with_default_agent:
        pw_encrypted, dek_wrapped = encrypt_secret(TENANT_PASSWORD, dek_wrapped)
        kwargs = {
            'default_agent_id': TENANT_EXT,
            'default_agent_password_encrypted': pw_encrypted,
        }
    return TeleCMICredential.objects.create(
        tenant_id=tenant_id,
        app_id='app-123',
        secret_encrypted=secret_encrypted,
        dek_wrapped=dek_wrapped,
        sbc_region=SBCRegionEnum.INDIA,
        is_active=True,
        **kwargs,
    )


@override_settings(JWT_SECRET_KEY=TEST_JWT_SECRET, JWT_ALGORITHM=TEST_JWT_ALGO)
class WebRTCConfigResolutionTest(TestCase):
    """The three-way resolution order and its two distinct 424 reasons."""

    def test_tenant_default_resolves_when_user_has_no_agent_row(self):
        _make_credential()
        self.assertEqual(TeleCMIAgent.objects.filter(tenant_id=TENANT).count(), 0)

        response = _authed_client().get(URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['telecmi_user_id'], TENANT_EXT)
        self.assertEqual(response.data['source'], 'tenant')
        self.assertEqual(response.data['sbc_host'], 'sbcind.telecmi.com')
        self.assertIsNone(response.data['default_caller_id'])
        self.assertEqual(response.data['auth']['kind'], 'password')
        self.assertEqual(response.data['auth']['value'], TENANT_PASSWORD)

    def test_per_user_agent_still_wins_over_tenant_default(self):
        _make_credential()
        TeleCMIAgent.objects.create(
            tenant_id=TENANT, user_id=USER,
            telecmi_user_id=USER_EXT,
            password_encrypted=encrypt_token(USER_PASSWORD),
            is_active=True,
        )

        response = _authed_client().get(URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['telecmi_user_id'], USER_EXT)
        self.assertEqual(response.data['source'], 'user')
        self.assertEqual(response.data['auth']['value'], USER_PASSWORD)

    def test_inactive_user_agent_falls_back_to_tenant_default(self):
        _make_credential()
        TeleCMIAgent.objects.create(
            tenant_id=TENANT, user_id=USER,
            telecmi_user_id=USER_EXT,
            password_encrypted=encrypt_token(USER_PASSWORD),
            is_active=False,
        )

        response = _authed_client().get(URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['source'], 'tenant')
        self.assertEqual(response.data['telecmi_user_id'], TENANT_EXT)

    def test_424_tenant_not_configured_when_no_credential(self):
        response = _authed_client().get(URL)

        self.assertEqual(response.status_code, 424)
        self.assertEqual(response.data['reason'], REASON_TENANT_NOT_CONFIGURED)

    def test_424_no_agent_is_a_distinct_reason(self):
        _make_credential(with_default_agent=False)

        response = _authed_client().get(URL)

        self.assertEqual(response.status_code, 424)
        self.assertEqual(response.data['reason'], REASON_NO_AGENT)
        # The two failures must not be confusable — that ambiguity is what
        # made the original bug expensive to diagnose.
        self.assertNotEqual(response.data['reason'], REASON_TENANT_NOT_CONFIGURED)
        self.assertIn('Settings', response.data['error'])

    def test_tenant_default_does_not_leak_across_tenants(self):
        _make_credential(tenant_id=OTHER_TENANT)

        response = _authed_client(tenant_id=TENANT).get(URL)

        self.assertEqual(response.status_code, 424)
        self.assertEqual(response.data['reason'], REASON_TENANT_NOT_CONFIGURED)

    def test_extension_id_without_password_is_not_usable(self):
        cred = _make_credential(with_default_agent=False)
        cred.default_agent_id = TENANT_EXT
        cred.save(update_fields=['default_agent_id'])

        response = _authed_client().get(URL)

        self.assertEqual(response.status_code, 424)
        self.assertEqual(response.data['reason'], REASON_NO_AGENT)


@override_settings(JWT_SECRET_KEY=TEST_JWT_SECRET, JWT_ALGORITHM=TEST_JWT_ALGO)
class CredentialSecretExposureTest(TestCase):
    """The app secret must never come back out of the credential API."""

    def test_credential_api_never_serializes_secret_or_password(self):
        _make_credential()

        response = _authed_client().get('/api/telephony/credentials/')

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertNotIn('app-secret', body)
        self.assertNotIn(TENANT_PASSWORD, body)
        self.assertNotIn('secret_encrypted', body)
        self.assertNotIn('default_agent_password_encrypted', body)
        self.assertNotIn('dek_wrapped', body)

    def test_credential_api_reports_configured_flags_without_values(self):
        _make_credential()

        response = _authed_client().get('/api/telephony/credentials/')

        row = response.data['results'][0] if 'results' in response.data else response.data[0]
        self.assertTrue(row['default_agent_configured'])
        self.assertEqual(row['default_agent_id'], TENANT_EXT)
        self.assertNotIn('default_agent_password', row)


@override_settings(JWT_SECRET_KEY=TEST_JWT_SECRET, JWT_ALGORITHM=TEST_JWT_ALGO)
class SaveThenFetchTest(TestCase):
    """A just-saved credential must be live on the next request."""

    @patch('telephony.services.telecmi_client.get_user_login_token')
    def test_save_then_fetch_returns_new_value_without_restart(self, mock_login):
        mock_login.return_value = 'verify-tok'
        client = _authed_client()

        create = client.post(
            '/api/telephony/credentials/',
            {
                'app_id': 'app-123',
                'secret': 'app-secret',
                'sbc_region': 'ind',
                'default_agent_id': TENANT_EXT,
                'default_agent_password': TENANT_PASSWORD,
            },
            format='json',
        )
        self.assertEqual(create.status_code, 201, create.data)

        # Same process, no re-login, no cache warm-up.
        fetched = client.get(URL)
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.data['telecmi_user_id'], TENANT_EXT)
        self.assertEqual(fetched.data['auth']['value'], TENANT_PASSWORD)

        # And an immediate correction is reflected just as fast.
        cred = TeleCMICredential.objects.get(tenant_id=TENANT)
        patched = client.patch(
            f'/api/telephony/credentials/{cred.id}/',
            {'default_agent_id': '103_9998888', 'default_agent_password': 'corrected-pw'},
            format='json',
        )
        self.assertEqual(patched.status_code, 200, patched.data)

        refetched = client.get(URL)
        self.assertEqual(refetched.data['telecmi_user_id'], '103_9998888')
        self.assertEqual(refetched.data['auth']['value'], 'corrected-pw')

    @patch('telephony.services.telecmi_client.get_user_login_token')
    def test_changing_default_extension_invalidates_cached_token(self, mock_login):
        mock_login.return_value = 'verify-tok'
        cred = _make_credential()
        cred.default_agent_token = 'stale-token'
        cred.default_agent_token_obtained_at = timezone.now()
        cred.save(update_fields=['default_agent_token', 'default_agent_token_obtained_at'])
        self.assertFalse(cred.is_default_token_stale())

        response = _authed_client().patch(
            f'/api/telephony/credentials/{cred.id}/',
            {'default_agent_password': 'new-pw'},
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)

        cred.refresh_from_db()
        self.assertIsNone(cred.default_agent_token)
        self.assertTrue(cred.is_default_token_stale())

    def test_changing_agent_password_invalidates_its_cached_token(self):
        _make_credential()
        agent = TeleCMIAgent.objects.create(
            tenant_id=TENANT, user_id=USER,
            telecmi_user_id=USER_EXT,
            password_encrypted=encrypt_token(USER_PASSWORD),
            cached_token='stale-token',
            token_obtained_at=timezone.now(),
            is_active=True,
        )
        self.assertFalse(agent.is_token_stale())

        response = _authed_client().patch(
            f'/api/telephony/agents/{agent.id}/',
            {'password': 'new-pw'},
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)

        agent.refresh_from_db()
        self.assertIsNone(agent.cached_token)
        self.assertTrue(agent.is_token_stale())


@override_settings(JWT_SECRET_KEY=TEST_JWT_SECRET, JWT_ALGORITHM=TEST_JWT_ALGO)
class DefaultAgentValidationTest(TestCase):
    """Save-time verification against TeleCMI (requirement E)."""

    @patch('telephony.services.telecmi_client.get_user_login_token')
    def test_rejected_extension_is_a_400_not_a_silent_save(self, mock_login):
        from telephony.services.telecmi_client import TeleCMIError
        mock_login.side_effect = TeleCMIError('Invalid credentials', status_code=401)

        response = _authed_client().post(
            '/api/telephony/credentials/',
            {
                'app_id': 'app-123', 'secret': 'app-secret', 'sbc_region': 'ind',
                'default_agent_id': TENANT_EXT,
                'default_agent_password': 'wrong-pw',
            },
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('default_agent_password', response.data)
        self.assertFalse(TeleCMICredential.objects.filter(tenant_id=TENANT).exists())

    @patch('telephony.services.telecmi_client.get_user_login_token')
    def test_telecmi_unreachable_stores_but_flags(self, mock_login):
        from telephony.services.telecmi_client import TeleCMIError
        # status_code=None is what telecmi_client raises for network failure.
        mock_login.side_effect = TeleCMIError('Network error calling TeleCMI')

        response = _authed_client().post(
            '/api/telephony/credentials/',
            {
                'app_id': 'app-123', 'secret': 'app-secret', 'sbc_region': 'ind',
                'default_agent_id': TENANT_EXT,
                'default_agent_password': TENANT_PASSWORD,
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201, response.data)
        cred = TeleCMICredential.objects.get(tenant_id=TENANT)
        self.assertIsNone(cred.default_agent_verified_at)
        self.assertIn('unreachable', cred.default_agent_verify_error)
        # Stored and usable despite being unverified.
        self.assertTrue(cred.has_default_agent)

    @patch('telephony.services.telecmi_client.get_user_login_token')
    def test_successful_verification_records_timestamp(self, mock_login):
        mock_login.return_value = 'tok'

        response = _authed_client().post(
            '/api/telephony/credentials/',
            {
                'app_id': 'app-123', 'secret': 'app-secret', 'sbc_region': 'ind',
                'default_agent_id': TENANT_EXT,
                'default_agent_password': TENANT_PASSWORD,
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201, response.data)
        cred = TeleCMICredential.objects.get(tenant_id=TENANT)
        self.assertIsNotNone(cred.default_agent_verified_at)
        self.assertEqual(cred.default_agent_verify_error, '')
        mock_login.assert_called_once_with(TENANT_EXT, TENANT_PASSWORD)

    def test_password_without_extension_is_rejected(self):
        response = _authed_client().post(
            '/api/telephony/credentials/',
            {
                'app_id': 'app-123', 'secret': 'app-secret', 'sbc_region': 'ind',
                'default_agent_password': TENANT_PASSWORD,
            },
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('default_agent_id', response.data)

    @patch('telephony.services.telecmi_client.get_user_login_token')
    def test_encrypted_at_rest_never_plaintext(self, mock_login):
        mock_login.return_value = 'tok'

        _authed_client().post(
            '/api/telephony/credentials/',
            {
                'app_id': 'app-123', 'secret': 'app-secret', 'sbc_region': 'ind',
                'default_agent_id': TENANT_EXT,
                'default_agent_password': TENANT_PASSWORD,
            },
            format='json',
        )

        cred = TeleCMICredential.objects.get(tenant_id=TENANT)
        self.assertNotIn(TENANT_PASSWORD, cred.default_agent_password_encrypted)
        self.assertTrue(cred.dek_wrapped, 'must use envelope encryption, not plaintext')
        # Both columns share this tenant's single DEK.
        from telephony.services.crypto import decrypt_secret, decrypt_default_agent_password
        self.assertEqual(decrypt_secret(cred), 'app-secret')
        self.assertEqual(decrypt_default_agent_password(cred), TENANT_PASSWORD)


class TenantDefaultTokenServiceTest(TestCase):
    """get_agent_token falls back to the shared extension, so REST calls work."""

    @patch('telephony.services.token_service.get_user_login_token')
    def test_falls_back_to_tenant_default_when_no_agent_row(self, mock_login):
        from telephony.services.token_service import get_agent_token
        mock_login.return_value = 'tenant-tok'
        _make_credential()

        self.assertEqual(get_agent_token(TENANT, USER), 'tenant-tok')
        mock_login.assert_called_once_with(TENANT_EXT, TENANT_PASSWORD)

        # Second call is served from the cache on the credential row.
        self.assertEqual(get_agent_token(TENANT, USER), 'tenant-tok')
        self.assertEqual(mock_login.call_count, 1)

    def test_error_when_neither_agent_nor_tenant_default(self):
        from telephony.services.token_service import get_agent_token, TokenServiceError
        _make_credential(with_default_agent=False)

        with self.assertRaises(TokenServiceError) as ctx:
            get_agent_token(TENANT, USER)
        self.assertIn('default extension', str(ctx.exception))
