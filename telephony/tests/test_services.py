"""Tests for telephony service layer (client, token, call_log, callback)."""
import uuid
from unittest.mock import patch, MagicMock, PropertyMock
from django.test import TestCase
from django.utils import timezone

from telephony.models import (
    TeleCMICredential, TeleCMIAgent, CallLog,
    CallDirectionEnum, CallTypeEnum, SBCRegionEnum,
)
from telephony.services.telecmi_client import TeleCMIError
from telephony.services.token_service import (
    get_agent_token, invalidate_token, get_tenant_credential, TokenServiceError,
)
from telephony.services.call_log_service import process_cdr_record, _find_lead_id, _format_duration
from telephony.services.callback_service import create_callback_task_if_needed

TENANT_ID = uuid.UUID('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
USER_ID = uuid.UUID('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb')


# ──────────────────────────────────────────────
# telecmi_client tests
# ──────────────────────────────────────────────

class TeleCMIClientTest(TestCase):

    @patch('telephony.services.telecmi_client.requests.post')
    def test_click_to_call_success(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {'code': 200, 'msg': 'Call initiated', 'request_id': 'abc123'},
        )
        from telephony.services.telecmi_client import click_to_call
        result = click_to_call('token-abc', '919000000000')
        self.assertEqual(result['code'], 200)
        self.assertEqual(result['request_id'], 'abc123')
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        self.assertIn('click2call', call_kwargs[0][0])

    @patch('telephony.services.telecmi_client.requests.post')
    def test_click_to_call_with_caller_id_and_extra(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {'code': 200, 'msg': 'Call initiated', 'request_id': 'x'},
        )
        from telephony.services.telecmi_client import click_to_call
        click_to_call('tok', '9190000', caller_id='18000000', extra_params={'crm': 'true'})
        payload = mock_post.call_args[1]['json']
        self.assertEqual(payload['callerid'], '18000000')
        self.assertEqual(payload['extra_params'], {'crm': 'true'})

    @patch('telephony.services.telecmi_client.requests.post')
    def test_api_error_raises_telecmi_error(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=404,
            json=lambda: {'code': 404, 'msg': 'Invalid user token'},
        )
        from telephony.services.telecmi_client import click_to_call
        with self.assertRaises(TeleCMIError) as ctx:
            click_to_call('bad-token', '919000000000')
        self.assertEqual(ctx.exception.status_code, 404)

    @patch('telephony.services.telecmi_client.requests.post')
    def test_network_error_raises_telecmi_error(self, mock_post):
        import requests as req
        mock_post.side_effect = req.ConnectionError('connection refused')
        from telephony.services.telecmi_client import click_to_call
        with self.assertRaises(TeleCMIError):
            click_to_call('tok', '919000000000')

    @patch('telephony.services.telecmi_client.requests.post')
    def test_send_sms(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {'code': 200, 'msg': 'sent'},
        )
        from telephony.services.telecmi_client import send_sms
        result = send_sms('tok', '919000000000', 'Hello test')
        payload = mock_post.call_args[1]['json']
        self.assertEqual(payload['to'], '919000000000')
        self.assertEqual(payload['text'], 'Hello test')

    @patch('telephony.services.telecmi_client.requests.post')
    def test_get_incoming_cdr(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {'code': 200, 'count': 1, 'cdr': [{'cmiuid': 'x'}]},
        )
        from telephony.services.telecmi_client import get_incoming_cdr
        result = get_incoming_cdr('tok', 1, 1000, 2000)
        self.assertEqual(result['count'], 1)
        payload = mock_post.call_args[1]['json']
        self.assertEqual(payload['type'], 1)
        self.assertIn('/user/in_cdr', mock_post.call_args[0][0])

    @patch('telephony.services.telecmi_client.requests.post')
    def test_get_user_login_token(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {'code': 200, 'token': 'my-fresh-token'},
        )
        from telephony.services.telecmi_client import get_user_login_token
        token = get_user_login_token('103_1111112', 'pass123')
        self.assertEqual(token, 'my-fresh-token')
        payload = mock_post.call_args[1]['json']
        self.assertEqual(payload['id'], '103_1111112')

    @patch('telephony.services.telecmi_client.requests.post')
    def test_get_user_login_token_missing_token_field(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {'code': 200},  # no 'token' key
        )
        from telephony.services.telecmi_client import get_user_login_token
        with self.assertRaises(TeleCMIError):
            get_user_login_token('103_1111112', 'pass123')


# ──────────────────────────────────────────────
# token_service tests
# ──────────────────────────────────────────────

class TokenServiceTest(TestCase):

    def _make_agent(self, token=None, hours_old=0):
        from integrations.utils.encryption import encrypt_token
        agent = TeleCMIAgent.objects.create(
            tenant_id=TENANT_ID,
            user_id=USER_ID,
            telecmi_user_id='103_1111112',
            password_encrypted=encrypt_token('pass123'),
            cached_token=token,
            token_obtained_at=timezone.now() - timezone.timedelta(hours=hours_old) if token else None,
        )
        return agent

    def test_no_agent_raises_error(self):
        with self.assertRaises(TokenServiceError):
            get_agent_token(TENANT_ID, USER_ID)

    def test_returns_cached_fresh_token(self):
        self._make_agent(token='cached-tok', hours_old=1)
        result = get_agent_token(TENANT_ID, USER_ID)
        self.assertEqual(result, 'cached-tok')

    @patch('telephony.services.token_service.get_user_login_token')
    def test_refreshes_stale_token(self, mock_login):
        mock_login.return_value = 'new-token-xyz'
        self._make_agent(token='old-tok', hours_old=21)

        result = get_agent_token(TENANT_ID, USER_ID)
        self.assertEqual(result, 'new-token-xyz')
        mock_login.assert_called_once()

        # Token saved to DB
        agent = TeleCMIAgent.objects.get(tenant_id=TENANT_ID, user_id=USER_ID)
        self.assertEqual(agent.cached_token, 'new-token-xyz')

    @patch('telephony.services.token_service.get_user_login_token')
    def test_login_failure_raises_token_service_error(self, mock_login):
        mock_login.side_effect = TeleCMIError('Invalid credentials', status_code=401)
        self._make_agent(token='old-tok', hours_old=21)
        with self.assertRaises(TokenServiceError):
            get_agent_token(TENANT_ID, USER_ID)

    def test_invalidate_clears_token(self):
        self._make_agent(token='tok', hours_old=1)
        invalidate_token(TENANT_ID, USER_ID)
        agent = TeleCMIAgent.objects.get(tenant_id=TENANT_ID, user_id=USER_ID)
        self.assertIsNone(agent.cached_token)

    def test_get_tenant_credential_found(self):
        TeleCMICredential.objects.create(
            tenant_id=TENANT_ID,
            app_id='app1',
            secret_encrypted='enc',
            sbc_region=SBCRegionEnum.INDIA,
        )
        cred = get_tenant_credential(TENANT_ID)
        self.assertEqual(cred.app_id, 'app1')

    def test_get_tenant_credential_not_found(self):
        with self.assertRaises(TokenServiceError):
            get_tenant_credential(TENANT_ID)


# ──────────────────────────────────────────────
# call_log_service tests
# ──────────────────────────────────────────────

class CallLogServiceTest(TestCase):

    def _raw_cdr(self, **overrides):
        base = {
            'cmiuid': 'cmi-001',
            'duration': 45,
            'billedsec': 40,
            'rate': 0.01,
            'name': 'Test Caller',
            'from': '919000000000',
            'to': '918000000000',
            'time': 1639554230000,
        }
        base.update(overrides)
        return base

    def _make_lead(self, phone='919000000000'):
        from crm.models import Lead, LeadStatus
        import uuid as _uuid
        status = LeadStatus.objects.create(
            tenant_id=TENANT_ID,
            name='New',
            order_index=1,
        )
        owner = _uuid.UUID('cccccccc-cccc-cccc-cccc-cccccccccccc')
        return Lead.objects.create(
            tenant_id=TENANT_ID,
            name='Test Lead',
            phone=phone,
            status=status,
            owner_user_id=owner,
        )

    def test_process_cdr_creates_call_log(self):
        raw = self._raw_cdr()
        log = process_cdr_record(TENANT_ID, raw, 'inbound')
        self.assertIsNotNone(log)
        self.assertEqual(log.cmiuid, 'cmi-001')
        self.assertEqual(log.direction, CallDirectionEnum.INBOUND)
        self.assertEqual(log.call_type, CallTypeEnum.ANSWERED)
        self.assertEqual(log.duration, 45)

    def test_process_cdr_idempotent(self):
        raw = self._raw_cdr()
        log1 = process_cdr_record(TENANT_ID, raw, 'inbound')
        log2 = process_cdr_record(TENANT_ID, raw, 'inbound')
        self.assertEqual(log1.id, log2.id)
        self.assertEqual(CallLog.objects.filter(tenant_id=TENANT_ID, cmiuid='cmi-001').count(), 1)

    def test_missed_call_detected_by_zero_duration(self):
        raw = self._raw_cdr(duration=0)
        log = process_cdr_record(TENANT_ID, raw, 'inbound')
        self.assertEqual(log.call_type, CallTypeEnum.MISSED)

    def test_links_to_lead_by_phone(self):
        lead = self._make_lead(phone='919000000000')
        raw = self._raw_cdr(**{'from': '919000000000'})
        log = process_cdr_record(TENANT_ID, raw, 'inbound')
        self.assertEqual(log.lead_id, lead.id)

    def test_no_lead_match_gives_null_lead_id(self):
        raw = self._raw_cdr(**{'from': '9199999999999'})
        log = process_cdr_record(TENANT_ID, raw, 'inbound')
        self.assertIsNone(log.lead_id)

    def test_creates_activity_when_lead_matched(self):
        from crm.models import LeadActivity
        lead = self._make_lead()
        raw = self._raw_cdr(**{'from': '919000000000'})
        log = process_cdr_record(TENANT_ID, raw, 'inbound')
        self.assertTrue(log.activity_created)
        activity = LeadActivity.objects.filter(
            tenant_id=TENANT_ID, lead_id=lead.id, type='CALL'
        ).first()
        self.assertIsNotNone(activity)
        self.assertIn('cmi-001', activity.meta.get('cmiuid', ''))

    def test_skips_missing_cmiuid(self):
        result = process_cdr_record(TENANT_ID, {'duration': 10}, 'inbound')
        self.assertIsNone(result)

    def test_format_duration_seconds(self):
        self.assertEqual(_format_duration(45), '45s')

    def test_format_duration_minutes(self):
        self.assertEqual(_format_duration(125), '2m 5s')

    def test_find_lead_suffix_match(self):
        self._make_lead(phone='+919000000000')
        # Strip + and match last 10 digits
        result = _find_lead_id(TENANT_ID, '919000000000')
        self.assertIsNotNone(result)

    def test_tenant_isolation_in_lead_lookup(self):
        """Lead from another tenant must not be matched."""
        other_tenant = uuid.uuid4()
        from crm.models import Lead, LeadStatus
        import uuid as _uuid
        status = LeadStatus.objects.create(
            tenant_id=other_tenant, name='New', order_index=1
        )
        Lead.objects.create(
            tenant_id=other_tenant, name='Other Lead', phone='919000000000',
            status=status, owner_user_id=_uuid.UUID('dddddddd-dddd-dddd-dddd-dddddddddddd'),
        )
        result = _find_lead_id(TENANT_ID, '919000000000')
        self.assertIsNone(result)


# ──────────────────────────────────────────────
# callback_service tests
# ──────────────────────────────────────────────

class CallbackServiceTest(TestCase):

    def _make_call_log(self, direction='inbound', call_type='missed', lead_id=None):
        return CallLog.objects.create(
            tenant_id=TENANT_ID,
            cmiuid='cmi-cb-001',
            direction=direction,
            call_type=call_type,
            from_number='919000000000',
            to_number='918000000000',
            duration=0 if call_type == 'missed' else 30,
            call_time=timezone.now(),
            lead_id=lead_id,
        )

    def _make_lead(self):
        from crm.models import Lead, LeadStatus
        import uuid as _uuid
        status = LeadStatus.objects.create(
            tenant_id=TENANT_ID, name='New', order_index=1
        )
        return Lead.objects.create(
            tenant_id=TENANT_ID,
            name='Test',
            phone='919000000000',
            status=status,
            owner_user_id=_uuid.UUID('cccccccc-cccc-cccc-cccc-cccccccccccc'),
        )

    def test_creates_task_for_missed_inbound_with_lead(self):
        from tasks.models import Task
        lead = self._make_lead()
        log = self._make_call_log(lead_id=lead.id)
        result = create_callback_task_if_needed(TENANT_ID, log, owner_user_id=USER_ID)
        self.assertTrue(result)
        task = Task.objects.filter(tenant_id=TENANT_ID, lead_id=lead.id).first()
        self.assertIsNotNone(task)
        self.assertIn('Call back', task.title)
        self.assertIn('cmi-cb-001', task.description)

    def test_no_task_for_outbound_missed(self):
        log = self._make_call_log(direction='outbound', call_type='missed')
        result = create_callback_task_if_needed(TENANT_ID, log, owner_user_id=USER_ID)
        self.assertFalse(result)

    def test_no_task_for_answered_inbound(self):
        lead = self._make_lead()
        log = self._make_call_log(direction='inbound', call_type='answered', lead_id=lead.id)
        result = create_callback_task_if_needed(TENANT_ID, log, owner_user_id=USER_ID)
        self.assertFalse(result)

    def test_no_task_without_lead(self):
        log = self._make_call_log(lead_id=None)
        result = create_callback_task_if_needed(TENANT_ID, log, owner_user_id=USER_ID)
        self.assertFalse(result)

    def test_idempotent_no_duplicate_task(self):
        from tasks.models import Task
        lead = self._make_lead()
        log = self._make_call_log(lead_id=lead.id)
        create_callback_task_if_needed(TENANT_ID, log, owner_user_id=USER_ID)
        create_callback_task_if_needed(TENANT_ID, log, owner_user_id=USER_ID)
        count = Task.objects.filter(tenant_id=TENANT_ID, lead_id=lead.id).count()
        self.assertEqual(count, 1)
