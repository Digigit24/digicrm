"""
Tests for telephony API views.

Uses real JWT tokens (signed with a test secret) to pass the JWT middleware,
exactly as the rest of the CRM test suite should.
"""
import uuid
import jwt as pyjwt
from unittest.mock import patch, MagicMock
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from telephony.models import (
    TeleCMICredential, TeleCMIAgent, CallLog, SMSLog,
    SBCRegionEnum, CallDirectionEnum, CallTypeEnum, SMSStatusEnum,
)

# ── Test constants ──────────────────────────────────────────
TEST_JWT_SECRET = 'test-jwt-secret-telephony-unit-tests'
TEST_JWT_ALGO = 'HS256'

TENANT_A = uuid.UUID('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
TENANT_B = uuid.UUID('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb')
USER_A = uuid.UUID('cccccccc-cccc-cccc-cccc-cccccccccccc')
USER_B = uuid.UUID('dddddddd-dddd-dddd-dddd-dddddddddddd')


def _make_jwt(tenant_id, user_id, is_super_admin=False):
    """Return a Bearer token string for the given tenant/user."""
    payload = {
        'user_id': str(user_id),
        'email': 'test@example.com',
        'tenant_id': str(tenant_id),
        'tenant_slug': 'test-tenant',
        'is_super_admin': is_super_admin,
        'permissions': {'crm': {'leads': {'view': 'all', 'create': True}}},
        'enabled_modules': ['crm'],
    }
    token = pyjwt.encode(payload, TEST_JWT_SECRET, algorithm=TEST_JWT_ALGO)
    return f'Bearer {token}'


def _authed_client(tenant_id, user_id, is_super_admin=False):
    """Return APIClient with a valid JWT Authorization header."""
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=_make_jwt(tenant_id, user_id, is_super_admin))
    return client


# ── Override JWT settings for all view tests ────────────────
@override_settings(JWT_SECRET_KEY=TEST_JWT_SECRET, JWT_ALGORITHM=TEST_JWT_ALGO)
class TenantIsolationModelTest(TestCase):
    """Pure model-level tenant isolation (no HTTP)."""

    def setUp(self):
        from django.utils import timezone
        now = timezone.now()
        CallLog.objects.create(
            tenant_id=TENANT_A, cmiuid='a-001',
            direction=CallDirectionEnum.INBOUND, call_type=CallTypeEnum.ANSWERED,
            from_number='9190000001', to_number='9180000001',
            duration=30, call_time=now,
        )
        CallLog.objects.create(
            tenant_id=TENANT_B, cmiuid='b-001',
            direction=CallDirectionEnum.OUTBOUND, call_type=CallTypeEnum.MISSED,
            from_number='9190000002', to_number='9180000002',
            duration=0, call_time=now,
        )

    def test_tenant_a_only_sees_own_logs(self):
        qs = CallLog.objects.filter(tenant_id=TENANT_A)
        self.assertEqual(qs.count(), 1)
        self.assertEqual(qs.first().cmiuid, 'a-001')

    def test_tenant_b_only_sees_own_logs(self):
        qs = CallLog.objects.filter(tenant_id=TENANT_B)
        self.assertEqual(qs.count(), 1)
        self.assertEqual(qs.first().cmiuid, 'b-001')

    def test_cross_tenant_cmiuid_not_shared(self):
        self.assertFalse(
            CallLog.objects.filter(tenant_id=TENANT_A, cmiuid='b-001').exists()
        )


@override_settings(JWT_SECRET_KEY=TEST_JWT_SECRET, JWT_ALGORITHM=TEST_JWT_ALGO)
class ClickToCallViewTest(TestCase):

    @patch('telephony.views.client.click_to_call')
    @patch('telephony.views.get_agent_token')
    def test_click_to_call_success(self, mock_token, mock_call):
        mock_token.return_value = 'valid-tok'
        mock_call.return_value = {'code': 200, 'msg': 'Call initiated', 'request_id': 'req1'}

        client = _authed_client(TENANT_A, USER_A)
        response = client.post(
            '/api/telephony/calls/click-to-call/',
            {'to_number': '919000000000'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['code'], 200)

    @patch('telephony.views.client.click_to_call')
    @patch('telephony.views.get_agent_token')
    def test_click_to_call_passes_lead_id_as_extra_param(self, mock_token, mock_call):
        mock_token.return_value = 'tok'
        mock_call.return_value = {'code': 200, 'msg': 'Call initiated', 'request_id': 'r'}

        client = _authed_client(TENANT_A, USER_A)
        client.post(
            '/api/telephony/calls/click-to-call/',
            {'to_number': '919000000000', 'lead_id': 42},
            format='json',
        )
        call_kwargs = mock_call.call_args[1]
        self.assertEqual(call_kwargs.get('extra_params', {}).get('lead_id'), '42')

    def test_click_to_call_missing_to_number(self):
        api_client = _authed_client(TENANT_A, USER_A)
        response = api_client.post(
            '/api/telephony/calls/click-to-call/', {}, format='json'
        )
        self.assertEqual(response.status_code, 400)

    @patch('telephony.views.get_agent_token')
    def test_click_to_call_no_agent_config(self, mock_token):
        from telephony.services.token_service import TokenServiceError
        mock_token.side_effect = TokenServiceError('No agent configured')

        api_client = _authed_client(TENANT_A, USER_A)
        response = api_client.post(
            '/api/telephony/calls/click-to-call/',
            {'to_number': '919000000000'},
            format='json',
        )
        self.assertEqual(response.status_code, 424)

    @patch('telephony.views.client.click_to_call')
    @patch('telephony.views.get_agent_token')
    def test_click_to_call_telecmi_gateway_error(self, mock_token, mock_call):
        from telephony.services.telecmi_client import TeleCMIError
        mock_token.return_value = 'tok'
        mock_call.side_effect = TeleCMIError('API error', status_code=500)

        api_client = _authed_client(TENANT_A, USER_A)
        response = api_client.post(
            '/api/telephony/calls/click-to-call/',
            {'to_number': '919000000000'},
            format='json',
        )
        self.assertEqual(response.status_code, 502)


@override_settings(JWT_SECRET_KEY=TEST_JWT_SECRET, JWT_ALGORITHM=TEST_JWT_ALGO)
class SMSSendViewTest(TestCase):

    @patch('telephony.views.client.send_sms')
    @patch('telephony.views.get_agent_token')
    def test_send_sms_success(self, mock_token, mock_sms):
        mock_token.return_value = 'tok'
        mock_sms.return_value = {'code': 200, 'msg': 'sent'}

        api_client = _authed_client(TENANT_A, USER_A)
        response = api_client.post(
            '/api/telephony/sms/send/',
            {'to_number': '919000000000', 'message': 'Hello'},
            format='json',
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            SMSLog.objects.filter(to_number='919000000000', tenant_id=TENANT_A).exists()
        )

    @patch('telephony.views.client.send_sms')
    @patch('telephony.views.get_agent_token')
    def test_send_sms_telecmi_failure_logged(self, mock_token, mock_sms):
        from telephony.services.telecmi_client import TeleCMIError
        mock_token.return_value = 'tok'
        mock_sms.side_effect = TeleCMIError('API error', 400)

        api_client = _authed_client(TENANT_A, USER_A)
        response = api_client.post(
            '/api/telephony/sms/send/',
            {'to_number': '919000000000', 'message': 'Hello'},
            format='json',
        )
        self.assertEqual(response.status_code, 502)
        log = SMSLog.objects.filter(to_number='919000000000', tenant_id=TENANT_A).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.status, SMSStatusEnum.FAILED)

    def test_send_sms_missing_message(self):
        api_client = _authed_client(TENANT_A, USER_A)
        response = api_client.post(
            '/api/telephony/sms/send/',
            {'to_number': '919000000000'},
            format='json',
        )
        self.assertEqual(response.status_code, 400)

    @patch('telephony.views.client.send_sms')
    @patch('telephony.views.get_agent_token')
    def test_sms_tenant_isolation(self, mock_token, mock_sms):
        """SMS logs from tenant A must not appear for tenant B."""
        mock_token.return_value = 'tok'
        mock_sms.return_value = {'code': 200, 'msg': 'sent'}

        _authed_client(TENANT_A, USER_A).post(
            '/api/telephony/sms/send/',
            {'to_number': '919000000001', 'message': 'Hi from A'},
            format='json',
        )
        self.assertFalse(
            SMSLog.objects.filter(to_number='919000000001', tenant_id=TENANT_B).exists()
        )


@override_settings(JWT_SECRET_KEY=TEST_JWT_SECRET, JWT_ALGORITHM=TEST_JWT_ALGO)
class CDRWebhookViewTest(TestCase):
    """Webhook tests — public endpoints, no JWT needed."""

    def _cdr_payload(self, cmiuid='wh-001', duration=30):
        return {
            'cmiuid': cmiuid,
            'duration': duration,
            'billedsec': 25,
            'rate': 0.01,
            'name': 'Test',
            'from': 919000000001,
            'to': 918000000001,
            'time': 1639554230000,
        }

    def test_webhook_requires_tenant_id(self):
        # No tenant_id → 400
        response = APIClient().post(
            '/api/telephony/webhook/cdr/',
            self._cdr_payload(),
            format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_webhook_creates_call_log(self):
        response = APIClient().post(
            f'/api/telephony/webhook/cdr/?tenant_id={TENANT_A}',
            self._cdr_payload(),
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(CallLog.objects.filter(tenant_id=TENANT_A, cmiuid='wh-001').exists())

    def test_webhook_idempotent(self):
        payload = self._cdr_payload()
        url = f'/api/telephony/webhook/cdr/?tenant_id={TENANT_A}'
        APIClient().post(url, payload, format='json')
        APIClient().post(url, payload, format='json')
        self.assertEqual(
            CallLog.objects.filter(tenant_id=TENANT_A, cmiuid='wh-001').count(), 1
        )

    def test_webhook_tenant_isolation(self):
        APIClient().post(
            f'/api/telephony/webhook/cdr/?tenant_id={TENANT_A}',
            self._cdr_payload('wh-a'),
            format='json',
        )
        APIClient().post(
            f'/api/telephony/webhook/cdr/?tenant_id={TENANT_B}',
            self._cdr_payload('wh-b'),
            format='json',
        )
        self.assertTrue(CallLog.objects.filter(tenant_id=TENANT_A, cmiuid='wh-a').exists())
        self.assertTrue(CallLog.objects.filter(tenant_id=TENANT_B, cmiuid='wh-b').exists())
        self.assertFalse(CallLog.objects.filter(tenant_id=TENANT_A, cmiuid='wh-b').exists())

    def test_webhook_secret_rejection(self):
        TeleCMICredential.objects.create(
            tenant_id=TENANT_A,
            app_id='app1',
            secret_encrypted='enc',
            sbc_region=SBCRegionEnum.INDIA,
            webhook_secret='mysecret',
        )
        response = APIClient().post(
            f'/api/telephony/webhook/cdr/?tenant_id={TENANT_A}',
            self._cdr_payload('wh-sec'),
            format='json',
            HTTP_X_WEBHOOK_SECRET='wrongsecret',
        )
        self.assertEqual(response.status_code, 401)

    def test_webhook_secret_accepted(self):
        TeleCMICredential.objects.create(
            tenant_id=TENANT_A,
            app_id='app1',
            secret_encrypted='enc',
            sbc_region=SBCRegionEnum.INDIA,
            webhook_secret='mysecret',
        )
        response = APIClient().post(
            f'/api/telephony/webhook/cdr/?tenant_id={TENANT_A}',
            self._cdr_payload('wh-ok'),
            format='json',
            HTTP_X_WEBHOOK_SECRET='mysecret',
        )
        self.assertEqual(response.status_code, 200)


@override_settings(JWT_SECRET_KEY=TEST_JWT_SECRET, JWT_ALGORITHM=TEST_JWT_ALGO)
class LiveEventWebhookViewTest(TestCase):

    def test_live_event_returns_ok(self):
        response = APIClient().post(
            f'/api/telephony/webhook/live/?tenant_id={TENANT_A}',
            {'event': 'ringing', 'from': '919000000000'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'ok')


@override_settings(JWT_SECRET_KEY=TEST_JWT_SECRET, JWT_ALGORITHM=TEST_JWT_ALGO)
class CallLogViewSetTest(TestCase):

    def setUp(self):
        from django.utils import timezone
        now = timezone.now()
        CallLog.objects.create(
            tenant_id=TENANT_A, cmiuid='list-a-001',
            direction=CallDirectionEnum.INBOUND, call_type=CallTypeEnum.ANSWERED,
            from_number='9190000010', to_number='9180000010',
            duration=60, call_time=now,
        )
        CallLog.objects.create(
            tenant_id=TENANT_B, cmiuid='list-b-001',
            direction=CallDirectionEnum.OUTBOUND, call_type=CallTypeEnum.MISSED,
            from_number='9190000011', to_number='9180000011',
            duration=0, call_time=now,
        )

    def test_list_returns_only_tenant_a_logs(self):
        api_client = _authed_client(TENANT_A, USER_A)
        response = api_client.get('/api/telephony/calls/')
        self.assertEqual(response.status_code, 200)
        # Only TENANT_A's log should appear
        cmis = [r['cmiuid'] for r in response.data['results']]
        self.assertIn('list-a-001', cmis)
        self.assertNotIn('list-b-001', cmis)

    def test_list_returns_only_tenant_b_logs(self):
        api_client = _authed_client(TENANT_B, USER_B)
        response = api_client.get('/api/telephony/calls/')
        self.assertEqual(response.status_code, 200)
        cmis = [r['cmiuid'] for r in response.data['results']]
        self.assertIn('list-b-001', cmis)
        self.assertNotIn('list-a-001', cmis)


@override_settings(JWT_SECRET_KEY=TEST_JWT_SECRET, JWT_ALGORITHM=TEST_JWT_ALGO)
class TeleCMICredentialViewSetTest(TestCase):

    def test_list_credentials_empty(self):
        api_client = _authed_client(TENANT_A, USER_A)
        response = api_client.get('/api/telephony/credentials/')
        self.assertEqual(response.status_code, 200)

    def test_unauthenticated_rejected(self):
        response = APIClient().get('/api/telephony/credentials/')
        self.assertEqual(response.status_code, 401)
