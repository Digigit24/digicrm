"""Tests for telephony models."""
import uuid
from django.test import TestCase
from django.utils import timezone

from telephony.models import (
    TeleCMICredential, TeleCMIAgent, CallLog, SMSLog,
    SBCRegionEnum, CallDirectionEnum, CallTypeEnum, SMSStatusEnum,
    SBC_HOST_MAP,
)


TENANT_ID = uuid.UUID('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
USER_ID = uuid.UUID('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb')


class TeleCMICredentialModelTest(TestCase):

    def _make_cred(self, **kwargs):
        defaults = dict(
            tenant_id=TENANT_ID,
            app_id='12345',
            secret_encrypted='enc_secret',
            sbc_region=SBCRegionEnum.INDIA,
        )
        defaults.update(kwargs)
        return TeleCMICredential.objects.create(**defaults)

    def test_create_credential(self):
        cred = self._make_cred()
        self.assertEqual(str(cred.tenant_id), str(TENANT_ID))
        self.assertEqual(cred.app_id, '12345')
        self.assertTrue(cred.is_active)

    def test_sbc_host_india(self):
        cred = self._make_cred(sbc_region=SBCRegionEnum.INDIA)
        self.assertEqual(cred.sbc_host, 'sbcind.telecmi.com')

    def test_sbc_host_us(self):
        cred = self._make_cred(sbc_region=SBCRegionEnum.US, tenant_id=uuid.uuid4())
        self.assertEqual(cred.sbc_host, 'sbcus.telecmi.com')

    def test_str(self):
        cred = self._make_cred()
        self.assertIn(str(TENANT_ID), str(cred))

    def test_tenant_unique(self):
        self._make_cred()
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            self._make_cred()  # Same tenant_id, should fail unique constraint


class TeleCMIAgentModelTest(TestCase):

    def _make_agent(self, **kwargs):
        defaults = dict(
            tenant_id=TENANT_ID,
            user_id=USER_ID,
            telecmi_user_id='103_1111112',
            password_encrypted='enc_pass',
        )
        defaults.update(kwargs)
        return TeleCMIAgent.objects.create(**defaults)

    def test_create_agent(self):
        agent = self._make_agent()
        self.assertEqual(agent.telecmi_user_id, '103_1111112')
        self.assertTrue(agent.is_active)

    def test_token_stale_when_no_token(self):
        agent = self._make_agent()
        self.assertTrue(agent.is_token_stale())

    def test_token_stale_when_old(self):
        agent = self._make_agent()
        agent.cached_token = 'some-token'
        agent.token_obtained_at = timezone.now() - timezone.timedelta(hours=21)
        agent.save()
        self.assertTrue(agent.is_token_stale())

    def test_token_fresh_when_recent(self):
        agent = self._make_agent()
        agent.cached_token = 'some-token'
        agent.token_obtained_at = timezone.now() - timezone.timedelta(hours=1)
        agent.save()
        self.assertFalse(agent.is_token_stale())

    def test_unique_tenant_user(self):
        self._make_agent()
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            self._make_agent()


class CallLogModelTest(TestCase):

    def _make_log(self, **kwargs):
        defaults = dict(
            tenant_id=TENANT_ID,
            cmiuid='cmi-test-001',
            direction=CallDirectionEnum.INBOUND,
            call_type=CallTypeEnum.ANSWERED,
            from_number='919000000000',
            to_number='918000000000',
            duration=45,
            billed_sec=40,
            rate='0.0100',
            call_time=timezone.now(),
        )
        defaults.update(kwargs)
        return CallLog.objects.create(**defaults)

    def test_create_call_log(self):
        log = self._make_log()
        self.assertEqual(log.cmiuid, 'cmi-test-001')
        self.assertEqual(log.direction, CallDirectionEnum.INBOUND)
        self.assertEqual(log.call_type, CallTypeEnum.ANSWERED)
        self.assertFalse(log.activity_created)

    def test_str(self):
        log = self._make_log()
        s = str(log)
        self.assertIn('Inbound', s)
        self.assertIn('Answered', s)

    def test_unique_tenant_cmiuid(self):
        self._make_log()
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            self._make_log()  # Same tenant_id + cmiuid

    def test_different_tenant_same_cmiuid_allowed(self):
        self._make_log()
        other_tenant = uuid.uuid4()
        # Should not raise
        log2 = self._make_log(tenant_id=other_tenant)
        self.assertEqual(log2.cmiuid, 'cmi-test-001')

    def test_missed_call_has_zero_duration(self):
        log = self._make_log(call_type=CallTypeEnum.MISSED, duration=0)
        self.assertEqual(log.duration, 0)


class SMSLogModelTest(TestCase):

    def test_create_sms_log(self):
        sms = SMSLog.objects.create(
            tenant_id=TENANT_ID,
            to_number='919000000000',
            message='Hello from CRM',
            status=SMSStatusEnum.SENT,
            sent_by_user_id=USER_ID,
        )
        self.assertEqual(sms.status, SMSStatusEnum.SENT)
        self.assertIsNone(sms.lead_id)

    def test_str(self):
        sms = SMSLog.objects.create(
            tenant_id=TENANT_ID,
            to_number='919000000000',
            message='Test',
        )
        self.assertIn('919000000000', str(sms))

    def test_failed_sms(self):
        sms = SMSLog.objects.create(
            tenant_id=TENANT_ID,
            to_number='919000000000',
            message='Test',
            status=SMSStatusEnum.FAILED,
            error_message='Token invalid',
        )
        self.assertEqual(sms.status, SMSStatusEnum.FAILED)
        self.assertEqual(sms.error_message, 'Token invalid')
