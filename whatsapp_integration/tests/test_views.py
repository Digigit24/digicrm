"""
Regression tests for DIGICRM WhatsApp tenant isolation.

Covers:
- whatsapp_integration.views.LeadWhatsAppViewSet inherits TenantViewSetMixin
- Cross-tenant lead access via the chat action returns 404
"""
import uuid
import jwt as pyjwt
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from crm.models import Lead
from whatsapp_integration.views import LeadWhatsAppViewSet


TEST_JWT_SECRET = 'test-jwt-secret-digicrm-whatsapp-unit-tests'
TEST_JWT_ALGO = 'HS256'

TENANT_A = uuid.UUID('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
TENANT_B = uuid.UUID('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb')
USER_A = uuid.UUID('cccccccc-cccc-cccc-cccc-cccccccccccc')


def _make_jwt(tenant_id, user_id):
    """Return a Bearer token string for the given tenant/user."""
    payload = {
        'user_id': str(user_id),
        'email': 'test@example.com',
        'tenant_id': str(tenant_id),
        'tenant_slug': 'test-tenant',
        'is_super_admin': False,
        'permissions': {
            'whatsapp': {
                'messages': {'view': 'all'}
            }
        },
        'enabled_modules': ['crm', 'whatsapp'],
    }
    token = pyjwt.encode(payload, TEST_JWT_SECRET, algorithm=TEST_JWT_ALGO)
    return f'Bearer {token}'


def _authed_client(tenant_id, user_id):
    """Return APIClient with a valid JWT Authorization header."""
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=_make_jwt(tenant_id, user_id))
    return client


@override_settings(JWT_SECRET_KEY=TEST_JWT_SECRET, JWT_ALGORITHM=TEST_JWT_ALGO)
class LeadWhatsAppViewSetTenantTest(TestCase):
    """LeadWhatsAppViewSet must be tenant-scoped and fail-closed across tenants."""

    def setUp(self):
        self.lead_a = Lead.objects.create(
            tenant_id=TENANT_A,
            name='Tenant A Lead',
            phone='+919000000001',
            owner_user_id=USER_A,
        )
        self.lead_b = Lead.objects.create(
            tenant_id=TENANT_B,
            name='Tenant B Lead',
            phone='+919000000002',
            owner_user_id=uuid.UUID('dddddddd-dddd-dddd-dddd-dddddddddddd'),
        )

    def test_viewset_includes_tenant_view_set_mixin(self):
        self.assertTrue(
            issubclass(LeadWhatsAppViewSet, type(self).__bases__[0].__mro__[0]) or
            any(base.__name__ == 'TenantViewSetMixin' for base in LeadWhatsAppViewSet.__mro__)
        )
        self.assertIn('TenantViewSetMixin', [base.__name__ for base in LeadWhatsAppViewSet.__mro__])

    @patch('whatsapp_integration.views._adapter_from_request')
    def test_chat_returns_data_for_own_tenant_lead(self, mock_adapter):
        mock_adapter.return_value.get_chat_history.return_value = {'messages': []}
        client = _authed_client(TENANT_A, USER_A)
        response = client.get(f'/api/whatsapp/leads/{self.lead_a.id}/chat/')
        self.assertEqual(response.status_code, 200)
        mock_adapter.return_value.get_chat_history.assert_called_once()

    @patch('whatsapp_integration.views._adapter_from_request')
    def test_chat_returns_404_for_other_tenant_lead(self, mock_adapter):
        mock_adapter.return_value.get_chat_history.return_value = {'messages': []}
        client = _authed_client(TENANT_A, USER_A)
        response = client.get(f'/api/whatsapp/leads/{self.lead_b.id}/chat/')
        self.assertEqual(response.status_code, 404)
        mock_adapter.return_value.get_chat_history.assert_not_called()
