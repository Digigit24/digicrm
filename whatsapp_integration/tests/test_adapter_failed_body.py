"""
Laravel answers HTTP 200 when it rejects our credentials.

`ApiVendorAccessCheckpost` rejects bad credentials through
`processExternalApiResponse()`, which calls `response()->json()` with no status
argument — so "Invalid Token", "Invalid Vendor" and "Vendor account is not in
active state" all arrive as **HTTP 200** with `{"result":"failed"}` in the body.

Commit c703342a taught `_make_request` to spot that shape and raise. This file
pins the behaviour for the READ paths — conversations, contacts, templates —
because they reach Laravel through the same `vendor_request` -> `_make_request`
chain and nothing proved they were covered.

It also pins the STATUS CODE, which is the part that actually reached users. A
rejected vendor token used to surface as 502, and every client in this
estate treats 404/501/502/503 as "this backend has not deployed the WhatsApp
endpoints yet" (see `isWhatsappEndpointUnavailable` in the web and mobile
services). So an expired credential rendered as "Chat is not available yet",
which sent people looking for a deployment problem that did not exist. 424 is
this codebase's established "a dependency is not usable" signal — see
`integrations/views_composio.py` — and no client swallows it.
"""
import uuid
from unittest.mock import patch

import jwt as pyjwt
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from whatsapp_integration.services.laravel_adapter import (
    LaravelAdapterError,
    LaravelWhatsAppAdapter,
)

TEST_JWT_SECRET = 'test-jwt-secret-digicrm-adapter-failed-body'
TENANT = uuid.UUID('aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa')
USER = uuid.UUID('cccccccc-3333-4333-8333-cccccccccccc')

# Verbatim from production: GET https://whatsappapi.celiyo.com/api/{uid}/chat/contacts
# with the deployed WA_API_TOKEN returns exactly this, with HTTP 200.
INVALID_TOKEN_BODY = {'result': 'failed', 'message': 'Invalid Token', 'data': []}

SETTINGS = dict(JWT_SECRET_KEY=TEST_JWT_SECRET, JWT_ALGORITHM='HS256')


class _Resp:
    """Minimal stand-in for requests.Response."""

    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def _adapter():
    return LaravelWhatsAppAdapter(
        tenant_id=str(TENANT), vendor_uid='vendor-uid-1', api_token='a-stale-token',
    )


def _authed_client():
    payload = {
        'user_id': str(USER),
        'email': 'test@example.com',
        'tenant_id': str(TENANT),
        'tenant_slug': 'test-tenant',
        'is_super_admin': False,
        'permissions': {'whatsapp': {'messages': {'view': True}, 'contacts': {'view': True}}},
        'enabled_modules': ['crm', 'whatsapp'],
    }
    client = APIClient()
    client.credentials(
        HTTP_AUTHORIZATION='Bearer ' + pyjwt.encode(payload, TEST_JWT_SECRET, algorithm='HS256')
    )
    return client


class ReadPathsRejectFailedBodyTests(TestCase):
    """Every read that goes through vendor_request must refuse the failure body."""

    def _assert_raises_for(self, call):
        with patch('whatsapp_integration.services.laravel_adapter.requests.request',
                   return_value=_Resp(INVALID_TOKEN_BODY)) as request:
            with self.assertRaises(LaravelAdapterError) as caught:
                call(_adapter())
        self.assertTrue(request.called, 'the adapter never reached Laravel')
        return caught.exception

    def test_get_conversations_raises_instead_of_returning_the_failure_body(self):
        error = self._assert_raises_for(lambda a: a.get_conversations({'page': 1}))
        self.assertIn('Invalid Token', str(error))

    def test_get_contacts_raises(self):
        error = self._assert_raises_for(lambda a: a.get_contacts({'page': 1}))
        self.assertIn('Invalid Token', str(error))

    def test_get_contact_groups_raises(self):
        self._assert_raises_for(lambda a: a.get_contact_groups())

    def test_get_unread_count_raises(self):
        self._assert_raises_for(lambda a: a.get_unread_count())

    def test_send_path_still_raises(self):
        """c703342a's original case must not regress."""
        self._assert_raises_for(
            lambda a: a.send_template_message({'phone_number': '919876543210'})
        )


class CredentialRejectionIsNotDeploymentUnavailabilityTests(TestCase):
    """
    The status code is the whole point of this fix.

    502/503 are indistinguishable from "not deployed" to every client we ship,
    so a bad token has to come back as something else or the error is a lie.
    """

    def _status_for(self, message):
        body = {'result': 'failed', 'message': message, 'data': []}
        with patch('whatsapp_integration.services.laravel_adapter.requests.request',
                   return_value=_Resp(body)):
            with self.assertRaises(LaravelAdapterError) as caught:
                _adapter().get_conversations({})
        return caught.exception.status_code

    def test_auth_failures_are_424_not_the_unavailable_range(self):
        for message in ('Invalid Token', 'Invalid Vendor',
                        'Vendor account is not in active state', 'Unauthorized'):
            status = self._status_for(message)
            self.assertEqual(status, 424, message)
            self.assertNotIn(
                status, (404, 501, 502, 503),
                f'{message!r} came back in the range clients read as "not deployed"',
            )

    def test_a_non_auth_failure_is_still_a_bad_gateway(self):
        """Only credential rejection is reclassified; a real upstream fault is not."""
        self.assertEqual(self._status_for('Something exploded upstream'), 502)


@override_settings(**SETTINGS)
class ConversationsViewReturnsACleanErrorTests(TestCase):
    """
    End to end: the symptom that started this was a 502 reaching the browser.

    Asserting on the VIEW, not just the adapter, because the bug users saw was a
    status code and a message — an exception that never escapes the adapter is
    invisible from here.
    """

    def test_conversations_returns_424_with_a_readable_detail(self):
        with patch('whatsapp_integration.services.laravel_adapter.requests.request',
                   return_value=_Resp(INVALID_TOKEN_BODY)), \
             patch('whatsapp_integration.views._adapter_from_request', side_effect=lambda r: _adapter()):
            response = _authed_client().get('/api/whatsapp/chat/conversations/')

        self.assertEqual(response.status_code, 424)
        self.assertIn('Invalid Token', response.json()['detail'])
        # Not a crash, and not the "not deployed" range.
        self.assertNotIn(response.status_code, (500, 502, 503))

    def test_contacts_returns_424_too(self):
        with patch('whatsapp_integration.services.laravel_adapter.requests.request',
                   return_value=_Resp(INVALID_TOKEN_BODY)), \
             patch('whatsapp_integration.views._adapter_from_request', side_effect=lambda r: _adapter()):
            response = _authed_client().get('/api/whatsapp/contacts/')

        self.assertEqual(response.status_code, 424)
