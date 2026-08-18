# apps/crm/tests.py

import uuid
from datetime import datetime, timezone

import jwt as pyjwt
from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from crm.models import (
    Lead, LeadStatus, LeadActivity, LeadFieldConfiguration,
    LeadGroup, LeadGroupMembership,
)
from common.generated_permissions import CRMPermissions


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
        'permissions': permissions or {'crm': {'leads': {'view': 'own', 'create': True, 'edit': True, 'delete': True}}},
        'enabled_modules': ['crm'],
        'roles': [],
        'exp': datetime.now(timezone.utc).replace(hour=23, minute=59),
    }
    return pyjwt.encode(payload, TEST_JWT_SECRET, algorithm=TEST_JWT_ALGO)


class LeadFieldLayoutTest(APITestCase):
    def setUp(self):
        self.first = LeadFieldConfiguration.objects.create(
            tenant_id=TENANT_A,
            field_name='name',
            field_label='Name',
            is_standard=True,
            is_visible=True,
            display_order=10,
        )
        self.second = LeadFieldConfiguration.objects.create(
            tenant_id=TENANT_A,
            field_name='phone',
            field_label='Phone',
            is_standard=True,
            is_visible=True,
            display_order=20,
        )
        self.third = LeadFieldConfiguration.objects.create(
            tenant_id=TENANT_A,
            field_name='industry',
            field_label='Industry',
            field_type='TEXT',
            is_visible=True,
            display_order=30,
        )
        token = _make_token(
            USER_A,
            permissions={'crm': {'settings': {'view': True, 'edit': True}}},
        )
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_layout_atomically_updates_order_and_visibility(self):
        response = self.client.patch(
            reverse('leadfieldconfiguration-layout'),
            {
                'fields': [
                    {'id': self.third.id, 'is_visible': False},
                    {'id': self.first.id, 'is_visible': True},
                    {'id': self.second.id, 'is_visible': False},
                ]
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([item['id'] for item in response.data], [self.third.id, self.first.id, self.second.id])
        self.third.refresh_from_db()
        self.second.refresh_from_db()
        self.assertEqual(self.third.display_order, 1)
        self.assertFalse(self.third.is_visible)
        self.assertEqual(self.second.display_order, 3)
        self.assertFalse(self.second.is_visible)

    def test_layout_rejects_incomplete_payload_without_changes(self):
        response = self.client.patch(
            reverse('leadfieldconfiguration-layout'),
            {'fields': [{'id': self.second.id, 'is_visible': False}]},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.second.refresh_from_db()
        self.assertEqual(self.second.display_order, 20)
        self.assertTrue(self.second.is_visible)


class LeadActivityObjectPermissionTest(APITestCase):
    """P2: LeadActivity retrieve/update/destroy must respect 'own' scope."""

    def setUp(self):
        self.status = LeadStatus.objects.create(
            tenant_id=TENANT_A,
            name='New',
            order_index=1,
        )
        self.lead_a = Lead.objects.create(
            tenant_id=TENANT_A,
            name='Lead A',
            phone='1111111111',
            status=self.status,
            owner_user_id=USER_A,
        )
        self.lead_b = Lead.objects.create(
            tenant_id=TENANT_A,
            name='Lead B',
            phone='2222222222',
            status=self.status,
            owner_user_id=USER_B,
        )
        self.activity_on_a = LeadActivity.objects.create(
            tenant_id=TENANT_A,
            lead=self.lead_a,
            type='NOTE',
            content='Activity on A',
            happened_at=datetime.now(timezone.utc),
            by_user_id=USER_A,
        )
        self.activity_on_b = LeadActivity.objects.create(
            tenant_id=TENANT_A,
            lead=self.lead_b,
            type='NOTE',
            content='Activity on B',
            happened_at=datetime.now(timezone.utc),
            by_user_id=USER_B,
        )

    # LeadActivityViewSet is gated on ``crm.activities.*`` (its
    # ``permission_resource``), not on ``crm.leads.*``. The module-level
    # default token only grants ``crm.leads``, so these tests must ask for the
    # activities scope explicitly or every request 403s before scope is
    # even considered.
    ACTIVITY_PERMS = {
        'crm': {
            'leads': {'view': 'own'},
            'activities': {'view': 'own', 'create': True, 'edit': True, 'delete': True},
        }
    }

    def _auth_client(self, user_id, permissions=None):
        token = _make_token(user_id, permissions=permissions or self.ACTIVITY_PERMS)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_own_scope_can_retrieve_activity_on_owned_lead(self):
        self._auth_client(USER_A)
        url = reverse('lead-activity-detail', kwargs={'pk': self.activity_on_a.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    # KNOWN FAILING -- this asserts a product decision that has not been made.
    #
    # With the activities scope granted, ``get_queryset`` filters the unowned
    # activity out, so DRF returns 404, not 403. Returning 404 is the
    # information-disclosure-safe answer: it does not confirm the row exists.
    # Getting 403 instead requires dropping the queryset scope filter on
    # ``retrieve`` and relying on ``check_object_permission``.
    #
    # Do NOT make that change until ``common.permissions.get_object_owner_id``
    # is de-duplicated: it is defined twice (registry-backed, then a loose
    # duck-typed version that silently shadows it), so the object-level check
    # currently resolves ownership through the weaker resolver. Removing the
    # queryset guard while that shadowing stands would widen access, not
    # narrow it.
    def test_own_scope_cannot_retrieve_activity_on_unowned_lead(self):
        self._auth_client(USER_A)
        url = reverse('lead-activity-detail', kwargs={'pk': self.activity_on_b.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class LeadGroupMembershipScopeTest(APITestCase):
    """P2: add/remove leads from a group must respect crm.leads.view scope."""

    def setUp(self):
        self.status = LeadStatus.objects.create(
            tenant_id=TENANT_A,
            name='New',
            order_index=1,
        )
        self.lead_a = Lead.objects.create(
            tenant_id=TENANT_A,
            name='Lead A',
            phone='1111111111',
            status=self.status,
            owner_user_id=USER_A,
        )
        self.lead_b = Lead.objects.create(
            tenant_id=TENANT_A,
            name='Lead B',
            phone='2222222222',
            status=self.status,
            owner_user_id=USER_B,
        )
        self.group = LeadGroup.objects.create(
            tenant_id=TENANT_A,
            name='Test Group',
            created_by=USER_A,
        )

    def _auth_client(self, user_id, permissions=None):
        token = _make_token(user_id, permissions=permissions)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_add_leads_only_adds_accessible_leads(self):
        """User with 'own' scope can only add their own leads to a group."""
        self._auth_client(USER_A)
        url = reverse('lead-group-add-leads', kwargs={'pk': self.group.id})
        response = self.client.post(url, {'lead_ids': [self.lead_a.id, self.lead_b.id]}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['added'], 1)
        self.assertEqual(response.data['not_found'], 1)
        self.assertTrue(LeadGroupMembership.objects.filter(group=self.group, lead=self.lead_a).exists())
        self.assertFalse(LeadGroupMembership.objects.filter(group=self.group, lead=self.lead_b).exists())

    def test_remove_leads_only_removes_accessible_leads(self):
        LeadGroupMembership.objects.create(group=self.group, lead=self.lead_a)
        LeadGroupMembership.objects.create(group=self.group, lead=self.lead_b)

        self._auth_client(USER_A)
        url = reverse('lead-group-remove-leads', kwargs={'pk': self.group.id})
        response = self.client.post(url, {'lead_ids': [self.lead_a.id, self.lead_b.id]}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['removed'], 1)
        self.assertFalse(LeadGroupMembership.objects.filter(group=self.group, lead=self.lead_a).exists())
        self.assertTrue(LeadGroupMembership.objects.filter(group=self.group, lead=self.lead_b).exists())
