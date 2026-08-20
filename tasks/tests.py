"""Tests for CRM-wide tasks.

Covers the six things the redesign had to get right:

* a task can exist with no lead at all;
* deleting a lead no longer deletes its tasks;
* ``related_type``/``related_id`` resolution is tenant-scoped and cannot leak;
* an assignee can see a task their manager created (``own`` scope);
* the checklist JSON -> rows migration round-trips;
* completing a recurring task spawns the next occurrence;
* ``my-day`` buckets in the caller's timezone, not UTC.
"""
import importlib
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import jwt as pyjwt
from django.apps import apps as django_apps
from django.conf import settings
from django.test import TestCase
from django.utils import timezone as dj_timezone
from rest_framework import status
from rest_framework.test import APITestCase

from crm.models import Lead
from meetings.models import Meeting
from notifications.models import Notification, Reminder, ReminderStatus, ReminderSubjectType
from notifications.tasks import dispatch_due_reminders
from real_estate.models import Project, Unit
from tasks import services
from tasks.models import Task, TaskChecklistItem, TaskRelatedTypeEnum, TaskStatusEnum
from tasks.tasks import materialize_for_task

TEST_JWT_SECRET = getattr(settings, 'JWT_SECRET_KEY', 'test-secret')
TEST_JWT_ALGO = getattr(settings, 'JWT_ALGORITHM', 'HS256')

TENANT_A = uuid.UUID('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
TENANT_B = uuid.UUID('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb')
MANAGER = uuid.UUID('cccccccc-cccc-cccc-cccc-cccccccccccc')
WORKER = uuid.UUID('dddddddd-dddd-dddd-dddd-dddddddddddd')
STRANGER = uuid.UUID('eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee')

IST = ZoneInfo('Asia/Kolkata')

ALL_TASK_PERMS = {'crm': {'tasks': {
    'view': 'all', 'create': True, 'edit': 'all', 'delete': 'all',
}}}
OWN_TASK_PERMS = {'crm': {'tasks': {
    'view': 'own', 'create': True, 'edit': 'own', 'delete': 'own',
}}}


def _make_token(user_id, tenant_id=TENANT_A, permissions=None):
    payload = {
        'user_id': str(user_id),
        'email': f'{user_id}@test.com',
        'tenant_id': str(tenant_id),
        'tenant_slug': 'test',
        'is_super_admin': False,
        'permissions': permissions or ALL_TASK_PERMS,
        'enabled_modules': ['crm'],
        'roles': [],
        'exp': datetime.now(timezone.utc) + timedelta(hours=6),
    }
    return pyjwt.encode(payload, TEST_JWT_SECRET, algorithm=TEST_JWT_ALGO)


class TaskAuthMixin:
    def _auth(self, user_id, tenant_id=TENANT_A, permissions=None):
        token = _make_token(user_id, tenant_id=tenant_id, permissions=permissions)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')


def make_lead(tenant_id=TENANT_A, name='Acme', owner=MANAGER):
    return Lead.objects.create(
        tenant_id=tenant_id, name=name, phone='+919000000000', owner_user_id=owner
    )


def make_project(tenant_id, name='Skyline Towers'):
    return Project.objects.create(
        tenant_id=tenant_id, name=name, project_type='RESIDENTIAL',
        status='UNDER_CONSTRUCTION', created_by_user_id=MANAGER,
    )


# ---------------------------------------------------------------------------
# 1. Standalone tasks + lead deletion
# ---------------------------------------------------------------------------
class StandaloneTaskTest(TestCase):
    """``lead`` is nullable and SET_NULL now."""

    def test_task_can_exist_with_no_lead_at_all(self):
        task = Task.objects.create(
            tenant_id=TENANT_A, title='Renew the office lease', owner_user_id=MANAGER
        )
        task.refresh_from_db()
        self.assertIsNone(task.lead_id)
        self.assertEqual(task.related_type, TaskRelatedTypeEnum.NONE)
        self.assertIsNone(task.related_id)
        # __str__ must not blow up on a null lead.
        self.assertEqual(str(task), 'Renew the office lease')

    def test_deleting_a_lead_no_longer_destroys_its_tasks(self):
        lead = make_lead()
        task = Task.objects.create(
            tenant_id=TENANT_A, lead=lead, title='Call back', owner_user_id=MANAGER
        )
        lead_id = lead.id
        lead.delete()

        task.refresh_from_db()
        self.assertTrue(Task.objects.filter(pk=task.pk).exists())
        self.assertIsNone(task.lead_id)
        # The orphaned pointer is left visible so the row can be re-homed; it
        # simply no longer resolves to a label.
        self.assertEqual(task.related_id, lead_id)
        self.assertIsNone(
            services.resolve_related_label(TENANT_A, task.related_type, task.related_id)
        )

    def test_bare_lead_is_promoted_to_lead_linkage(self):
        """Old-style create (``lead`` only) still produces a linked task."""
        lead = make_lead()
        task = Task.objects.create(
            tenant_id=TENANT_A, lead=lead, title='Send quote', owner_user_id=MANAGER
        )
        self.assertEqual(task.related_type, TaskRelatedTypeEnum.LEAD)
        self.assertEqual(task.related_id, lead.id)

    def test_related_id_back_fills_the_lead_fk(self):
        lead = make_lead()
        task = Task.objects.create(
            tenant_id=TENANT_A, title='Send quote', owner_user_id=MANAGER,
            related_type=TaskRelatedTypeEnum.LEAD, related_id=lead.id,
        )
        self.assertEqual(task.lead_id, lead.id)


# ---------------------------------------------------------------------------
# 2. Tenant-scoped related resolution
# ---------------------------------------------------------------------------
class RelatedResolutionTenantScopeTest(TestCase):
    def setUp(self):
        self.project_a = make_project(TENANT_A, 'Ours')
        self.project_b = make_project(TENANT_B, 'Theirs')

    def test_label_resolves_within_the_tenant(self):
        self.assertEqual(
            services.resolve_related_label(
                TENANT_A, TaskRelatedTypeEnum.PROJECT, self.project_a.id
            ),
            'Ours',
        )

    def test_another_tenants_project_never_resolves(self):
        """The whole point of the explicit type+id pair: no cross-tenant leak."""
        self.assertIsNone(
            services.resolve_related_label(
                TENANT_A, TaskRelatedTypeEnum.PROJECT, self.project_b.id
            )
        )
        self.assertFalse(
            services.related_exists(TENANT_A, TaskRelatedTypeEnum.PROJECT, self.project_b.id)
        )

    def test_bulk_resolution_is_tenant_scoped_too(self):
        labels = services.resolve_related_labels(TENANT_A, [
            (TaskRelatedTypeEnum.PROJECT, self.project_a.id),
            (TaskRelatedTypeEnum.PROJECT, self.project_b.id),
        ])
        self.assertEqual(labels, {(TaskRelatedTypeEnum.PROJECT, self.project_a.id): 'Ours'})

    def test_unit_and_meeting_labels(self):
        unit = Unit.objects.create(
            tenant_id=TENANT_A, project=self.project_a, unit_type='FLAT',
            unit_number='A-1203', status='AVAILABLE',
        )
        start = dj_timezone.now()
        meeting = Meeting.objects.create(
            tenant_id=TENANT_A, title='Site visit', start_at=start,
            end_at=start + timedelta(hours=1), owner_user_id=MANAGER,
        )
        self.assertEqual(
            services.resolve_related_label(TENANT_A, TaskRelatedTypeEnum.UNIT, unit.id),
            'A-1203',
        )
        self.assertEqual(
            services.resolve_related_label(TENANT_A, TaskRelatedTypeEnum.MEETING, meeting.id),
            'Site visit',
        )


class RelatedResolutionApiTest(TaskAuthMixin, APITestCase):
    def setUp(self):
        self.project_a = make_project(TENANT_A, 'Ours')
        self.project_b = make_project(TENANT_B, 'Theirs')

    def test_create_rejects_another_tenants_related_id(self):
        self._auth(MANAGER)
        response = self.client.post('/api/tasks/', {
            'title': 'Snoop', 'related_type': 'PROJECT', 'related_id': self.project_b.id,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('related_id', response.data)

    def test_create_accepts_own_tenant_related_id_and_returns_the_label(self):
        self._auth(MANAGER)
        response = self.client.post('/api/tasks/', {
            'title': 'Snag list', 'related_type': 'PROJECT', 'related_id': self.project_a.id,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data['related_label'], 'Ours')
        self.assertIsNone(response.data['lead'])

    def test_standalone_task_via_api(self):
        self._auth(MANAGER)
        response = self.client.post(
            '/api/tasks/', {'title': 'Order stationery'}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertIsNone(response.data['lead'])
        self.assertEqual(response.data['related_type'], 'NONE')
        self.assertIsNone(response.data['related_label'])

    def test_a_stale_cross_tenant_pointer_serializes_as_null_label(self):
        """Even a row written straight to the DB must not disclose the name."""
        task = Task.objects.create(
            tenant_id=TENANT_A, title='Planted', owner_user_id=MANAGER,
            related_type=TaskRelatedTypeEnum.PROJECT, related_id=self.project_b.id,
        )
        self._auth(MANAGER)
        response = self.client.get(f'/api/tasks/{task.id}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data['related_label'])
        self.assertNotIn('Theirs', str(response.data))


# ---------------------------------------------------------------------------
# 3. Ownership / permission scoping
# ---------------------------------------------------------------------------
class TaskOwnershipScopeTest(TaskAuthMixin, APITestCase):
    """A task assigned to you but raised by your manager is yours to see."""

    def setUp(self):
        self.assigned_to_worker = Task.objects.create(
            tenant_id=TENANT_A, title='Chase the paperwork',
            owner_user_id=MANAGER, assignee_user_id=WORKER,
        )
        self.managers_own = Task.objects.create(
            tenant_id=TENANT_A, title='Board deck', owner_user_id=MANAGER,
        )
        self.someone_elses = Task.objects.create(
            tenant_id=TENANT_A, title='Not yours', owner_user_id=STRANGER,
        )

    def test_assignee_sees_their_task_under_own_scope(self):
        self._auth(WORKER, permissions=OWN_TASK_PERMS)
        response = self.client.get('/api/tasks/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        titles = {row['title'] for row in response.data['results']}
        self.assertIn('Chase the paperwork', titles)
        self.assertNotIn('Board deck', titles)
        self.assertNotIn('Not yours', titles)

    def test_assignee_can_retrieve_their_task_under_own_scope(self):
        self._auth(WORKER, permissions=OWN_TASK_PERMS)
        response = self.client.get(f'/api/tasks/{self.assigned_to_worker.id}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_own_scope_still_excludes_unrelated_tasks(self):
        self._auth(WORKER, permissions=OWN_TASK_PERMS)
        response = self.client.get(f'/api/tasks/{self.someone_elses.id}/')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_owner_still_sees_their_own_task(self):
        self._auth(MANAGER, permissions=OWN_TASK_PERMS)
        response = self.client.get('/api/tasks/')
        titles = {row['title'] for row in response.data['results']}
        self.assertEqual(titles, {'Chase the paperwork', 'Board deck'})

    def test_tasks_from_another_tenant_are_invisible(self):
        Task.objects.create(
            tenant_id=TENANT_B, title='Other tenant', owner_user_id=WORKER,
        )
        self._auth(WORKER, permissions=OWN_TASK_PERMS)
        response = self.client.get('/api/tasks/')
        titles = {row['title'] for row in response.data['results']}
        self.assertNotIn('Other tenant', titles)


# ---------------------------------------------------------------------------
# 4. Checklist
# ---------------------------------------------------------------------------
class ChecklistMigrationTest(TestCase):
    """The JSON blob -> rows data migration must round-trip."""

    def setUp(self):
        self.migration = importlib.import_module(
            'tasks.migrations.0003_migrate_checklist_json'
        )

    def _task(self, checklist, title='T'):
        return Task.objects.create(
            tenant_id=TENANT_A, title=title, owner_user_id=MANAGER, checklist=checklist
        )

    def test_forwards_handles_every_historic_shape(self):
        plain = self._task(['Call the lead', 'Send brochure'], 'plain')
        dicts = self._task(
            [{'text': 'Collect PAN', 'done': True},
             {'title': 'Collect Aadhaar', 'completed': False, 'order': 5}],
            'dicts',
        )
        wrapped = self._task({'items': ['Only one']}, 'wrapped')
        junk = self._task([None, 42, {'nope': 'x'}], 'junk')
        empty = self._task(None, 'empty')

        self.migration.forwards(django_apps, None)

        self.assertEqual(
            list(plain.checklist_items.values_list('text', 'is_done', 'order_index')),
            [('Call the lead', False, 0), ('Send brochure', False, 1)],
        )
        self.assertEqual(
            list(dicts.checklist_items.values_list('text', 'is_done', 'order_index')),
            [('Collect PAN', True, 0), ('Collect Aadhaar', False, 5)],
        )
        self.assertEqual(wrapped.checklist_items.count(), 1)
        self.assertEqual(junk.checklist_items.count(), 0)
        self.assertEqual(empty.checklist_items.count(), 0)

        # The original column is deliberately left populated so this is reversible.
        plain.refresh_from_db()
        self.assertEqual(plain.checklist, ['Call the lead', 'Send brochure'])

    def test_forwards_is_idempotent(self):
        task = self._task(['One', 'Two'])
        self.migration.forwards(django_apps, None)
        self.migration.forwards(django_apps, None)
        self.assertEqual(task.checklist_items.count(), 2)

    def test_round_trip_back_to_json(self):
        task = self._task(['Call the lead', 'Send brochure'])
        self.migration.forwards(django_apps, None)
        # Something added through the new endpoints after the migration ran.
        TaskChecklistItem.objects.create(
            tenant_id=TENANT_A, task=task, text='Added later', order_index=2
        )

        self.migration.backwards(django_apps, None)

        task.refresh_from_db()
        self.assertEqual(TaskChecklistItem.objects.count(), 0)
        self.assertEqual([row['text'] for row in task.checklist],
                         ['Call the lead', 'Send brochure', 'Added later'])
        self.assertEqual([row['is_done'] for row in task.checklist],
                         [False, False, False])


class ChecklistEndpointTest(TaskAuthMixin, APITestCase):
    def setUp(self):
        self.task = Task.objects.create(
            tenant_id=TENANT_A, title='Onboarding', owner_user_id=MANAGER
        )
        self._auth(MANAGER)

    def test_post_appends_and_get_lists_in_order(self):
        for text in ('First', 'Second'):
            response = self.client.post(
                f'/api/tasks/{self.task.id}/checklist/', {'text': text}, format='json'
            )
            self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)

        response = self.client.get(f'/api/tasks/{self.task.id}/checklist/')
        self.assertEqual([row['text'] for row in response.data], ['First', 'Second'])
        self.assertEqual([row['order_index'] for row in response.data], [0, 1])

    def test_patch_ticks_an_item_and_stamps_done_at(self):
        item = TaskChecklistItem.objects.create(
            tenant_id=TENANT_A, task=self.task, text='Collect PAN'
        )
        response = self.client.patch(
            f'/api/tasks/{self.task.id}/checklist/{item.id}/',
            {'is_done': True}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertTrue(response.data['is_done'])
        self.assertIsNotNone(response.data['done_at'])

    def test_delete_removes_the_item(self):
        item = TaskChecklistItem.objects.create(
            tenant_id=TENANT_A, task=self.task, text='Obsolete'
        )
        response = self.client.delete(
            f'/api/tasks/{self.task.id}/checklist/{item.id}/'
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(TaskChecklistItem.objects.filter(pk=item.id).exists())

    def test_item_from_another_task_is_not_reachable(self):
        other = Task.objects.create(
            tenant_id=TENANT_A, title='Other', owner_user_id=MANAGER
        )
        item = TaskChecklistItem.objects.create(
            tenant_id=TENANT_A, task=other, text='Not mine'
        )
        response = self.client.patch(
            f'/api/tasks/{self.task.id}/checklist/{item.id}/',
            {'is_done': True}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_counts_are_exposed_on_the_task(self):
        TaskChecklistItem.objects.create(
            tenant_id=TENANT_A, task=self.task, text='A', is_done=True
        )
        TaskChecklistItem.objects.create(tenant_id=TENANT_A, task=self.task, text='B')
        response = self.client.get(f'/api/tasks/{self.task.id}/')
        self.assertEqual(response.data['checklist_total_count'], 2)
        self.assertEqual(response.data['checklist_done_count'], 1)
        self.assertEqual(len(response.data['checklist_items']), 2)


# ---------------------------------------------------------------------------
# 5. Recurrence
# ---------------------------------------------------------------------------
class RecurrenceTest(TaskAuthMixin, APITestCase):
    def setUp(self):
        # 2026-09-07 is a Monday.
        self.due = datetime(2026, 9, 7, 9, 0, tzinfo=timezone.utc)
        self.task = Task.objects.create(
            tenant_id=TENANT_A, title='Weekly pipeline review',
            owner_user_id=MANAGER, assignee_user_id=WORKER,
            due_date=self.due, rrule='FREQ=WEEKLY;BYDAY=MO', timezone='UTC',
        )

    def test_recurrence_end_is_denormalised_from_count(self):
        task = Task.objects.create(
            tenant_id=TENANT_A, title='Three times', owner_user_id=MANAGER,
            due_date=self.due, rrule='FREQ=DAILY;COUNT=3',
        )
        self.assertEqual(
            task.recurrence_end_at, self.due + timedelta(days=2)
        )

    def test_open_ended_series_has_no_recurrence_end(self):
        self.assertIsNone(self.task.recurrence_end_at)

    def test_completing_spawns_the_next_occurrence(self):
        self._auth(MANAGER)
        response = self.client.post(f'/api/tasks/{self.task.id}/complete/')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['status'], 'DONE')
        self.assertIsNotNone(response.data['completed_at'])

        following = response.data['next_task']
        self.assertIsNotNone(following)
        self.assertEqual(following['status'], 'TODO')
        self.assertEqual(following['recurring_parent'], self.task.id)
        self.assertEqual(
            following['due_date'], (self.due + timedelta(days=7)).isoformat().replace('+00:00', 'Z')
        )

    def test_the_series_keeps_rolling_and_never_forks(self):
        second = services.spawn_next_occurrence(self.task)
        third = services.spawn_next_occurrence(second)
        self.assertEqual(second.recurring_parent_id, self.task.id)
        # Grandchildren still point at the head of the series, not at their parent.
        self.assertEqual(third.recurring_parent_id, self.task.id)
        self.assertEqual(third.due_date, self.due + timedelta(days=14))

    def test_spawning_is_idempotent(self):
        first = services.spawn_next_occurrence(self.task)
        again = services.spawn_next_occurrence(self.task)
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(Task.objects.filter(recurring_parent=self.task).count(), 1)

    def test_a_recurring_checklist_comes_back_unticked(self):
        TaskChecklistItem.objects.create(
            tenant_id=TENANT_A, task=self.task, text='Pull the numbers', is_done=True
        )
        following = services.spawn_next_occurrence(self.task)
        items = list(following.checklist_items.values_list('text', 'is_done'))
        self.assertEqual(items, [('Pull the numbers', False)])

    def test_completing_a_one_off_spawns_nothing(self):
        one_off = Task.objects.create(
            tenant_id=TENANT_A, title='One off', owner_user_id=MANAGER, due_date=self.due
        )
        self._auth(MANAGER)
        response = self.client.post(f'/api/tasks/{one_off.id}/complete/')
        self.assertIsNone(response.data['next_task'])

    def test_complete_toggles_back_off(self):
        self._auth(MANAGER)
        done = Task.objects.create(
            tenant_id=TENANT_A, title='Toggle me', owner_user_id=MANAGER,
            status=TaskStatusEnum.DONE,
        )
        response = self.client.post(f'/api/tasks/{done.id}/complete/')
        self.assertEqual(response.data['status'], 'TODO')
        self.assertIsNone(response.data['completed_at'])

    def test_an_invalid_rrule_is_rejected_at_the_api(self):
        self._auth(MANAGER)
        response = self.client.post('/api/tasks/', {
            'title': 'Bad rule', 'due_date': self.due.isoformat(),
            'rrule': 'FREQ=NOPE;INTERVAL=banana',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('rrule', response.data)

    def test_dst_safe_expansion_uses_the_authored_timezone(self):
        """A 09:00 IST daily task stays 09:00 IST, not a drifting UTC offset."""
        anchor = datetime(2026, 3, 7, 3, 30, tzinfo=timezone.utc)  # 09:00 IST
        task = Task.objects.create(
            tenant_id=TENANT_A, title='Daily standup', owner_user_id=MANAGER,
            due_date=anchor, rrule='FREQ=DAILY', timezone='Asia/Kolkata',
        )
        following = services.next_occurrence_after(task)
        self.assertEqual(following.astimezone(IST).hour, 9)
        self.assertEqual(following.astimezone(IST).minute, 0)


# ---------------------------------------------------------------------------
# 6. my-day
# ---------------------------------------------------------------------------
class MyDayBucketTest(TestCase):
    """Bucketing must use the caller's local calendar day, not the UTC one."""

    def setUp(self):
        # 2026-09-07 21:00 UTC == 2026-09-08 02:30 IST. The UTC day and the IST
        # day disagree, which is exactly the case that used to file an IST user's
        # early-morning task under "overdue".
        self.now = datetime(2026, 9, 7, 21, 0, tzinfo=timezone.utc)

    def _task(self, title, due=None, **kwargs):
        return Task.objects.create(
            tenant_id=TENANT_A, title=title, owner_user_id=MANAGER,
            due_date=due, **kwargs
        )

    def test_grouping_in_a_non_utc_timezone(self):
        # 03:00 IST on 8 Sep -- "today" for the IST user, "tomorrow" in UTC terms.
        today_ist = self._task('Morning call', datetime(2026, 9, 7, 21, 30, tzinfo=timezone.utc))
        overdue = self._task('Late', datetime(2026, 9, 6, 5, 0, tzinfo=timezone.utc))
        this_week = self._task('Thursday', datetime(2026, 9, 10, 5, 0, tzinfo=timezone.utc))
        later = self._task('Next month', datetime(2026, 10, 20, 5, 0, tzinfo=timezone.utc))
        undated = self._task('Someday')
        done_today = self._task('Finished', datetime(2026, 9, 7, 22, 0, tzinfo=timezone.utc))
        done_today.status = TaskStatusEnum.DONE
        done_today.completed_at = datetime(2026, 9, 7, 21, 5, tzinfo=timezone.utc)
        Task.objects.filter(pk=done_today.pk).update(
            status=TaskStatusEnum.DONE, completed_at=done_today.completed_at
        )
        done_today.refresh_from_db()

        rows = list(Task.objects.filter(tenant_id=TENANT_A))
        groups = services.bucket_for_my_day(rows, 'Asia/Kolkata', now=self.now)

        self.assertEqual([t.id for t in groups['today']], [today_ist.id])
        self.assertEqual([t.id for t in groups['overdue']], [overdue.id])
        self.assertEqual([t.id for t in groups['this_week']], [this_week.id])
        self.assertEqual(
            sorted(t.id for t in groups['later']), sorted([later.id, undated.id])
        )
        self.assertEqual([t.id for t in groups['done_today']], [done_today.id])

    def test_the_same_data_buckets_differently_in_utc(self):
        """Proof the timezone argument actually does something.

        ``now`` is 2026-09-07 21:00 UTC, which is already 2026-09-08 02:30 IST.
        A task due 2026-09-08 10:00 UTC (15:30 IST) is therefore *today* for the
        IST user but still *later this week* for a UTC one -- bucket on the UTC
        date and every IST user's day is wrong by one.
        """
        task = self._task('Afternoon', datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc))

        ist_groups = services.bucket_for_my_day([task], 'Asia/Kolkata', now=self.now)
        utc_groups = services.bucket_for_my_day([task], 'UTC', now=self.now)

        self.assertEqual([t.id for t in ist_groups['today']], [task.id])
        self.assertEqual(ist_groups['this_week'], [])
        self.assertEqual(utc_groups['today'], [])
        self.assertEqual([t.id for t in utc_groups['this_week']], [task.id])

    def test_cancelled_tasks_appear_nowhere(self):
        self._task('Dropped', self.now, status=TaskStatusEnum.CANCELLED)
        groups = services.bucket_for_my_day(
            list(Task.objects.all()), 'Asia/Kolkata', now=self.now
        )
        self.assertEqual(sum(len(v) for v in groups.values()), 0)


class MyDayEndpointTest(TaskAuthMixin, APITestCase):
    def test_endpoint_returns_every_group_and_echoes_the_timezone(self):
        Task.objects.create(
            tenant_id=TENANT_A, title='Mine', owner_user_id=MANAGER,
            due_date=dj_timezone.now() + timedelta(hours=2),
        )
        Task.objects.create(
            tenant_id=TENANT_A, title='Someone else\'s', owner_user_id=STRANGER,
        )
        self._auth(MANAGER)
        response = self.client.get('/api/tasks/my-day/?timezone=Asia/Kolkata')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        for group in ('overdue', 'today', 'this_week', 'later', 'done_today'):
            self.assertIn(group, response.data)
        self.assertEqual(response.data['timezone'], 'Asia/Kolkata')

        titles = {
            row['title']
            for group in ('overdue', 'today', 'this_week', 'later', 'done_today')
            for row in response.data[group]
        }
        self.assertIn('Mine', titles)
        self.assertNotIn("Someone else's", titles)

    def test_a_junk_timezone_falls_back_to_utc_rather_than_erroring(self):
        self._auth(MANAGER)
        response = self.client.get('/api/tasks/my-day/?timezone=Mars/Olympus_Mons')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['timezone'], 'UTC')


# ---------------------------------------------------------------------------
# 7. bulk / reorder
# ---------------------------------------------------------------------------
class BulkAndReorderTest(TaskAuthMixin, APITestCase):
    def setUp(self):
        self.tasks = [
            Task.objects.create(
                tenant_id=TENANT_A, title=f'T{i}', owner_user_id=MANAGER, order_index=i
            )
            for i in range(3)
        ]

    def test_bulk_patch_applies_to_every_listed_task(self):
        self._auth(MANAGER)
        response = self.client.patch('/api/tasks/bulk/', {
            'ids': [t.id for t in self.tasks],
            'patch': {'priority': 'HIGH', 'labels': ['sprint-1']},
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data, {'updated': 3})
        for task in self.tasks:
            task.refresh_from_db()
            self.assertEqual(task.priority, 'HIGH')
            self.assertEqual(task.labels, ['sprint-1'])

    def test_bulk_completion_stamps_completed_at(self):
        self._auth(MANAGER)
        self.client.patch('/api/tasks/bulk/', {
            'ids': [self.tasks[0].id], 'patch': {'status': 'DONE'},
        }, format='json')
        self.tasks[0].refresh_from_db()
        self.assertIsNotNone(self.tasks[0].completed_at)

    def test_bulk_refuses_fields_outside_the_whitelist(self):
        self._auth(MANAGER)
        response = self.client.patch('/api/tasks/bulk/', {
            'ids': [self.tasks[0].id], 'patch': {'tenant_id': str(TENANT_B)},
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_bulk_skips_tasks_outside_your_scope(self):
        mine = Task.objects.create(
            tenant_id=TENANT_A, title='Mine', owner_user_id=WORKER
        )
        theirs = Task.objects.create(
            tenant_id=TENANT_A, title='Theirs', owner_user_id=STRANGER
        )
        self._auth(WORKER, permissions=OWN_TASK_PERMS)
        response = self.client.patch('/api/tasks/bulk/', {
            'ids': [mine.id, theirs.id], 'patch': {'priority': 'HIGH'},
        }, format='json')
        self.assertEqual(response.data, {'updated': 1})
        theirs.refresh_from_db()
        self.assertEqual(theirs.priority, 'MEDIUM')

    def test_bulk_cannot_reach_another_tenant(self):
        outsider = Task.objects.create(
            tenant_id=TENANT_B, title='Outsider', owner_user_id=MANAGER
        )
        self._auth(MANAGER)
        response = self.client.patch('/api/tasks/bulk/', {
            'ids': [outsider.id], 'patch': {'priority': 'HIGH'},
        }, format='json')
        self.assertEqual(response.data, {'updated': 0})
        outsider.refresh_from_db()
        self.assertEqual(outsider.priority, 'MEDIUM')

    def test_reorder_rewrites_order_index_to_list_position(self):
        self._auth(MANAGER)
        new_order = [self.tasks[2].id, self.tasks[0].id, self.tasks[1].id]
        response = self.client.post(
            '/api/tasks/reorder/', {'ids': new_order}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data, {'updated': 3})
        for position, task_id in enumerate(new_order):
            self.assertEqual(Task.objects.get(pk=task_id).order_index, position)


# ---------------------------------------------------------------------------
# 8. Reminders reuse the existing notifications pipeline
# ---------------------------------------------------------------------------
class TaskReminderTest(TaskAuthMixin, APITestCase):
    def test_materialize_creates_a_reminder_for_assignee_and_owner(self):
        task = Task.objects.create(
            tenant_id=TENANT_A, title='Call at 3', owner_user_id=MANAGER,
            assignee_user_id=WORKER, due_date=dj_timezone.now() + timedelta(minutes=30),
            reminder_minutes_before=10,
        )
        created = materialize_for_task(task)
        self.assertEqual(created, 2)
        recipients = set(
            Reminder.objects.filter(task=task).values_list('recipient_user_id', flat=True)
        )
        self.assertEqual(recipients, {WORKER, MANAGER})
        self.assertEqual(
            Reminder.objects.filter(task=task).first().subject_type,
            ReminderSubjectType.TASK,
        )

    def test_materialize_is_idempotent(self):
        task = Task.objects.create(
            tenant_id=TENANT_A, title='Once', owner_user_id=MANAGER,
            due_date=dj_timezone.now() + timedelta(minutes=30),
            reminder_minutes_before=10,
        )
        materialize_for_task(task)
        materialize_for_task(task)
        self.assertEqual(Reminder.objects.filter(task=task).count(), 1)

    def test_the_existing_dispatcher_delivers_a_task_reminder(self):
        """No new delivery infrastructure -- dispatch_due_reminders does the work."""
        task = Task.objects.create(
            tenant_id=TENANT_A, title='Send the contract', owner_user_id=MANAGER,
            due_date=dj_timezone.now() + timedelta(minutes=5),
            reminder_minutes_before=10,  # remind_at is already in the past
        )
        materialize_for_task(task)

        result = dispatch_due_reminders()
        self.assertEqual(result['delivered'], 1)

        notification = Notification.objects.get(recipient_user_id=MANAGER)
        self.assertEqual(notification.notification_type, 'TASK_REMINDER')
        self.assertEqual(notification.title, 'Send the contract')
        self.assertEqual(notification.payload['task_id'], task.id)
        self.assertEqual(
            Reminder.objects.get(task=task).status, ReminderStatus.DELIVERED
        )

    def test_a_lead_reminder_and_a_task_reminder_coexist(self):
        """The uniq_active_lead_reminder predicate had to learn about tasks."""
        lead = make_lead()
        now = dj_timezone.now()
        Reminder.objects.create(
            tenant_id=TENANT_A, lead=lead, recipient_user_id=MANAGER,
            created_by_user_id=MANAGER, follow_up_at=now, remind_at=now,
        )
        task = Task.objects.create(
            tenant_id=TENANT_A, lead=lead, title='Chase', owner_user_id=MANAGER,
            due_date=now + timedelta(minutes=30), reminder_minutes_before=10,
        )
        self.assertEqual(materialize_for_task(task), 1)
        self.assertEqual(Reminder.objects.filter(lead=lead).count(), 2)

    def test_completing_a_task_cancels_its_pending_reminder(self):
        task = Task.objects.create(
            tenant_id=TENANT_A, title='Done soon', owner_user_id=MANAGER,
            due_date=dj_timezone.now() + timedelta(minutes=30),
            reminder_minutes_before=10,
        )
        materialize_for_task(task)
        self._auth(MANAGER)
        self.client.post(f'/api/tasks/{task.id}/complete/')
        self.assertEqual(
            Reminder.objects.get(task=task).status, ReminderStatus.CANCELLED
        )


class SnoozeTest(TaskAuthMixin, APITestCase):
    def test_snooze_moves_the_due_date_forward(self):
        due = dj_timezone.now() + timedelta(hours=1)
        task = Task.objects.create(
            tenant_id=TENANT_A, title='Later', owner_user_id=MANAGER, due_date=due
        )
        self._auth(MANAGER)
        response = self.client.post(
            f'/api/tasks/{task.id}/snooze/', {'minutes': 60}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        task.refresh_from_db()
        self.assertGreater(task.due_date, due)
        self.assertIsNotNone(task.snoozed_until)

    def test_snooze_needs_minutes_or_until(self):
        task = Task.objects.create(
            tenant_id=TENANT_A, title='Later', owner_user_id=MANAGER
        )
        self._auth(MANAGER)
        response = self.client.post(f'/api/tasks/{task.id}/snooze/', {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


# ---------------------------------------------------------------------------
# 9. Backwards compatibility of the pre-existing surface
# ---------------------------------------------------------------------------
class BackwardsCompatibilityTest(TaskAuthMixin, APITestCase):
    """Everything the old API did must keep working."""

    def setUp(self):
        self.lead = make_lead(name='Old Client')
        self.task = Task.objects.create(
            tenant_id=TENANT_A, lead=self.lead, title='Legacy task',
            owner_user_id=MANAGER, due_date=dj_timezone.now() + timedelta(days=1),
        )
        self._auth(MANAGER)

    def test_lead_filter_still_works(self):
        response = self.client.get(f'/api/tasks/?lead={self.lead.id}')
        self.assertEqual(response.data['count'], 1)

    def test_lead_name_is_still_exposed(self):
        response = self.client.get(f'/api/tasks/{self.task.id}/')
        self.assertEqual(response.data['lead_name'], 'Old Client')

    def test_create_with_only_a_lead_still_works(self):
        response = self.client.post('/api/tasks/', {
            'lead': self.lead.id, 'title': 'Old style',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data['lead'], self.lead.id)
        self.assertEqual(response.data['related_type'], 'LEAD')
        self.assertEqual(response.data['related_id'], self.lead.id)
        self.assertEqual(response.data['related_label'], 'Old Client')

    def test_due_date_range_filters_still_work(self):
        response = self.client.get(
            '/api/tasks/', {'due_date__gte': dj_timezone.now().isoformat()}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['count'], 1)

    def test_search_still_works(self):
        response = self.client.get('/api/tasks/?search=Legacy')
        self.assertEqual(response.data['count'], 1)

    def test_ordering_by_order_index_is_available(self):
        response = self.client.get('/api/tasks/?ordering=order_index')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
