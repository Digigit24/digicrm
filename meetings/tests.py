# apps/meetings/tests.py

import uuid
from datetime import datetime, timezone, timedelta

import jwt as pyjwt
from django.conf import settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from meetings.models import Meeting
from crm.models import Lead, LeadStatus


TEST_JWT_SECRET = getattr(settings, 'JWT_SECRET_KEY', 'test-secret')
TEST_JWT_ALGO = getattr(settings, 'JWT_ALGORITHM', 'HS256')

TENANT_A = uuid.UUID('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
USER_A = uuid.UUID('cccccccc-cccc-cccc-cccc-cccccccccccc')
USER_B = uuid.UUID('dddddddd-dddd-dddd-dddd-dddddddddddd')


def _make_token(user_id, tenant_id=TENANT_A, permissions=None):
    payload = {
        'user_id': str(user_id),
        'email': f'{user_id}@test.com',
        'tenant_id': str(tenant_id),
        'tenant_slug': 'test',
        'is_super_admin': False,
        'permissions': permissions or {'crm': {'meetings': {'view': 'own'}}},
        'enabled_modules': ['crm'],
        'roles': [],
        'exp': datetime.now(timezone.utc).replace(hour=23, minute=59),
    }
    return pyjwt.encode(payload, TEST_JWT_SECRET, algorithm=TEST_JWT_ALGO)


class MeetingCalendarScopeTest(APITestCase):
    """P2: calendar endpoint must only return meetings within the user's view scope."""

    def setUp(self):
        self.status = LeadStatus.objects.create(
            tenant_id=TENANT_A,
            name='New',
            order_index=1,
        )
        self.lead = Lead.objects.create(
            tenant_id=TENANT_A,
            name='Lead',
            phone='1111111111',
            status=self.status,
            owner_user_id=USER_A,
        )
        base = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)
        self.meeting_a = Meeting.objects.create(
            tenant_id=TENANT_A,
            title='Meeting A',
            start_at=base,
            end_at=base + timedelta(hours=1),
            owner_user_id=USER_A,
        )
        self.meeting_b = Meeting.objects.create(
            tenant_id=TENANT_A,
            title='Meeting B',
            start_at=base + timedelta(days=1),
            end_at=base + timedelta(days=1, hours=1),
            owner_user_id=USER_B,
        )

    def _auth_client(self, user_id, permissions=None):
        token = _make_token(user_id, permissions=permissions)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_calendar_only_returns_own_meetings(self):
        self._auth_client(USER_A)
        url = reverse('meeting-calendar')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        total = response.data['total_meetings']
        self.assertEqual(total, 1)
        # Both meetings fall in the same default month range, but only A is owned by USER_A.
        self.assertEqual(response.data['total_meetings'], 1)
