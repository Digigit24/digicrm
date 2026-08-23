"""
DigiCRM publishing the FULL inbound WhatsApp message from its own webhook.

The thing under test is a side channel, so the tests are mostly about what must
NOT happen: the webhook must not break when Pusher does, the envelope must not
drift from the REST one, and a message must never land on another tenant's
channel.

Pusher itself is mocked everywhere in this module. Nothing here may touch the
real account.
"""
import json
import uuid
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from crm.models import Lead, LeadActivity
from whatsapp_integration.models import WhatsAppVendorConfig
from whatsapp_integration.services import publisher
from whatsapp_integration.services.normalizer import normalize_message
from whatsapp_integration.services.realtime import (
    DIGICRM_MESSAGE_EVENT, DIGICRM_STATUS_EVENT,
)

TENANT_A = uuid.UUID('aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa')
TENANT_B = uuid.UUID('bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb')

VENDOR_A = 'vendor-uid-aaaa-0001'
VENDOR_B = 'vendor-uid-bbbb-0002'

SECRET_A = 'webhook-shared-secret-AAAA'
SECRET_B = 'webhook-shared-secret-BBBB'

WEBHOOK_URL = '/api/whatsapp/webhooks/message-replied/'
STATUS_URL = '/api/whatsapp/webhooks/message-status/'

PUSHER_SETTINGS = dict(
    PUSHER_APP_ID='2109286',
    PUSHER_KEY='649db422ae8f2e9c7a9d',
    PUSHER_SECRET='unit-test-pusher-secret-not-the-real-one',
    PUSHER_CLUSTER='ap2',
)


def _make_lead(phone='919876543210', name='Asha'):
    """Lead.owner_user_id is NOT NULL; every test lead needs one."""
    return Lead.objects.create(
        tenant_id=TENANT_A, name=name, phone=phone,
        owner_user_id=uuid.UUID('cccccccc-3333-4333-8333-cccccccccccc'),
    )


def _inbound_payload(tenant_id, *, phone='919876543210', body='hello there', **extra):
    data = {
        'phone': phone,
        'message_body': body,
        'message_wamid': 'wamid.HBgMOTE5ODc2NTQzMjEwFQIAEhgUM0E0',
        'message_uid': 'msg-uid-0001',
        'messaged_at': '2026-08-24T10:30:00+00:00',
    }
    data.update(extra)
    return {'tenant_id': str(tenant_id), 'data': data}


class PublisherTestBase(TestCase):
    def setUp(self):
        WhatsAppVendorConfig.objects.create(
            tenant_id=TENANT_A, vendor_uid=VENDOR_A,
            api_token='token-a', webhook_secret=SECRET_A, is_active=True,
        )
        WhatsAppVendorConfig.objects.create(
            tenant_id=TENANT_B, vendor_uid=VENDOR_B,
            api_token='token-b', webhook_secret=SECRET_B, is_active=True,
        )
        # A fresh mock per test, and the module-level client cache cleared, so
        # one test's client can never leak into the next.
        publisher._client = None
        self.pusher = MagicMock()
        self._patcher = patch.object(publisher, '_get_client', return_value=self.pusher)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)
        self.addCleanup(lambda: setattr(publisher, '_client', None))

    def post_webhook(self, payload, *, secret=SECRET_A, url=WEBHOOK_URL):
        return self.client.post(
            url, data=json.dumps(payload),
            content_type='application/json', HTTP_X_ADAPTER_SECRET=secret,
        )

    def published(self):
        """(channel, event, payload) of the single publish that happened."""
        self.assertEqual(self.pusher.trigger.call_count, 1, self.pusher.trigger.call_args_list)
        return self.pusher.trigger.call_args[0]


class EnvelopeMatchesRestTests(PublisherTestBase):
    """The published body must be the SAME envelope GET /api/whatsapp/chat/ returns."""

    def test_published_message_has_exactly_the_rest_envelope_keys(self):
        response = self.post_webhook(_inbound_payload(TENANT_A))
        self.assertEqual(response.status_code, 200)

        _, event, payload = self.published()
        self.assertEqual(event, DIGICRM_MESSAGE_EVENT)

        # The reference envelope, built by the same normaliser the REST surface
        # calls, from a Laravel-shaped row.
        reference = normalize_message({
            '_uid': 'anything', 'wamid': 'x', 'is_incoming_message': 1,
            'message': 'hi', 'messaged_at': '2026-08-24T10:30:00+00:00',
        }, TENANT_A)

        self.assertEqual(set(payload['message'].keys()), set(reference.keys()))

    def test_envelope_is_byte_identical_to_normalising_the_same_row(self):
        """No second implementation: the publisher must go through normalize_message."""
        payload_in = _inbound_payload(TENANT_A)
        self.post_webhook(payload_in)
        _, _, payload = self.published()

        row = publisher.webhook_data_to_row(payload_in['data'], incoming=True)
        self.assertEqual(payload['message'], normalize_message(row, TENANT_A))

    def test_the_body_and_wamid_that_laravel_omits_are_present(self):
        """The entire point: no refetch needed to render this message."""
        self.post_webhook(_inbound_payload(TENANT_A, body='hello there'))
        _, _, payload = self.published()

        self.assertEqual(payload['message']['text'], 'hello there')
        self.assertEqual(
            payload['message']['wamid'],
            'wamid.HBgMOTE5ODc2NTQzMjEwFQIAEhgUM0E0',
        )
        self.assertEqual(payload['message']['direction'], 'in')
        self.assertEqual(payload['message']['type'], 'text')
        self.assertEqual(payload['contact'], '919876543210')

    def test_a_media_reply_publishes_a_media_envelope_not_a_blank_text_bubble(self):
        self.post_webhook(_inbound_payload(
            TENANT_A, body=None,
            media={'type': 'image', 'mime_type': 'image/jpeg', 'caption': 'the roof'},
        ))
        _, _, payload = self.published()

        self.assertEqual(payload['message']['type'], 'image')
        self.assertIsNotNone(payload['message']['media'])
        self.assertEqual(payload['message']['media']['mime'], 'image/jpeg')


class TenantScopingTests(PublisherTestBase):
    def test_publishes_to_the_channel_derived_from_the_webhook_tenants_config(self):
        self.post_webhook(_inbound_payload(TENANT_A))
        channel, _, _ = self.published()
        self.assertEqual(channel, f'private-vendor-channel.{VENDOR_A}')

    def test_never_publishes_to_another_tenants_channel(self):
        self.post_webhook(_inbound_payload(TENANT_B), secret=SECRET_B)
        channel, _, _ = self.published()
        self.assertEqual(channel, f'private-vendor-channel.{VENDOR_B}')
        self.assertNotIn(VENDOR_A, channel)

    def test_a_body_supplied_channel_is_ignored_entirely(self):
        """The caller does not get to name the channel it publishes to."""
        payload = _inbound_payload(TENANT_A)
        payload['data']['channel'] = f'private-vendor-channel.{VENDOR_B}'
        payload['channel'] = f'private-vendor-channel.{VENDOR_B}'
        payload['data']['vendor_uid'] = VENDOR_B

        self.post_webhook(payload)
        channel, _, _ = self.published()
        self.assertEqual(channel, f'private-vendor-channel.{VENDOR_A}')

    def test_an_unauthorised_webhook_publishes_nothing(self):
        response = self.post_webhook(_inbound_payload(TENANT_A), secret='wrong')
        self.assertEqual(response.status_code, 401)
        self.pusher.trigger.assert_not_called()

    def test_no_vendor_config_means_no_publish_and_no_crash(self):
        WhatsAppVendorConfig.objects.filter(tenant_id=TENANT_A).update(is_active=False)
        # Secret verification also reads the config, so this webhook is refused
        # before publishing; assert directly that the publisher stays quiet.
        self.assertFalse(publisher.publish_inbound_message(
            TENANT_A, _inbound_payload(TENANT_A)['data'],
        ))
        self.pusher.trigger.assert_not_called()


class FailureIsContainedTests(PublisherTestBase):
    def test_a_pusher_error_does_not_break_webhook_processing(self):
        self.pusher.trigger.side_effect = RuntimeError('pusher is down')
        _make_lead()

        response = self.post_webhook(_inbound_payload(TENANT_A))

        self.assertEqual(response.status_code, 200)
        # The durable record is still written.
        self.assertEqual(
            LeadActivity.objects.filter(tenant_id=TENANT_A).count(), 1,
        )

    def test_pusher_unconfigured_is_a_silent_no_op(self):
        self._patcher.stop()
        with override_settings(PUSHER_APP_ID='', PUSHER_SECRET=''):
            publisher._client = None
            publisher._unconfigured_logged = False
            self.assertIsNone(publisher._get_client())
            self.assertFalse(publisher.publish_inbound_message(
                TENANT_A, _inbound_payload(TENANT_A)['data'],
            ))
        self._patcher.start()

    def test_a_garbage_payload_still_returns_200(self):
        response = self.post_webhook({
            'tenant_id': str(TENANT_A),
            'data': {'phone': '919876543210', 'message_body': {'not': 'a string'}},
        })
        self.assertEqual(response.status_code, 200)

    def test_publish_functions_never_raise(self):
        self.pusher.trigger.side_effect = RuntimeError('boom')
        for payload_data in ({}, None, {'phone': None}, {'message_body': object()}):
            self.assertFalse(publisher.publish_inbound_message(TENANT_A, payload_data))
            self.assertFalse(publisher.publish_message_status(TENANT_A, payload_data))

    def test_an_oversized_payload_is_shrunk_rather_than_rejected(self):
        """Pusher hard-rejects >10KB; a huge message must still broadcast."""
        self.post_webhook(_inbound_payload(TENANT_A, body='x' * 12000))
        _, _, payload = self.published()

        self.assertLess(
            len(json.dumps(payload, default=str).encode('utf-8')), 10_000,
        )
        self.assertTrue(payload['message'].get('truncated'))


class LeadMatchingIsNotAPrerequisiteTests(PublisherTestBase):
    def test_publishes_even_when_no_lead_matches_the_phone(self):
        """
        The chat surface is keyed on the WhatsApp contact, not a CRM lead. The
        `phone__contains` lookup in the view mis-matches 10-digit-stored numbers
        (audit P0-6); realtime must not inherit that bug.
        """
        self.assertFalse(Lead.objects.filter(tenant_id=TENANT_A).exists())

        response = self.post_webhook(_inbound_payload(TENANT_A))

        self.assertEqual(response.status_code, 200)
        channel, event, _ = self.published()
        self.assertEqual(event, DIGICRM_MESSAGE_EVENT)
        self.assertEqual(channel, f'private-vendor-channel.{VENDOR_A}')


class StatusEventTests(PublisherTestBase):
    def test_a_status_update_publishes_the_status_event(self):
        response = self.post_webhook({
            'tenant_id': str(TENANT_A),
            'data': {
                'phone': '919876543210',
                'message_wamid': 'wamid.STATUS001',
                'message_uid': 'msg-uid-0009',
                'message_status': 'read',
            },
        }, url=STATUS_URL)

        self.assertEqual(response.status_code, 200)
        channel, event, payload = self.published()
        self.assertEqual(channel, f'private-vendor-channel.{VENDOR_A}')
        self.assertEqual(event, DIGICRM_STATUS_EVENT)
        self.assertEqual(payload['status'], 'read')
        self.assertEqual(payload['wamid'], 'wamid.STATUS001')
        self.assertEqual(payload['id'], 'msg-uid-0009')

    def test_a_failed_status_carries_the_error(self):
        self.post_webhook({
            'tenant_id': str(TENANT_A),
            'data': {
                'phone': '919876543210',
                'message_wamid': 'wamid.STATUS002',
                'message_status': 'failed',
                'whatsapp_message_error': 'Recipient has not opted in',
            },
        }, url=STATUS_URL)

        _, _, payload = self.published()
        self.assertEqual(payload['status'], 'failed')
        self.assertEqual(payload['error'], 'Recipient has not opted in')


class BackwardCompatibilityTests(PublisherTestBase):
    def test_digicrm_never_publishes_under_laravels_event_name(self):
        self.post_webhook(_inbound_payload(TENANT_A))
        _, event, _ = self.published()
        self.assertNotEqual(event, 'VendorChannelBroadcast')

    def test_the_existing_webhook_side_effects_are_unchanged(self):
        lead = _make_lead()
        self.assertIsNone(lead.last_contacted_at)

        self.post_webhook(_inbound_payload(TENANT_A, body='hello there'))

        lead.refresh_from_db()
        self.assertIsNotNone(lead.last_contacted_at)
        activity = LeadActivity.objects.get(tenant_id=TENANT_A, lead=lead)
        self.assertIn('hello there', activity.content)
        self.assertEqual(
            activity.meta['wamid'], 'wamid.HBgMOTE5ODc2NTQzMjEwFQIAEhgUM0E0',
        )

    def test_an_unknown_event_type_is_still_a_400(self):
        response = self.post_webhook(
            _inbound_payload(TENANT_A), url='/api/whatsapp/webhooks/who-knows/',
        )
        self.assertEqual(response.status_code, 400)
        self.pusher.trigger.assert_not_called()


@override_settings(**PUSHER_SETTINGS)
class GrantAdvertisesTheNewEventsTests(TestCase):
    """Clients discover the new names by value, exactly as they do `event`."""

    def test_build_grant_returns_the_digicrm_event_names(self):
        WhatsAppVendorConfig.objects.create(
            tenant_id=TENANT_A, vendor_uid=VENDOR_A,
            api_token='token-a', webhook_secret=SECRET_A, is_active=True,
        )
        from whatsapp_integration.services.realtime import build_grant

        grant = build_grant(tenant_id=TENANT_A)

        self.assertEqual(grant['digicrm_event'], 'DigicrmMessage')
        self.assertEqual(grant['digicrm_echo_event'], '.DigicrmMessage')
        self.assertEqual(grant['digicrm_status_event'], 'DigicrmMessageStatus')
        self.assertEqual(grant['digicrm_status_echo_event'], '.DigicrmMessageStatus')
        # The Laravel contract is untouched.
        self.assertEqual(grant['event'], 'VendorChannelBroadcast')
        self.assertEqual(grant['echo_event'], '.VendorChannelBroadcast')
        # And the secret still never leaves the server.
        self.assertNotIn('secret', grant)
        self.assertNotIn('api_token', grant)
