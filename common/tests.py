"""
Regression tests for DIGICRM tenant-isolation fixes.

Covers:
- common.mixins.TenantViewSetMixin fail-closed behaviour
- common.middleware.JWTAuthenticationMiddleware rejects header-based tenant override
- common.permissions scope enforcement (own/team) and admin bypass
"""
import uuid
import jwt as pyjwt
from types import SimpleNamespace
from unittest.mock import MagicMock

from django.test import TestCase, override_settings, RequestFactory
from django.db.models import Q
from rest_framework.test import APIClient

from common.mixins import TenantViewSetMixin
from common.middleware import JWTAuthenticationMiddleware
from common.permissions import (
    is_admin_request,
    check_permission,
    check_object_permission,
    get_queryset_for_permission,
    get_object_owner_id,
    OWNERSHIP_FIELDS,
)


TEST_JWT_SECRET = 'test-jwt-secret-digicrm-unit-tests'
TEST_JWT_ALGO = 'HS256'

TENANT_A = uuid.UUID('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
TENANT_B = uuid.UUID('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb')
USER_A = uuid.UUID('cccccccc-cccc-cccc-cccc-cccccccccccc')
USER_B = uuid.UUID('dddddddd-dddd-dddd-dddd-dddddddddddd')


def _make_jwt(tenant_id, user_id, permissions=None, is_super_admin=False, roles=None):
    """Return a Bearer token string for the given tenant/user."""
    payload = {
        'user_id': str(user_id),
        'email': 'test@example.com',
        'tenant_id': str(tenant_id),
        'tenant_slug': 'test-tenant',
        'is_super_admin': is_super_admin,
        'permissions': permissions or {'crm': {'leads': {'view': 'all', 'create': True}}},
        'enabled_modules': ['crm'],
        'roles': roles or [],
    }
    token = pyjwt.encode(payload, TEST_JWT_SECRET, algorithm=TEST_JWT_ALGO)
    return f'Bearer {token}'


class FakeQuerySet:
    """Minimal queryset stand-in for TenantViewSetMixin tests."""

    def __init__(self, data):
        self._data = data

    def filter(self, **kwargs):
        return FakeQuerySet([d for d in self._data if all(d.get(k) == v for k, v in kwargs.items())])

    def none(self):
        return FakeQuerySet([])

    def __len__(self):
        return len(self._data)

    def __iter__(self):
        return iter(self._data)


class FakeParentViewSet:
    def get_queryset(self):
        return FakeQuerySet([
            {'id': 1, 'tenant_id': str(TENANT_A)},
            {'id': 2, 'tenant_id': str(TENANT_B)},
        ])


class TenantViewSetMixinTest(TestCase):
    """TenantViewSetMixin must return an empty queryset when tenant_id is missing."""

    def _make_viewset(self, request):
        class TestViewSet(TenantViewSetMixin, FakeParentViewSet):
            pass

        viewset = TestViewSet()
        viewset.request = request
        return viewset

    def test_returns_filtered_queryset_when_tenant_id_present(self):
        request = MagicMock()
        request.tenant_id = str(TENANT_A)
        request.method = 'GET'
        request.path = '/api/test/'
        request.META = {}
        request.user = None

        qs = self._make_viewset(request).get_queryset()
        self.assertEqual(len(qs), 1)
        self.assertEqual(list(qs)[0]['id'], 1)

    def test_returns_empty_queryset_when_tenant_id_missing(self):
        request = MagicMock()
        request.tenant_id = None
        request.method = 'GET'
        request.path = '/api/test/'
        request.META = {}
        request.user = None

        qs = self._make_viewset(request).get_queryset()
        self.assertEqual(len(qs), 0)

    def test_returns_empty_queryset_when_tenant_id_is_empty_string(self):
        request = MagicMock()
        request.tenant_id = '   '
        request.method = 'GET'
        request.path = '/api/test/'
        request.META = {}
        request.user = None

        qs = self._make_viewset(request).get_queryset()
        self.assertEqual(len(qs), 0)


@override_settings(JWT_SECRET_KEY=TEST_JWT_SECRET, JWT_ALGORITHM=TEST_JWT_ALGO)
class JWTAuthenticationMiddlewareTenantBindingTest(TestCase):
    """Tenant context must come from the JWT, not from client headers."""

    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = JWTAuthenticationMiddleware(lambda req: None)

    def _build_request(self, auth_token, headers=None):
        headers = headers or {}
        request = self.factory.get('/api/test/', **headers)
        if auth_token:
            request.META['HTTP_AUTHORIZATION'] = auth_token
        return request

    def test_tenant_id_comes_from_jwt_despite_override_header(self):
        """X-Tenant-Id header must not override the JWT-bound tenant."""
        request = self._build_request(
            _make_jwt(TENANT_A, USER_A),
            {'HTTP_X_TENANT_ID': str(TENANT_B)},
        )
        response = self.middleware.process_request(request)
        self.assertIsNone(response)
        self.assertEqual(request.tenant_id, str(TENANT_A))
        self.assertEqual(request.tenant_slug, 'test-tenant')

    def test_tenanttoken_header_does_not_override_jwt_tenant(self):
        request = self._build_request(
            _make_jwt(TENANT_A, USER_A),
            {'HTTP_TENANTTOKEN': str(TENANT_B)},
        )
        response = self.middleware.process_request(request)
        self.assertIsNone(response)
        self.assertEqual(request.tenant_id, str(TENANT_A))

    def test_x_tenant_slug_header_does_not_override_jwt_slug(self):
        request = self._build_request(
            _make_jwt(TENANT_A, USER_A),
            {'HTTP_X_TENANT_SLUG': 'other-tenant'},
        )
        response = self.middleware.process_request(request)
        self.assertIsNone(response)
        self.assertEqual(request.tenant_slug, 'test-tenant')


class AdminBypassTest(TestCase):
    """is_admin_request must trust only explicit admin grants, not role names."""

    def _request(self, **kwargs):
        return SimpleNamespace(**kwargs)

    def test_super_admin_is_admin(self):
        request = self._request(is_super_admin=True, permissions={}, roles=[])
        self.assertTrue(is_admin_request(request))

    def test_full_access_grant_is_admin(self):
        request = self._request(
            is_super_admin=False,
            permissions={'admin': {'full_access': True}},
            roles=[]
        )
        self.assertTrue(is_admin_request(request))

    def test_role_name_admin_is_not_admin(self):
        """P2: role names like 'admin' must not bypass permission checks."""
        request = self._request(
            is_super_admin=False,
            permissions={'crm': {'leads': {'view': 'own'}}},
            roles=[{'name': 'admin'}]
        )
        self.assertFalse(is_admin_request(request))

    def test_role_name_superadmin_is_not_admin(self):
        request = self._request(
            is_super_admin=False,
            permissions={'crm': {'leads': {'view': 'own'}}},
            roles=['superadmin']
        )
        self.assertFalse(is_admin_request(request))


class ScopedPermissionTest(TestCase):
    """Own/team scope must be enforced fail-closed at object and queryset level."""

    def _request(self, user_id, permissions):
        return SimpleNamespace(user_id=user_id, permissions=permissions, is_super_admin=False)

    def test_own_scope_allows_owned_object(self):
        lead = SimpleNamespace(_meta=SimpleNamespace(label='crm.Lead'), owner_user_id=USER_A, assigned_to=None)
        request = self._request(USER_A, {'crm': {'leads': {'view': 'own'}}})
        self.assertTrue(check_object_permission(request, lead, 'crm.leads.view'))

    def test_own_scope_denies_unowned_object(self):
        lead = SimpleNamespace(_meta=SimpleNamespace(label='crm.Lead'), owner_user_id=USER_B, assigned_to=None)
        request = self._request(USER_A, {'crm': {'leads': {'view': 'own'}}})
        self.assertFalse(check_object_permission(request, lead, 'crm.leads.view'))

    def test_own_scope_denies_when_owner_unresolvable(self):
        """Fail-closed: unregistered model with 'own' scope is denied."""
        obj = SimpleNamespace(_meta=SimpleNamespace(label='crm.Unknown'))
        request = self._request(USER_A, {'crm': {'leads': {'view': 'own'}}})
        self.assertFalse(check_object_permission(request, obj, 'crm.leads.view'))

    def test_team_scope_allows_owned_lead(self):
        lead = SimpleNamespace(_meta=SimpleNamespace(label='crm.Lead'), owner_user_id=USER_A, assigned_to=USER_B)
        request = self._request(USER_A, {'crm': {'leads': {'view': 'team'}}})
        self.assertTrue(check_object_permission(request, lead, 'crm.leads.view'))

    def test_team_scope_allows_assigned_lead(self):
        """P2: 'team' on leads includes assigned_to matches."""
        lead = SimpleNamespace(_meta=SimpleNamespace(label='crm.Lead'), owner_user_id=USER_B, assigned_to=USER_A)
        request = self._request(USER_A, {'crm': {'leads': {'view': 'team'}}})
        self.assertTrue(check_object_permission(request, lead, 'crm.leads.view'))

    def test_team_scope_denies_non_team_lead(self):
        lead = SimpleNamespace(_meta=SimpleNamespace(label='crm.Lead'), owner_user_id=USER_B, assigned_to=USER_B)
        request = self._request(USER_A, {'crm': {'leads': {'view': 'team'}}})
        self.assertFalse(check_object_permission(request, lead, 'crm.leads.view'))

    def test_action_level_check_accepts_scoped_grant_without_object(self):
        """A user with 'own' scope has the action permission; filtering happens elsewhere."""
        request = self._request(USER_A, {'crm': {'leads': {'view': 'own'}}})
        self.assertTrue(check_permission(request, 'crm.leads.view'))

    def test_get_queryset_for_permission_own_filters_by_owner(self):
        qs = FakeQuerySet([
            {'id': 1, 'tenant_id': str(TENANT_A), 'owner_user_id': str(USER_A)},
            {'id': 2, 'tenant_id': str(TENANT_A), 'owner_user_id': str(USER_B)},
            {'id': 3, 'tenant_id': str(TENANT_B), 'owner_user_id': str(USER_A)},
        ])
        # FakeQuerySet needs a model attribute for the registry lookup.
        qs.model = SimpleNamespace(_meta=SimpleNamespace(label='crm.Lead'))
        request = self._request(USER_A, {'crm.leads.view': 'own'})
        request.tenant_id = str(TENANT_A)

        result = get_queryset_for_permission(qs, request, 'crm.leads.view')
        self.assertEqual(len(result), 1)
        self.assertEqual(list(result)[0]['id'], 1)

    def test_get_queryset_for_permission_team_filters_by_owner_or_assigned(self):
        qs = FakeQuerySet([
            {'id': 1, 'tenant_id': str(TENANT_A), 'owner_user_id': str(USER_A), 'assigned_to': str(USER_B)},
            {'id': 2, 'tenant_id': str(TENANT_A), 'owner_user_id': str(USER_B), 'assigned_to': str(USER_A)},
            {'id': 3, 'tenant_id': str(TENANT_A), 'owner_user_id': str(USER_B), 'assigned_to': str(USER_B)},
        ])
        qs.model = SimpleNamespace(_meta=SimpleNamespace(label='crm.Lead'))
        request = self._request(USER_A, {'crm.leads.view': 'team'})
        request.tenant_id = str(TENANT_A)

        result = get_queryset_for_permission(qs, request, 'crm.leads.view')
        ids = {r['id'] for r in result}
        self.assertEqual(ids, {1, 2})

    def test_get_queryset_for_permission_missing_tenant_returns_empty(self):
        qs = FakeQuerySet([{'id': 1, 'tenant_id': str(TENANT_A), 'owner_user_id': str(USER_A)}])
        qs.model = SimpleNamespace(_meta=SimpleNamespace(label='crm.Lead'))
        request = self._request(USER_A, {'crm.leads.view': 'own'})
        request.tenant_id = None

        result = get_queryset_for_permission(qs, request, 'crm.leads.view')
        self.assertEqual(len(result), 0)
