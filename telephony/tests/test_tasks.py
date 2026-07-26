"""Tests for telephony Celery tasks."""
import uuid
from unittest.mock import patch
from django.test import TestCase

from telephony.models import TeleCMICredential, TeleCMIAgent, SBCRegionEnum
from telephony.tasks import sync_all_telecmi_cdrs

TENANT_A = uuid.UUID('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
TENANT_B = uuid.UUID('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb')
USER_A = uuid.UUID('cccccccc-cccc-cccc-cccc-cccccccccccc')
USER_B = uuid.UUID('dddddddd-dddd-dddd-dddd-dddddddddddd')


class SyncAllCDRsTaskTest(TestCase):

    def _make_credential(self, tenant_id):
        return TeleCMICredential.objects.create(
            tenant_id=tenant_id,
            app_id='app1',
            secret_encrypted='enc-secret',
            sbc_region=SBCRegionEnum.INDIA,
        )

    def _make_agent(self, tenant_id, user_id, active=True):
        return TeleCMIAgent.objects.create(
            tenant_id=tenant_id,
            user_id=user_id,
            telecmi_user_id='user1',
            password_encrypted='enc-password',
            is_active=active,
        )

    @patch('telephony.tasks.sync_cdr_for_agent')
    def test_sync_runs_for_each_active_agent(self, mock_sync):
        self._make_credential(TENANT_A)
        self._make_credential(TENANT_B)
        self._make_agent(TENANT_A, USER_A)
        self._make_agent(TENANT_B, USER_B)

        sync_all_telecmi_cdrs(hours_back=1)

        self.assertEqual(mock_sync.call_count, 2)
        mock_sync.assert_any_call(TENANT_A, USER_A, hours_back=1)
        mock_sync.assert_any_call(TENANT_B, USER_B, hours_back=1)

    @patch('telephony.tasks.sync_cdr_for_agent')
    def test_inactive_agents_are_skipped(self, mock_sync):
        self._make_credential(TENANT_A)
        self._make_agent(TENANT_A, USER_A, active=True)
        self._make_agent(TENANT_A, USER_B, active=False)

        sync_all_telecmi_cdrs()

        self.assertEqual(mock_sync.call_count, 1)
        mock_sync.assert_called_once_with(TENANT_A, USER_A, hours_back=1)

    @patch('telephony.tasks.sync_cdr_for_agent')
    def test_one_agent_failure_does_not_stop_others(self, mock_sync):
        self._make_credential(TENANT_A)
        self._make_credential(TENANT_B)
        self._make_agent(TENANT_A, USER_A)
        self._make_agent(TENANT_B, USER_B)
        mock_sync.side_effect = [Exception('boom'), {'created': 1}]

        # Should not raise
        sync_all_telecmi_cdrs(hours_back=1)

        self.assertEqual(mock_sync.call_count, 2)
