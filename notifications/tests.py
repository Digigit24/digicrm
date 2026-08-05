import uuid
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

import jwt as pyjwt
from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from crm.models import Lead
from notifications.models import Notification, Reminder, ReminderStatus
from notifications.tasks import dispatch_due_reminders


TENANT_A = uuid.UUID('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
TENANT_B = uuid.UUID('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb')
USER_A = uuid.UUID('cccccccc-cccc-cccc-cccc-cccccccccccc')
USER_B = uuid.UUID('dddddddd-dddd-dddd-dddd-dddddddddddd')


def make_token(user_id, tenant_id=TENANT_A):
    payload = {
        'user_id': str(user_id),
        'email': f'{user_id}@test.com',
        'tenant_id': str(tenant_id),
        'tenant_slug': 'test',
        'is_super_admin': False,
        'permissions': {
            'crm': {'leads': {'view': 'all', 'edit': 'all'}},
        },
        'enabled_modules': ['crm'],
        'roles': [],
        'exp': datetime.now(dt_timezone.utc) + timedelta(hours=1),
    }
    return pyjwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


class FollowUpScheduleAPITest(APITestCase):
    def setUp(self):
        self.lead = Lead.objects.create(
            tenant_id=TENANT_A,
            name='Asha Sharma',
            phone='+919999999999',
            owner_user_id=USER_A,
        )
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {make_token(USER_A)}')

    def test_schedule_upserts_and_cancels_personal_reminder_atomically(self):
        follow_up_at = timezone.now() + timedelta(hours=3)
        url = f'/api/crm/leads/{self.lead.id}/follow-up-schedule/'

        response = self.client.patch(url, {
            'follow_up_at': follow_up_at.isoformat(),
            'reminder': {'enabled': True, 'offset_minutes': 30},
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        reminder = Reminder.objects.get(lead=self.lead, status=ReminderStatus.PENDING)
        self.assertEqual(reminder.recipient_user_id, USER_A)
        self.assertEqual(reminder.remind_at, reminder.follow_up_at - timedelta(minutes=30))

        response = self.client.patch(url, {
            'follow_up_at': None,
            'reminder': {'enabled': False, 'offset_minutes': 0},
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        reminder.refresh_from_db()
        self.assertEqual(reminder.status, ReminderStatus.CANCELLED)
        self.lead.refresh_from_db()
        self.assertIsNone(self.lead.next_follow_up_at)

    def test_rejects_a_reminder_in_the_past(self):
        response = self.client.patch(
            f'/api/crm/leads/{self.lead.id}/follow-up-schedule/',
            {
                'follow_up_at': (timezone.now() + timedelta(minutes=5)).isoformat(),
                'reminder': {'enabled': True, 'offset_minutes': 10},
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Reminder.objects.exists())

    def test_legacy_lead_patch_cancels_a_stale_reminder(self):
        follow_up_at = timezone.now() + timedelta(hours=2)
        reminder = Reminder.objects.create(
            tenant_id=TENANT_A,
            lead=self.lead,
            recipient_user_id=USER_A,
            created_by_user_id=USER_A,
            follow_up_at=follow_up_at,
            remind_at=follow_up_at,
        )

        response = self.client.patch(
            f'/api/crm/leads/{self.lead.id}/',
            {'next_follow_up_at': None},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        reminder.refresh_from_db()
        self.assertEqual(reminder.status, ReminderStatus.CANCELLED)


class ReminderDeliveryTest(APITestCase):
    def setUp(self):
        self.lead = Lead.objects.create(
            tenant_id=TENANT_A,
            name='Kabir Singh',
            phone='+918888888888',
            owner_user_id=USER_A,
        )

    @patch('notifications.tasks.publish_notification', return_value=True)
    def test_dispatch_is_idempotent(self, _publish):
        reminder = Reminder.objects.create(
            tenant_id=TENANT_A,
            lead=self.lead,
            recipient_user_id=USER_A,
            created_by_user_id=USER_A,
            follow_up_at=timezone.now(),
            remind_at=timezone.now() - timedelta(seconds=1),
        )

        first = dispatch_due_reminders()
        second = dispatch_due_reminders()

        reminder.refresh_from_db()
        self.assertEqual(first['delivered'], 1)
        self.assertEqual(second['delivered'], 0)
        self.assertEqual(reminder.status, ReminderStatus.DELIVERED)
        self.assertEqual(Notification.objects.filter(reminder=reminder).count(), 1)


class NotificationInboxAPITest(APITestCase):
    def setUp(self):
        self.lead = Lead.objects.create(
            tenant_id=TENANT_A,
            name='Meera Rao',
            phone='+917777777777',
            owner_user_id=USER_A,
        )
        Notification.objects.create(
            tenant_id=TENANT_A,
            recipient_user_id=USER_A,
            lead=self.lead,
            title='Your reminder',
            body='Follow up now',
            dedupe_key='user-a-reminder',
        )
        Notification.objects.create(
            tenant_id=TENANT_A,
            recipient_user_id=USER_B,
            title='Another user reminder',
            dedupe_key='user-b-reminder',
        )
        Notification.objects.create(
            tenant_id=TENANT_B,
            recipient_user_id=USER_A,
            title='Another tenant reminder',
            dedupe_key='tenant-b-reminder',
        )
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {make_token(USER_A)}')

    def test_inbox_and_count_are_scoped_to_current_tenant_and_user(self):
        response = self.client.get('/api/notifications/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['title'], 'Your reminder')

        count_response = self.client.get('/api/notifications/unread-count/')
        self.assertEqual(count_response.data, {'count': 1})

    def test_mark_all_read_persists(self):
        response = self.client.post('/api/notifications/mark-all-read/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {'updated': 1})
        self.assertIsNotNone(Notification.objects.get(dedupe_key='user-a-reminder').read_at)
        self.assertIsNone(Notification.objects.get(dedupe_key='user-b-reminder').read_at)
