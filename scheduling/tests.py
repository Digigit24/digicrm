"""Tests for the unified calendar API (`/api/calendar/`)."""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import jwt as pyjwt
from django.conf import settings
from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from crm.models import Lead, LeadStatus
from meetings.models import Meeting, MeetingAttendee, VisibilityEnum
from scheduling.models import CalendarPreference
from tasks.models import Task

TEST_JWT_SECRET = getattr(settings, 'JWT_SECRET_KEY', 'test-secret')
TEST_JWT_ALGO = getattr(settings, 'JWT_ALGORITHM', 'HS256')

TENANT_A = uuid.UUID('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
USER_A = uuid.UUID('cccccccc-cccc-cccc-cccc-cccccccccccc')
USER_B = uuid.UUID('dddddddd-dddd-dddd-dddd-dddddddddddd')

IST = ZoneInfo('Asia/Kolkata')

WINDOW_START = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)


def _token(user_id, permissions, tenant_id=TENANT_A):
    payload = {
        'user_id': str(user_id),
        'email': f'{user_id}@test.com',
        'tenant_id': str(tenant_id),
        'tenant_slug': 'test',
        'is_super_admin': False,
        'permissions': permissions,
        'enabled_modules': ['crm'],
        'roles': [],
        'exp': datetime.now(timezone.utc) + timedelta(hours=6),
    }
    return pyjwt.encode(payload, TEST_JWT_SECRET, algorithm=TEST_JWT_ALGO)


def perms(meetings='all', tasks='all', leads='all'):
    return {
        'crm': {
            'meetings': {'view': meetings, 'create': True, 'edit': meetings,
                         'delete': meetings, 'cancel': meetings},
            'tasks': {'view': tasks, 'edit': tasks, 'delete': tasks},
            'leads': {'view': leads, 'edit': leads},
        }
    }


class CalendarTestBase(APITestCase):
    def setUp(self):
        cache.clear()
        self.patcher = patch(
            'scheduling.directory.fetch_tenant_users',
            return_value={'count': 2, 'results': [
                {'id': str(USER_A), 'name': 'Ritik', 'email': 'a@test.com'},
                {'id': str(USER_B), 'name': 'Asha', 'email': 'b@test.com'},
            ]},
        )
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

        self.status = LeadStatus.objects.create(
            tenant_id=TENANT_A, name='New', order_index=1
        )
        self.lead_a = Lead.objects.create(
            tenant_id=TENANT_A, name='Acme', phone='1', status=self.status,
            owner_user_id=USER_A,
        )
        self.lead_b = Lead.objects.create(
            tenant_id=TENANT_A, name='Globex', phone='2', status=self.status,
            owner_user_id=USER_B,
        )

    def auth(self, user_id, permissions):
        self.client.credentials(
            HTTP_AUTHORIZATION=f'Bearer {_token(user_id, permissions)}'
        )

    def make_meeting(self, owner, title='Meeting', day=8, **kwargs):
        start = datetime(2026, 9, day, 9, 30, tzinfo=timezone.utc)
        defaults = dict(
            tenant_id=TENANT_A, title=title, start_at=start,
            end_at=start + timedelta(hours=1), owner_user_id=owner,
        )
        defaults.update(kwargs)
        meeting = Meeting.objects.create(**defaults)
        MeetingAttendee.objects.create(
            tenant_id=TENANT_A, meeting=meeting, user_id=owner,
            role='ORGANIZER', is_organizer=True, response_status='ACCEPTED',
        )
        return meeting

    def events(self, **extra):
        params = {
            'start': WINDOW_START.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'end': WINDOW_END.strftime('%Y-%m-%dT%H:%M:%SZ'),
        }
        params.update(extra)
        return self.client.get(reverse('scheduling:calendar-events'), params)


class CalendarEventsRangeTest(CalendarTestBase):
    def test_requires_start_and_end(self):
        self.auth(USER_A, perms())
        response = self.client.get(reverse('scheduling:calendar-events'))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_range_cap(self):
        self.auth(USER_A, perms())
        response = self.events(start='2020-01-01T00:00:00Z', end='2030-01-01T00:00:00Z')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unknown_layer_is_rejected(self):
        self.auth(USER_A, perms())
        response = self.events(layers='meetings,unicorns')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unknown_timezone_is_rejected(self):
        self.auth(USER_A, perms())
        response = self.events(tz='Mars/Olympus_Mons')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_meetings_tasks_and_follow_ups_are_merged(self):
        self.make_meeting(USER_A, 'Demo', meeting_type='DEMO')
        Task.objects.create(
            tenant_id=TENANT_A, lead=self.lead_a, title='Send proposal',
            owner_user_id=USER_A, assignee_user_id=USER_A,
            due_date=datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc),
        )
        self.lead_a.next_follow_up_at = datetime(2026, 9, 10, 5, 30, tzinfo=timezone.utc)
        self.lead_a.save(update_fields=['next_follow_up_at'])

        self.auth(USER_A, perms())
        response = self.events()
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        sources = [event['source'] for event in response.data['events']]
        self.assertCountEqual(sources, ['meeting', 'task', 'follow_up'])

        by_source = {event['source']: event for event in response.data['events']}
        self.assertEqual(by_source['meeting']['color_key'], 'demo')
        self.assertEqual(by_source['task']['color_key'], 'task')
        self.assertTrue(by_source['task']['all_day'])
        self.assertEqual(by_source['follow_up']['color_key'], 'follow_up')
        self.assertEqual(by_source['meeting']['owner_name'], 'Ritik')
        self.assertTrue(by_source['meeting']['can_edit'])

    def test_events_are_sorted_and_carry_a_stable_id(self):
        self.make_meeting(USER_A, 'Later', day=20)
        self.make_meeting(USER_A, 'Earlier', day=2)
        self.auth(USER_A, perms())
        response = self.events(layers='meetings')
        titles = [event['title'] for event in response.data['events']]
        self.assertEqual(titles, ['Earlier', 'Later'])
        self.assertTrue(response.data['events'][0]['id'].startswith('meeting:'))

    def test_recurring_meeting_is_expanded(self):
        rule = 'FREQ=WEEKLY;BYDAY=TU;COUNT=8'
        from meetings import recurrence
        start = datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)
        self.make_meeting(
            USER_A, 'Weekly', day=1, recurrence_rule=rule,
            recurrence_end_at=recurrence.compute_recurrence_end(rule, start, 'UTC'),
        )
        self.auth(USER_A, perms())
        response = self.events(layers='meetings')
        # 2026-09-01 is a Tuesday; September 2026 has 5 Tuesdays.
        self.assertEqual(len(response.data['events']), 5)
        self.assertTrue(all(event['is_recurring'] for event in response.data['events']))

    def test_expand_recurring_false_returns_masters_only(self):
        rule = 'FREQ=WEEKLY;BYDAY=TU;COUNT=8'
        from meetings import recurrence
        start = datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)
        self.make_meeting(
            USER_A, 'Weekly', day=1, recurrence_rule=rule,
            recurrence_end_at=recurrence.compute_recurrence_end(rule, start, 'UTC'),
        )
        self.auth(USER_A, perms())
        response = self.events(layers='meetings', expand_recurring='false')
        self.assertEqual(len(response.data['events']), 1)

    def test_soft_deleted_and_cancelled_are_hidden_by_default(self):
        self.make_meeting(USER_A, 'Gone', is_deleted=True)
        self.make_meeting(USER_A, 'Cancelled', day=9, status='CANCELLED')
        self.make_meeting(USER_A, 'Live', day=10)
        self.auth(USER_A, perms())
        response = self.events(layers='meetings')
        self.assertEqual([e['title'] for e in response.data['events']], ['Live'])

        response = self.events(layers='meetings', include_cancelled='true')
        self.assertCountEqual(
            [e['title'] for e in response.data['events']], ['Cancelled', 'Live']
        )

    def test_task_all_day_bucket_uses_the_requested_timezone(self):
        """A 02:00 IST due date is 20:30 UTC the previous day."""
        due_local = datetime(2026, 9, 10, 2, 0, tzinfo=IST)
        Task.objects.create(
            tenant_id=TENANT_A, lead=self.lead_a, title='Early task',
            owner_user_id=USER_A, due_date=due_local.astimezone(timezone.utc),
        )
        self.auth(USER_A, perms())
        response = self.events(layers='tasks', tz='Asia/Kolkata')
        event = response.data['events'][0]
        # 2026-09-10 00:00 IST == 2026-09-09 18:30 UTC
        self.assertEqual(
            event['start_at'], datetime(2026, 9, 9, 18, 30, tzinfo=timezone.utc)
        )
        self.assertEqual(
            event['end_at'], datetime(2026, 9, 10, 18, 30, tzinfo=timezone.utc)
        )


class CalendarPerSourceScopeTest(CalendarTestBase):
    """Each layer resolves its own permission scope independently."""

    def setUp(self):
        super().setUp()
        self.make_meeting(USER_A, 'Mine')
        self.make_meeting(USER_B, 'Theirs', day=9)
        Task.objects.create(
            tenant_id=TENANT_A, lead=self.lead_a, title='My task',
            owner_user_id=USER_A, assignee_user_id=USER_A,
            due_date=datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc),
        )
        Task.objects.create(
            tenant_id=TENANT_A, lead=self.lead_b, title='Their task',
            owner_user_id=USER_B, assignee_user_id=USER_B,
            due_date=datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc),
        )

    def test_meetings_all_but_tasks_own(self):
        self.auth(USER_A, perms(meetings='all', tasks='own', leads='own'))
        response = self.events(
            layers='meetings,tasks',
            user_ids=f'{USER_A},{USER_B}',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        meetings = [e['title'] for e in response.data['events'] if e['source'] == 'meeting']
        tasks = [e['title'] for e in response.data['events'] if e['source'] == 'task']
        self.assertCountEqual(meetings, ['Mine', 'Theirs'])
        self.assertEqual(tasks, ['My task'])
        self.assertEqual(response.data['layer_scopes']['meetings'], 'all')
        self.assertEqual(response.data['layer_scopes']['tasks'], 'own')
        self.assertIn(str(USER_B), response.data['denied_user_ids'])

    def test_own_scope_gets_zero_foreign_events_even_when_passing_user_ids(self):
        self.auth(USER_A, perms(meetings='own', tasks='own', leads='own'))
        response = self.events(layers='meetings', user_ids=str(USER_B))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['events'], [])
        self.assertEqual(response.data['denied_user_ids'], [str(USER_B)])

    def test_no_grant_at_all_is_403(self):
        self.auth(USER_A, {'crm': {}})
        response = self.events(layers='meetings')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_task_assigned_to_me_but_owned_by_my_manager_is_visible(self):
        """tasks.Task is now registered in OWNERSHIP_FIELDS."""
        Task.objects.create(
            tenant_id=TENANT_A, lead=self.lead_b, title='Delegated',
            owner_user_id=USER_B, assignee_user_id=USER_A,
            due_date=datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc),
        )
        self.auth(USER_A, perms(meetings='own', tasks='team', leads='own'))
        response = self.events(layers='tasks', user_ids=f'{USER_A},{USER_B}')
        titles = [e['title'] for e in response.data['events']]
        self.assertIn('Delegated', titles)


class CalendarPrivateVisibilityTest(CalendarTestBase):
    def setUp(self):
        super().setUp()
        self.private = self.make_meeting(
            USER_B, 'Therapy appointment', day=8,
            visibility=VisibilityEnum.PRIVATE,
            description='very personal', location='Clinic',
        )

    def test_private_meeting_is_redacted_for_a_non_participant(self):
        self.auth(USER_A, perms())
        response = self.events(layers='meetings', user_ids=str(USER_B))
        self.assertEqual(len(response.data['events']), 1)
        event = response.data['events'][0]
        self.assertTrue(event['redacted'])
        self.assertEqual(event['title'], 'Busy')
        self.assertIsNone(event['description'])
        self.assertIsNone(event['location'])
        self.assertEqual(event['attendees'], [])
        self.assertIsNone(event['lead'])
        self.assertFalse(event['can_edit'])
        # free/busy is still exposed
        self.assertIn('start_at', event)
        self.assertIn('end_at', event)
        self.assertEqual(event['transparency'], 'OPAQUE')

    def test_private_meeting_is_full_detail_for_its_owner(self):
        self.auth(USER_B, perms())
        response = self.events(layers='meetings', user_ids=str(USER_B))
        event = response.data['events'][0]
        self.assertFalse(event['redacted'])
        self.assertEqual(event['title'], 'Therapy appointment')
        self.assertEqual(event['description'], 'very personal')

    def test_private_meeting_is_full_detail_for_an_attendee(self):
        MeetingAttendee.objects.create(
            tenant_id=TENANT_A, meeting=self.private, user_id=USER_A
        )
        self.auth(USER_A, perms())
        response = self.events(layers='meetings', user_ids=str(USER_B))
        event = response.data['events'][0]
        self.assertFalse(event['redacted'])
        self.assertEqual(event['title'], 'Therapy appointment')

    def test_default_visibility_is_full_detail_for_an_all_scoped_teammate(self):
        self.make_meeting(USER_B, 'Normal standup', day=9)
        self.auth(USER_A, perms())
        response = self.events(layers='meetings', user_ids=str(USER_B))
        normal = [e for e in response.data['events'] if e['source_id'] != self.private.pk][0]
        self.assertFalse(normal['redacted'])
        self.assertEqual(normal['title'], 'Normal standup')


class CalendarPreferencesTest(CalendarTestBase):
    def test_get_auto_creates_defaults(self):
        self.auth(USER_A, perms())
        response = self.client.get(reverse('scheduling:calendar-preferences'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['timezone'], 'UTC')
        self.assertEqual(response.data['default_view'], 'MONTH')
        self.assertEqual(response.data['visible_layers'],
                         ['meetings', 'tasks', 'follow_ups'])
        self.assertTrue(
            CalendarPreference.objects.filter(tenant_id=TENANT_A, user_id=USER_A).exists()
        )

    def test_patch_updates_and_validates_timezone(self):
        self.auth(USER_A, perms())
        response = self.client.patch(
            reverse('scheduling:calendar-preferences'),
            {'timezone': 'Asia/Kolkata', 'default_view': 'WEEK'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['timezone'], 'Asia/Kolkata')

        response = self.client.patch(
            reverse('scheduling:calendar-preferences'),
            {'timezone': 'Nowhere/Nothing'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_preference_timezone_is_the_default_bucketing_zone(self):
        CalendarPreference.objects.create(
            tenant_id=TENANT_A, user_id=USER_A, timezone='Asia/Kolkata'
        )
        Task.objects.create(
            tenant_id=TENANT_A, lead=self.lead_a, title='Early task',
            owner_user_id=USER_A,
            due_date=datetime(2026, 9, 10, 2, 0, tzinfo=IST).astimezone(timezone.utc),
        )
        self.auth(USER_A, perms())
        response = self.events(layers='tasks')
        self.assertEqual(response.data['range']['timezone'], 'Asia/Kolkata')
        self.assertEqual(
            response.data['events'][0]['start_at'],
            datetime(2026, 9, 9, 18, 30, tzinfo=timezone.utc),
        )


class CalendarMembersTest(CalendarTestBase):
    def test_team_list_for_an_all_scoped_caller(self):
        self.auth(USER_A, perms())
        response = self.client.get(reverse('scheduling:calendar-members'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['can_view_team'])
        self.assertEqual(len(response.data['members']), 2)
        self.assertTrue(any(member['is_self'] for member in response.data['members']))
        colors = {member['user_id']: member['color_index'] for member in response.data['members']}
        self.assertTrue(all(0 <= value < 12 for value in colors.values()))

    def test_own_scoped_caller_only_sees_themselves(self):
        self.auth(USER_A, perms(meetings='own'))
        response = self.client.get(reverse('scheduling:calendar-members'))
        self.assertFalse(response.data['can_view_team'])
        self.assertEqual(len(response.data['members']), 1)
        self.assertEqual(response.data['members'][0]['user_id'], str(USER_A))

    def test_counts_in_range(self):
        self.make_meeting(USER_A, 'One')
        self.make_meeting(USER_A, 'Two', day=9)
        self.auth(USER_A, perms())
        response = self.client.get(reverse('scheduling:calendar-members'), {
            'start': WINDOW_START.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'end': WINDOW_END.strftime('%Y-%m-%dT%H:%M:%SZ'),
        })
        self_row = [m for m in response.data['members'] if m['is_self']][0]
        self.assertEqual(self_row['counts']['meetings'], 2)

    def test_directory_is_cached(self):
        self.auth(USER_A, perms())
        self.client.get(reverse('scheduling:calendar-members'))
        self.client.get(reverse('scheduling:calendar-members'))
        from scheduling.directory import fetch_tenant_users
        self.assertEqual(fetch_tenant_users.call_count, 1)


class CalendarConflictsTest(CalendarTestBase):
    def test_overlap_is_reported(self):
        self.make_meeting(USER_A, 'Existing', day=8)
        self.auth(USER_A, perms())
        response = self.client.post(reverse('scheduling:calendar-conflicts'), {
            'start_at': '2026-09-08T10:00:00Z',
            'end_at': '2026-09-08T11:00:00Z',
            'user_ids': [str(USER_A)],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertTrue(response.data['has_conflicts'])
        self.assertEqual(response.data['conflicts'][0]['title'], 'Existing')

    def test_no_overlap(self):
        self.make_meeting(USER_A, 'Existing', day=8)
        self.auth(USER_A, perms())
        response = self.client.post(reverse('scheduling:calendar-conflicts'), {
            'start_at': '2026-09-08T15:00:00Z',
            'end_at': '2026-09-08T16:00:00Z',
        }, format='json')
        self.assertFalse(response.data['has_conflicts'])

    def test_excluded_meeting_is_ignored(self):
        meeting = self.make_meeting(USER_A, 'Existing', day=8)
        self.auth(USER_A, perms())
        response = self.client.post(reverse('scheduling:calendar-conflicts'), {
            'start_at': '2026-09-08T10:00:00Z',
            'end_at': '2026-09-08T11:00:00Z',
            'exclude_meeting_id': meeting.pk,
        }, format='json')
        self.assertFalse(response.data['has_conflicts'])

    def test_private_conflict_is_redacted(self):
        self.make_meeting(USER_B, 'Secret', day=8, visibility=VisibilityEnum.PRIVATE)
        self.auth(USER_A, perms())
        response = self.client.post(reverse('scheduling:calendar-conflicts'), {
            'start_at': '2026-09-08T10:00:00Z',
            'end_at': '2026-09-08T11:00:00Z',
            'user_ids': [str(USER_B)],
        }, format='json')
        self.assertTrue(response.data['has_conflicts'])
        self.assertTrue(response.data['conflicts'][0]['redacted'])
        self.assertEqual(response.data['conflicts'][0]['title'], 'Busy')


class CalendarAvailabilityTest(CalendarTestBase):
    def test_busy_blocks_expose_no_titles(self):
        self.make_meeting(USER_A, 'Existing', day=8)
        self.auth(USER_A, perms())
        response = self.client.post(reverse('scheduling:calendar-availability'), {
            'start': '2026-09-08T00:00:00Z',
            'end': '2026-09-09T00:00:00Z',
            'user_ids': [str(USER_A)],
            'duration_minutes': 30,
            'granularity_minutes': 30,
            'respect_working_hours': False,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        blocks = response.data['busy'][str(USER_A)]
        self.assertEqual(len(blocks), 1)
        self.assertEqual(set(blocks[0].keys()), {'start', 'end', 'reason'})

    def test_transparent_meetings_do_not_block(self):
        self.make_meeting(USER_A, 'Free time', day=8, transparency='TRANSPARENT')
        self.auth(USER_A, perms())
        response = self.client.post(reverse('scheduling:calendar-availability'), {
            'start': '2026-09-08T00:00:00Z',
            'end': '2026-09-09T00:00:00Z',
            'respect_working_hours': False,
        }, format='json')
        self.assertEqual(response.data['busy'][str(USER_A)], [])

    def test_suggested_slots_avoid_busy_time(self):
        self.make_meeting(USER_A, 'Existing', day=8)   # 09:30-10:30 UTC
        self.auth(USER_A, perms())
        response = self.client.post(reverse('scheduling:calendar-availability'), {
            'start': '2026-09-08T09:00:00Z',
            'end': '2026-09-08T12:00:00Z',
            'duration_minutes': 30,
            'granularity_minutes': 30,
            'respect_working_hours': False,
        }, format='json')
        starts = [slot['start'] for slot in response.data['suggested_slots']]
        self.assertIn(datetime(2026, 9, 8, 9, 0, tzinfo=timezone.utc), starts)
        self.assertNotIn(datetime(2026, 9, 8, 9, 30, tzinfo=timezone.utc), starts)
        self.assertNotIn(datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc), starts)
        self.assertIn(datetime(2026, 9, 8, 10, 30, tzinfo=timezone.utc), starts)


class CalendarLayersTest(CalendarTestBase):
    def test_layer_metadata_reflects_the_callers_scopes(self):
        self.auth(USER_A, perms(meetings='all', tasks='own', leads=None))
        response = self.client.get(reverse('scheduling:calendar-layers'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        by_key = {layer['key']: layer for layer in response.data['layers']}
        self.assertEqual(by_key['meetings']['scope'], 'all')
        self.assertEqual(by_key['tasks']['scope'], 'own')
        self.assertIsNone(by_key['follow_ups']['scope'])
        self.assertFalse(by_key['follow_ups']['visible'])
        self.assertIn('meeting', response.data['color_tokens'])
