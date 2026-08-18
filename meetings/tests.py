# apps/meetings/tests.py

import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import jwt as pyjwt
from django.conf import settings
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from crm.models import Lead, LeadStatus
from meetings import recurrence, services
from meetings.models import (
    AttendeeResponseEnum,
    Meeting,
    MeetingAttendee,
    MeetingReminder,
    MeetingStatusEnum,
)
from notifications.models import Reminder, ReminderStatus, ReminderSubjectType

TEST_JWT_SECRET = getattr(settings, 'JWT_SECRET_KEY', 'test-secret')
TEST_JWT_ALGO = getattr(settings, 'JWT_ALGORITHM', 'HS256')

TENANT_A = uuid.UUID('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
USER_A = uuid.UUID('cccccccc-cccc-cccc-cccc-cccccccccccc')
USER_B = uuid.UUID('dddddddd-dddd-dddd-dddd-dddddddddddd')

IST = ZoneInfo('Asia/Kolkata')


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
        'exp': datetime.now(timezone.utc) + timedelta(hours=6),
    }
    return pyjwt.encode(payload, TEST_JWT_SECRET, algorithm=TEST_JWT_ALGO)


ALL_MEETING_PERMS = {
    'crm': {'meetings': {
        'view': 'all', 'create': True, 'edit': 'all', 'delete': 'all', 'cancel': 'all',
    }}
}
OWN_MEETING_PERMS = {
    'crm': {'meetings': {
        'view': 'own', 'create': True, 'edit': 'own', 'delete': 'own', 'cancel': 'own',
    }}
}


class MeetingAuthMixin:
    def _auth(self, user_id, permissions=None):
        token = _make_token(user_id, permissions=permissions)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')


# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------
class MeetingConstraintTest(TestCase):
    """The two constraint changes in migration 0002."""

    def setUp(self):
        self.base = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)

    def test_two_same_title_same_time_meetings_are_allowed(self):
        """unique_meeting_per_tenant is GONE.

        Two people in a tenant must both be able to have a "Standup" at 09:00.
        """
        Meeting.objects.create(
            tenant_id=TENANT_A, title='Standup',
            start_at=self.base, end_at=self.base + timedelta(minutes=15),
            owner_user_id=USER_A,
        )
        second = Meeting.objects.create(
            tenant_id=TENANT_A, title='Standup',
            start_at=self.base, end_at=self.base + timedelta(minutes=15),
            owner_user_id=USER_B,
        )
        self.assertEqual(
            Meeting.objects.filter(tenant_id=TENANT_A, title='Standup').count(), 2
        )
        self.assertIsNotNone(second.pk)

    def test_same_owner_can_repeat_title_and_time(self):
        for _ in range(3):
            Meeting.objects.create(
                tenant_id=TENANT_A, title='Standup',
                start_at=self.base, end_at=self.base + timedelta(minutes=15),
                owner_user_id=USER_A,
            )
        self.assertEqual(Meeting.objects.count(), 3)

    def test_zero_duration_marker_is_legal(self):
        """The check constraint is now >= so a zero-length marker is storable."""
        meeting = Meeting.objects.create(
            tenant_id=TENANT_A, title='Marker',
            start_at=self.base, end_at=self.base, owner_user_id=USER_A,
        )
        self.assertEqual(meeting.start_at, meeting.end_at)

    def test_end_before_start_is_still_rejected(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Meeting.objects.create(
                    tenant_id=TENANT_A, title='Backwards',
                    start_at=self.base, end_at=self.base - timedelta(hours=1),
                    owner_user_id=USER_A,
                )

    def test_override_uniqueness(self):
        master = Meeting.objects.create(
            tenant_id=TENANT_A, title='Weekly', start_at=self.base,
            end_at=self.base + timedelta(hours=1), owner_user_id=USER_A,
            recurrence_rule='FREQ=WEEKLY;COUNT=4',
        )
        Meeting.objects.create(
            tenant_id=TENANT_A, title='Weekly', start_at=self.base,
            end_at=self.base + timedelta(hours=1), owner_user_id=USER_A,
            recurring_parent=master, recurrence_original_start=self.base,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Meeting.objects.create(
                    tenant_id=TENANT_A, title='Weekly dup', start_at=self.base,
                    end_at=self.base + timedelta(hours=1), owner_user_id=USER_A,
                    recurring_parent=master, recurrence_original_start=self.base,
                )


# ---------------------------------------------------------------------------
# Timezone correctness
# ---------------------------------------------------------------------------
class MeetingTimezoneTest(MeetingAuthMixin, APITestCase):
    """The IST 02:00 bug: a 02:00 Asia/Kolkata meeting is 20:30 UTC the *previous*
    day, so bucketing on the UTC date files it under the wrong local date."""

    def setUp(self):
        # 2026-09-10 02:00 IST == 2026-09-09 20:30 UTC
        self.local = datetime(2026, 9, 10, 2, 0, tzinfo=IST)
        self.utc = self.local.astimezone(timezone.utc)
        self.meeting = Meeting.objects.create(
            tenant_id=TENANT_A, title='Early call',
            start_at=self.utc, end_at=self.utc + timedelta(hours=1),
            owner_user_id=USER_A, timezone='Asia/Kolkata',
        )

    def test_utc_date_and_local_date_differ(self):
        self.assertEqual(self.utc.date().isoformat(), '2026-09-09')
        self.assertEqual(
            recurrence.local_date(self.utc, 'Asia/Kolkata').isoformat(), '2026-09-10'
        )

    def test_calendar_action_buckets_in_requested_timezone(self):
        self._auth(USER_A, ALL_MEETING_PERMS)
        url = reverse('meeting-calendar')
        response = self.client.get(url, {'month': '2026-09', 'tz': 'Asia/Kolkata'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('2026-09-10', response.data['calendar_data'])
        self.assertNotIn('2026-09-09', response.data['calendar_data'])

    def test_calendar_action_in_utc_buckets_on_the_previous_day(self):
        self._auth(USER_A, ALL_MEETING_PERMS)
        response = self.client.get(reverse('meeting-calendar'), {'month': '2026-09'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('2026-09-09', response.data['calendar_data'])

    def test_unknown_timezone_is_rejected_by_the_serializer(self):
        self._auth(USER_A, ALL_MEETING_PERMS)
        response = self.client.post(reverse('meeting-list'), {
            'title': 'Bad zone',
            'start_at': '2026-09-10T09:00:00Z',
            'end_at': '2026-09-10T10:00:00Z',
            'timezone': 'Mars/Olympus_Mons',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('timezone', response.data)

    def test_weekly_ist_series_keeps_local_time_across_a_us_dst_shift(self):
        start = datetime(2026, 10, 20, 9, 0, tzinfo=IST).astimezone(timezone.utc)
        meeting = Meeting.objects.create(
            tenant_id=TENANT_A, title='IST standup', start_at=start,
            end_at=start + timedelta(minutes=30), owner_user_id=USER_A,
            timezone='Asia/Kolkata', recurrence_rule='FREQ=WEEKLY;COUNT=6',
        )
        occurrences = recurrence.expand_occurrences(
            meeting, start, start + timedelta(days=60)
        )
        self.assertEqual(len(occurrences), 6)
        for occurrence in occurrences:
            self.assertEqual(occurrence.astimezone(IST).hour, 9)
            self.assertEqual(occurrence.astimezone(IST).minute, 0)


class AllDayMeetingTest(MeetingAuthMixin, APITestCase):
    def test_all_day_snaps_to_local_midnight_boundaries(self):
        self._auth(USER_A, ALL_MEETING_PERMS)
        response = self.client.post(reverse('meeting-list'), {
            'title': 'Conference',
            'all_day': True,
            'timezone': 'Asia/Kolkata',
            'start_at': '2026-09-10T07:15:00Z',   # 12:45 IST on 2026-09-10
            'end_at': '2026-09-10T09:00:00Z',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        meeting = Meeting.objects.get(pk=response.data['id'])
        self.assertTrue(meeting.all_day)
        # 2026-09-10 00:00 IST == 2026-09-09 18:30 UTC, end is the NEXT local midnight.
        self.assertEqual(meeting.start_at, datetime(2026, 9, 9, 18, 30, tzinfo=timezone.utc))
        self.assertEqual(meeting.end_at, datetime(2026, 9, 10, 18, 30, tzinfo=timezone.utc))
        self.assertNotEqual(meeting.start_at, meeting.end_at)

    def test_multi_day_all_day_event_keeps_its_span(self):
        self._auth(USER_A, ALL_MEETING_PERMS)
        response = self.client.post(reverse('meeting-list'), {
            'title': 'Conference', 'all_day': True, 'timezone': 'UTC',
            'start_at': '2026-03-03T00:00:00Z',
            'end_at': '2026-03-05T00:00:00Z',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        meeting = Meeting.objects.get(pk=response.data['id'])
        self.assertEqual((meeting.end_at - meeting.start_at).days, 2)

    def test_timed_meeting_still_requires_end_after_start(self):
        self._auth(USER_A, ALL_MEETING_PERMS)
        response = self.client.post(reverse('meeting-list'), {
            'title': 'Backwards',
            'start_at': '2026-09-10T10:00:00Z',
            'end_at': '2026-09-10T09:00:00Z',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


# ---------------------------------------------------------------------------
# Recurrence
# ---------------------------------------------------------------------------
class RecurrenceExpansionTest(TestCase):
    def setUp(self):
        self.start = datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)  # a Tuesday
        self.master = Meeting.objects.create(
            tenant_id=TENANT_A, title='Product demo', start_at=self.start,
            end_at=self.start + timedelta(hours=1), owner_user_id=USER_A,
            timezone='UTC', recurrence_rule='FREQ=WEEKLY;BYDAY=TU;COUNT=8',
            recurrence_end_at=recurrence.compute_recurrence_end(
                'FREQ=WEEKLY;BYDAY=TU;COUNT=8', self.start, 'UTC'
            ),
        )

    def test_expands_the_declared_number_of_occurrences(self):
        occurrences = recurrence.expand_occurrences(
            self.master, self.start, self.start + timedelta(days=90)
        )
        self.assertEqual(len(occurrences), 8)
        self.assertEqual(occurrences[0], self.start)
        self.assertEqual(occurrences[1], self.start + timedelta(days=7))

    def test_recurrence_end_at_is_denormalised(self):
        self.assertEqual(
            self.master.recurrence_end_at, self.start + timedelta(days=7 * 7)
        )

    def test_exdates_are_subtracted(self):
        removed = self.start + timedelta(days=7)
        self.master.recurrence_exdates = [removed.isoformat()]
        self.master.save(update_fields=['recurrence_exdates'])
        occurrences = recurrence.expand_occurrences(
            self.master, self.start, self.start + timedelta(days=90)
        )
        self.assertEqual(len(occurrences), 7)
        self.assertNotIn(removed, occurrences)

    def test_is_valid_occurrence(self):
        self.assertTrue(
            recurrence.is_valid_occurrence(self.master, self.start + timedelta(days=7))
        )
        self.assertFalse(
            recurrence.is_valid_occurrence(self.master, self.start + timedelta(days=3))
        )

    def test_open_ended_series_has_no_recurrence_end(self):
        self.assertIsNone(
            recurrence.compute_recurrence_end('FREQ=DAILY', self.start, 'UTC')
        )

    def test_until_without_z_is_normalised(self):
        rule = 'FREQ=DAILY;UNTIL=20260910T000000'
        end = recurrence.compute_recurrence_end(rule, self.start, 'UTC')
        self.assertIsNotNone(end)
        self.assertEqual(end.date().isoformat(), '2026-09-09')


class RecurrenceEditScopeTest(MeetingAuthMixin, APITestCase):
    """The three edit modes from plan B.3."""

    def setUp(self):
        self.start = datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)
        rule = 'FREQ=WEEKLY;BYDAY=TU;COUNT=8'
        self.master = Meeting.objects.create(
            tenant_id=TENANT_A, title='Weekly sync', start_at=self.start,
            end_at=self.start + timedelta(hours=1), owner_user_id=USER_A,
            timezone='UTC', recurrence_rule=rule,
            recurrence_end_at=recurrence.compute_recurrence_end(rule, self.start, 'UTC'),
        )
        MeetingAttendee.objects.create(
            tenant_id=TENANT_A, meeting=self.master, user_id=USER_A,
            role='ORGANIZER', is_organizer=True,
        )
        MeetingReminder.objects.create(
            tenant_id=TENANT_A, meeting=self.master, minutes_before=10,
        )
        self.third = self.start + timedelta(days=14)
        # Real clients send a Z-suffixed instant; a naked '+' in a query
        # string would arrive decoded as a space.
        self.third_param = self.third.strftime('%Y-%m-%dT%H:%M:%SZ')
        self._auth(USER_A, ALL_MEETING_PERMS)
        self.url = reverse('meeting-detail', args=[self.master.pk])

    def test_scoped_edit_without_occurrence_start_is_a_400(self):
        response = self.client.patch(
            f'{self.url}?edit_scope=this', {'title': 'Nope'}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('occurrence_start', response.data)

    def test_scoped_edit_with_a_bogus_instant_is_a_400(self):
        bogus = (self.start + timedelta(days=3)).isoformat()
        response = self.client.patch(
            f'{self.url}?edit_scope=this&occurrence_start={bogus}',
            {'title': 'Nope'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_edit_this_creates_an_override_and_copies_children(self):
        response = self.client.patch(
            f'{self.url}?edit_scope=this&occurrence_start={self.third_param}',
            {'title': 'Just this one'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        override = Meeting.objects.get(
            recurring_parent=self.master, recurrence_original_start=self.third
        )
        self.assertEqual(override.title, 'Just this one')
        self.assertIsNone(override.recurrence_rule)
        self.assertNotEqual(override.uid, self.master.uid)
        self.assertEqual(override.attendees.count(), 1)
        self.assertEqual(override.reminder_rules.count(), 1)
        self.master.refresh_from_db()
        self.assertEqual(self.master.title, 'Weekly sync')

    def test_edit_all_mutates_the_master_in_place(self):
        response = self.client.patch(
            f'{self.url}?edit_scope=all', {'title': 'Renamed series'}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.master.refresh_from_db()
        self.assertEqual(self.master.title, 'Renamed series')
        self.assertFalse(Meeting.objects.filter(recurring_parent=self.master).exists())

    def test_edit_this_and_following_splits_the_series(self):
        response = self.client.patch(
            f'{self.url}?edit_scope=this_and_following'
            f'&occurrence_start={self.third_param}',
            {'title': 'New chapter'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertIn('updated', response.data)
        self.assertIn('created', response.data)

        self.master.refresh_from_db()
        self.assertIn('UNTIL=', self.master.recurrence_rule)
        self.assertNotIn('COUNT=', self.master.recurrence_rule)

        head = recurrence.expand_occurrences(
            self.master, self.start, self.start + timedelta(days=365)
        )
        self.assertEqual(len(head), 2)   # occurrences 1 and 2 stay with the master

        new_master = Meeting.objects.get(pk=response.data['created']['id'])
        self.assertEqual(new_master.title, 'New chapter')
        self.assertEqual(new_master.start_at, self.third)
        self.assertNotEqual(new_master.uid, self.master.uid)
        tail = recurrence.expand_occurrences(
            new_master, self.third, self.third + timedelta(days=365)
        )
        self.assertEqual(len(tail), 6)   # 8 total - 2 kept by the master
        self.assertEqual(len(head) + len(tail), 8)
        self.assertEqual(new_master.attendees.count(), 1)
        self.assertEqual(new_master.reminder_rules.count(), 1)

    def test_delete_this_appends_an_exdate(self):
        response = self.client.delete(
            f'{self.url}?edit_scope=this&occurrence_start={self.third_param}'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.master.refresh_from_db()
        self.assertEqual(len(self.master.recurrence_exdates), 1)
        occurrences = recurrence.expand_occurrences(
            self.master, self.start, self.start + timedelta(days=365)
        )
        self.assertEqual(len(occurrences), 7)
        self.assertNotIn(self.third, occurrences)

    def test_delete_all_soft_deletes_the_series(self):
        response = self.client.delete(f'{self.url}?edit_scope=all')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.master.refresh_from_db()
        self.assertTrue(self.master.is_deleted)
        self.assertIsNotNone(self.master.deleted_at)
        # soft delete, not destructive
        self.assertTrue(Meeting.objects.filter(pk=self.master.pk).exists())

    def test_occurrences_endpoint_previews_the_series(self):
        response = self.client.get(
            reverse('meeting-occurrences', args=[self.master.pk]),
            {'start': self.start.isoformat(),
             'end': (self.start + timedelta(days=365)).isoformat()},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 8)


# ---------------------------------------------------------------------------
# Attendees / RSVP / lifecycle
# ---------------------------------------------------------------------------
class MeetingApiTest(MeetingAuthMixin, APITestCase):
    def setUp(self):
        self.start = datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)
        self.lead_status = LeadStatus.objects.create(
            tenant_id=TENANT_A, name='New', order_index=1
        )
        self.lead = Lead.objects.create(
            tenant_id=TENANT_A, name='Acme Corp', phone='1111111111',
            status=self.lead_status, owner_user_id=USER_A,
        )

    def test_create_with_nested_attendees_and_reminders(self):
        self._auth(USER_A, ALL_MEETING_PERMS)
        response = self.client.post(reverse('meeting-list'), {
            'title': 'Product demo',
            'meeting_type': 'DEMO',
            'lead': self.lead.pk,
            'start_at': '2026-09-01T09:30:00Z',
            'end_at': '2026-09-01T10:30:00Z',
            'timezone': 'Asia/Kolkata',
            'recurrence_rule': 'FREQ=WEEKLY;BYDAY=TU;COUNT=8',
            'attendees': [
                {'user_id': str(USER_B), 'role': 'REQUIRED'},
                {'lead': self.lead.pk, 'role': 'REQUIRED'},
                {'email': 'ext@client.com', 'display_name': 'Client', 'role': 'OPTIONAL'},
            ],
            'reminder_rules': [
                {'minutes_before': 1440, 'method': 'IN_APP'},
                {'minutes_before': 10, 'method': 'IN_APP'},
            ],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        meeting = Meeting.objects.get(pk=response.data['id'])
        # 3 requested + the auto-created organiser
        self.assertEqual(meeting.attendees.count(), 4)
        self.assertEqual(meeting.reminder_rules.count(), 2)
        self.assertTrue(meeting.attendees.filter(is_organizer=True, user_id=USER_A).exists())
        self.assertIsNotNone(meeting.recurrence_end_at)
        self.assertEqual(response.data['color_key'], 'demo')

    def test_attendee_without_identity_is_rejected(self):
        self._auth(USER_A, ALL_MEETING_PERMS)
        response = self.client.post(reverse('meeting-list'), {
            'title': 'Ghost', 'start_at': '2026-09-01T09:30:00Z',
            'end_at': '2026-09-01T10:30:00Z',
            'attendees': [{'role': 'REQUIRED'}],
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def _make_meeting(self, owner=USER_A, **kwargs):
        defaults = dict(
            tenant_id=TENANT_A, title='Sync', start_at=self.start,
            end_at=self.start + timedelta(hours=1), owner_user_id=owner,
        )
        defaults.update(kwargs)
        meeting = Meeting.objects.create(**defaults)
        MeetingAttendee.objects.create(
            tenant_id=TENANT_A, meeting=meeting, user_id=owner,
            role='ORGANIZER', is_organizer=True,
            response_status=AttendeeResponseEnum.ACCEPTED,
        )
        return meeting

    def test_attendee_with_own_scope_can_rsvp_to_someone_elses_meeting(self):
        meeting = self._make_meeting(owner=USER_A)
        MeetingAttendee.objects.create(
            tenant_id=TENANT_A, meeting=meeting, user_id=USER_B
        )
        self._auth(USER_B, OWN_MEETING_PERMS)
        response = self.client.post(
            reverse('meeting-rsvp', args=[meeting.pk]),
            {'response_status': 'ACCEPTED'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        attendee = meeting.attendees.get(user_id=USER_B)
        self.assertEqual(attendee.response_status, 'ACCEPTED')
        self.assertIsNotNone(attendee.responded_at)

    def test_non_attendee_with_own_scope_cannot_rsvp(self):
        meeting = self._make_meeting(owner=USER_A)
        self._auth(USER_B, OWN_MEETING_PERMS)
        response = self.client.post(
            reverse('meeting-rsvp', args=[meeting.pk]),
            {'response_status': 'ACCEPTED'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_cancel_keeps_the_row_visible(self):
        meeting = self._make_meeting()
        self._auth(USER_A, ALL_MEETING_PERMS)
        response = self.client.post(
            reverse('meeting-cancel', args=[meeting.pk]),
            {'reason': 'Client rescheduled'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        meeting.refresh_from_db()
        self.assertEqual(meeting.status, MeetingStatusEnum.CANCELLED)
        self.assertEqual(meeting.cancellation_reason, 'Client rescheduled')
        self.assertFalse(meeting.is_deleted)

    def test_complete_sets_completed_at(self):
        meeting = self._make_meeting()
        self._auth(USER_A, ALL_MEETING_PERMS)
        response = self.client.post(
            reverse('meeting-complete', args=[meeting.pk]),
            {'notes': 'Went well'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        meeting.refresh_from_db()
        self.assertEqual(meeting.status, MeetingStatusEnum.COMPLETED)
        self.assertIsNotNone(meeting.completed_at)
        self.assertIn('Went well', meeting.notes)

    def test_destroy_is_a_soft_delete_and_hidden_from_list(self):
        meeting = self._make_meeting()
        self._auth(USER_A, ALL_MEETING_PERMS)
        self.client.delete(reverse('meeting-detail', args=[meeting.pk]))
        meeting.refresh_from_db()
        self.assertTrue(meeting.is_deleted)

        listing = self.client.get(reverse('meeting-list'))
        ids = [row['id'] for row in listing.data['results']]
        self.assertNotIn(meeting.pk, ids)

        listing = self.client.get(reverse('meeting-list'), {'is_deleted': 'true'})
        ids = [row['id'] for row in listing.data['results']]
        self.assertIn(meeting.pk, ids)

    def test_add_and_remove_attendees(self):
        meeting = self._make_meeting()
        self._auth(USER_A, ALL_MEETING_PERMS)
        response = self.client.post(
            reverse('meeting-attendees', args=[meeting.pk]),
            {'attendees': [{'email': 'guest@example.com', 'display_name': 'Guest'}]},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        attendee = meeting.attendees.get(email='guest@example.com')

        response = self.client.delete(
            reverse('meeting-remove-attendee', args=[meeting.pk, attendee.pk])
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(meeting.attendees.filter(pk=attendee.pk).exists())

    def test_owner_scope_hides_other_peoples_meetings(self):
        self._make_meeting(owner=USER_A)
        self._make_meeting(owner=USER_B)
        self._auth(USER_A, OWN_MEETING_PERMS)
        response = self.client.get(reverse('meeting-list'))
        self.assertEqual(response.data['count'], 1)


# ---------------------------------------------------------------------------
# Reminders
# ---------------------------------------------------------------------------
class MeetingReminderMaterializationTest(TestCase):
    """The least obvious blocker: notifications.Reminder was hard-wired to one
    active reminder per (tenant, lead, recipient) and lead was NOT NULL."""

    def setUp(self):
        from django.utils import timezone as dj_timezone
        self.now = dj_timezone.now()
        self.start = self.now + timedelta(days=3)
        self.lead_status = LeadStatus.objects.create(
            tenant_id=TENANT_A, name='New', order_index=1
        )
        self.lead = Lead.objects.create(
            tenant_id=TENANT_A, name='Acme', phone='999', status=self.lead_status,
            owner_user_id=USER_A,
        )

    def _meeting(self, lead=None):
        meeting = Meeting.objects.create(
            tenant_id=TENANT_A, title='Demo', start_at=self.start,
            end_at=self.start + timedelta(hours=1), owner_user_id=USER_A, lead=lead,
        )
        MeetingAttendee.objects.create(
            tenant_id=TENANT_A, meeting=meeting, user_id=USER_A,
            role='ORGANIZER', is_organizer=True,
        )
        return meeting

    def test_multiple_reminders_per_meeting(self):
        from meetings.tasks import materialize_meeting_reminders
        meeting = self._meeting(lead=self.lead)
        MeetingReminder.objects.create(tenant_id=TENANT_A, meeting=meeting,
                                       minutes_before=1440)
        MeetingReminder.objects.create(tenant_id=TENANT_A, meeting=meeting,
                                       minutes_before=10)

        result = materialize_meeting_reminders()
        self.assertEqual(result['created'], 2)
        offsets = sorted(
            Reminder.objects.filter(meeting=meeting).values_list('offset_minutes', flat=True)
        )
        self.assertEqual(offsets, [10, 1440])
        self.assertTrue(all(
            row.subject_type == ReminderSubjectType.MEETING
            for row in Reminder.objects.filter(meeting=meeting)
        ))

    def test_non_lead_linked_meeting_can_have_a_reminder(self):
        from meetings.tasks import materialize_meeting_reminders
        meeting = self._meeting(lead=None)
        MeetingReminder.objects.create(tenant_id=TENANT_A, meeting=meeting,
                                       minutes_before=30)
        materialize_meeting_reminders()
        reminder = Reminder.objects.get(meeting=meeting)
        self.assertIsNone(reminder.lead_id)
        self.assertEqual(reminder.status, ReminderStatus.PENDING)

    def test_materialization_is_idempotent(self):
        from meetings.tasks import materialize_meeting_reminders
        meeting = self._meeting(lead=self.lead)
        MeetingReminder.objects.create(tenant_id=TENANT_A, meeting=meeting,
                                       minutes_before=60)
        materialize_meeting_reminders()
        materialize_meeting_reminders()
        self.assertEqual(Reminder.objects.filter(meeting=meeting).count(), 1)

    def test_recurring_meeting_gets_one_reminder_per_occurrence(self):
        from meetings.tasks import materialize_meeting_reminders
        rule = 'FREQ=DAILY;COUNT=4'
        meeting = Meeting.objects.create(
            tenant_id=TENANT_A, title='Daily', start_at=self.start,
            end_at=self.start + timedelta(minutes=30), owner_user_id=USER_A,
            recurrence_rule=rule,
            recurrence_end_at=recurrence.compute_recurrence_end(rule, self.start, 'UTC'),
        )
        MeetingReminder.objects.create(tenant_id=TENANT_A, meeting=meeting,
                                       minutes_before=15)
        materialize_meeting_reminders()
        self.assertEqual(Reminder.objects.filter(meeting=meeting).count(), 4)
        starts = set(
            Reminder.objects.filter(meeting=meeting)
            .values_list('occurrence_start_at', flat=True)
        )
        self.assertEqual(len(starts), 4)

    def test_lead_follow_up_constraint_still_holds(self):
        """The follow-up rule (one active reminder per lead+recipient) is intact."""
        Reminder.objects.create(
            tenant_id=TENANT_A, lead=self.lead, recipient_user_id=USER_A,
            created_by_user_id=USER_A, follow_up_at=self.start, remind_at=self.start,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Reminder.objects.create(
                    tenant_id=TENANT_A, lead=self.lead, recipient_user_id=USER_A,
                    created_by_user_id=USER_A, follow_up_at=self.start,
                    remind_at=self.start,
                )

    def test_meeting_reminders_do_not_collide_with_the_lead_follow_up(self):
        from meetings.tasks import materialize_meeting_reminders
        Reminder.objects.create(
            tenant_id=TENANT_A, lead=self.lead, recipient_user_id=USER_A,
            created_by_user_id=USER_A, follow_up_at=self.start, remind_at=self.start,
        )
        meeting = self._meeting(lead=self.lead)
        MeetingReminder.objects.create(tenant_id=TENANT_A, meeting=meeting,
                                       minutes_before=45)
        materialize_meeting_reminders()
        self.assertEqual(Reminder.objects.filter(lead=self.lead).count(), 2)

    def test_delivery_produces_a_meeting_notification(self):
        from notifications.models import Notification
        from notifications.tasks import dispatch_due_reminders
        from django.utils import timezone as dj_timezone

        meeting = self._meeting(lead=self.lead)
        Reminder.objects.create(
            tenant_id=TENANT_A, meeting=meeting, lead=self.lead,
            occurrence_start_at=self.start,
            subject_type=ReminderSubjectType.MEETING,
            recipient_user_id=USER_A, created_by_user_id=USER_A,
            follow_up_at=self.start,
            remind_at=dj_timezone.now() - timedelta(minutes=1),
            offset_minutes=10,
        )
        dispatch_due_reminders()
        notification = Notification.objects.get()
        self.assertEqual(notification.notification_type, 'MEETING_REMINDER')
        self.assertTrue(notification.dedupe_key.startswith('meeting-reminder:'))
        self.assertEqual(notification.payload['meeting_id'], meeting.pk)


# ---------------------------------------------------------------------------
# Legacy scope test (kept)
# ---------------------------------------------------------------------------
class MeetingCalendarScopeTest(MeetingAuthMixin, APITestCase):
    """P2: calendar endpoint must only return meetings within the user's view scope."""

    def setUp(self):
        self.status = LeadStatus.objects.create(
            tenant_id=TENANT_A, name='New', order_index=1,
        )
        self.lead = Lead.objects.create(
            tenant_id=TENANT_A, name='Lead', phone='1111111111',
            status=self.status, owner_user_id=USER_A,
        )
        base = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)
        self.meeting_a = Meeting.objects.create(
            tenant_id=TENANT_A, title='Meeting A', start_at=base,
            end_at=base + timedelta(hours=1), owner_user_id=USER_A,
        )
        self.meeting_b = Meeting.objects.create(
            tenant_id=TENANT_A, title='Meeting B', start_at=base + timedelta(days=1),
            end_at=base + timedelta(days=1, hours=1), owner_user_id=USER_B,
        )

    def test_calendar_only_returns_own_meetings(self):
        self._auth(USER_A)
        response = self.client.get(reverse('meeting-calendar'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['total_meetings'], 1)


class SeriesSplitUnitTest(TestCase):
    """services.split_series in isolation."""

    def test_exdates_are_partitioned_between_the_two_masters(self):
        start = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
        split_at = start + timedelta(days=14)
        rule = 'FREQ=WEEKLY;COUNT=8'
        master = Meeting.objects.create(
            tenant_id=TENANT_A, title='S', start_at=start,
            end_at=start + timedelta(hours=1), owner_user_id=USER_A,
            recurrence_rule=rule, timezone='UTC',
            recurrence_exdates=[
                (start + timedelta(days=7)).isoformat(),
                (start + timedelta(days=21)).isoformat(),
            ],
        )
        master, new_master = services.split_series(master, split_at)
        self.assertEqual(len(master.recurrence_exdates), 1)
        self.assertEqual(len(new_master.recurrence_exdates), 1)
        self.assertIn('UNTIL=', master.recurrence_rule)

    def test_overrides_after_the_split_are_reparented(self):
        start = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
        split_at = start + timedelta(days=14)
        master = Meeting.objects.create(
            tenant_id=TENANT_A, title='S', start_at=start,
            end_at=start + timedelta(hours=1), owner_user_id=USER_A,
            recurrence_rule='FREQ=WEEKLY;COUNT=8', timezone='UTC',
        )
        early, _ = services.get_or_create_override(master, start + timedelta(days=7))
        late, _ = services.get_or_create_override(master, start + timedelta(days=21))

        master, new_master = services.split_series(master, split_at)
        early.refresh_from_db()
        late.refresh_from_db()
        self.assertEqual(early.recurring_parent_id, master.pk)
        self.assertEqual(late.recurring_parent_id, new_master.pk)
