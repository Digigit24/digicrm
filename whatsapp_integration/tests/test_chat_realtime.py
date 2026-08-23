"""
Tests for the DigiCRM-owned WhatsApp surface:

* the Pusher realtime grant (tenant scoping, no vendor-token leakage),
* the pinned normalised message envelope for all 11 WhatsApp types plus the
  unsupported fallback,
* the authenticated media proxy (traversal, forgery, cross-tenant, headers),
* Laravel's auth-failure-as-HTTP-200 detection,
* `template_data` extraction for template bodies.
"""
import hashlib
import hmac
import uuid
from unittest.mock import patch

import jwt as pyjwt
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from whatsapp_integration.models import WhatsAppVendorConfig
from whatsapp_integration.services import realtime
from whatsapp_integration.services.laravel_adapter import (
    LaravelAdapterError, _raise_on_failed_body,
)
from whatsapp_integration.services.media import (
    MediaReferenceError, is_safe_media_path, make_media_id, resolve_media_id,
    safe_content_type,
)
from whatsapp_integration.services.normalizer import (
    normalize_message, normalize_messages, normalize_reply_window,
)
from whatsapp_integration.views import _template_body_text

TEST_JWT_SECRET = 'test-jwt-secret-digicrm-whatsapp-chat'
TEST_JWT_ALGO = 'HS256'

TENANT_A = uuid.UUID('aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa')
TENANT_B = uuid.UUID('bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb')
USER_A = uuid.UUID('cccccccc-3333-4333-8333-cccccccccccc')
USER_B = uuid.UUID('dddddddd-4444-4444-8444-dddddddddddd')

VENDOR_A = 'vendor-uid-aaaa-0001'
VENDOR_B = 'vendor-uid-bbbb-0002'

# The tenant-wide credential that must never reach a client.
VENDOR_TOKEN_A = 'super-secret-vendor-api-access-token-AAAA'
VENDOR_TOKEN_B = 'super-secret-vendor-api-access-token-BBBB'

PUSHER_KEY = '649db422ae8f2e9c7a9d'
PUSHER_SECRET = 'unit-test-pusher-secret-not-the-real-one'
PUSHER_APP_ID = '2109286'
PUSHER_CLUSTER = 'ap2'


def _make_jwt(tenant_id, user_id, permissions=None, modules=('crm', 'whatsapp')):
    payload = {
        'user_id': str(user_id),
        'email': 'test@example.com',
        'tenant_id': str(tenant_id),
        'tenant_slug': 'test-tenant',
        'is_super_admin': False,
        'permissions': permissions if permissions is not None else {
            'whatsapp': {'messages': {'view': 'all', 'send': True}},
        },
        'enabled_modules': list(modules),
    }
    return 'Bearer ' + pyjwt.encode(payload, TEST_JWT_SECRET, algorithm=TEST_JWT_ALGO)


def _authed_client(tenant_id, user_id, **kwargs):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=_make_jwt(tenant_id, user_id, **kwargs))
    return client


PUSHER_SETTINGS = dict(
    JWT_SECRET_KEY=TEST_JWT_SECRET,
    JWT_ALGORITHM=TEST_JWT_ALGO,
    PUSHER_APP_ID=PUSHER_APP_ID,
    PUSHER_KEY=PUSHER_KEY,
    PUSHER_SECRET=PUSHER_SECRET,
    PUSHER_CLUSTER=PUSHER_CLUSTER,
)


# ---------------------------------------------------------------------------
# A. Realtime grant
# ---------------------------------------------------------------------------

@override_settings(**PUSHER_SETTINGS)
class RealtimeGrantTest(TestCase):
    """The grant must be tenant-derived, single-channel, and token-free."""

    GRANT_URL = '/api/whatsapp/realtime/grant/'

    def setUp(self):
        WhatsAppVendorConfig.objects.create(
            tenant_id=TENANT_A, vendor_uid=VENDOR_A, api_token=VENDOR_TOKEN_A,
        )
        WhatsAppVendorConfig.objects.create(
            tenant_id=TENANT_B, vendor_uid=VENDOR_B, api_token=VENDOR_TOKEN_B,
        )

    # -- auth -------------------------------------------------------------

    def test_unauthenticated_caller_is_rejected(self):
        response = APIClient().post(self.GRANT_URL, {}, format='json')
        self.assertIn(response.status_code, (401, 403))

    def test_caller_without_whatsapp_module_is_rejected(self):
        client = _authed_client(TENANT_A, USER_A, modules=('crm',))
        response = client.post(self.GRANT_URL, {}, format='json')
        self.assertEqual(response.status_code, 403)

    def test_caller_without_message_view_permission_is_rejected(self):
        client = _authed_client(TENANT_A, USER_A, permissions={'whatsapp': {'messages': {}}})
        response = client.post(self.GRANT_URL, {}, format='json')
        self.assertEqual(response.status_code, 403)

    # -- tenant scoping ---------------------------------------------------

    def test_channel_is_derived_from_the_jwt_tenant(self):
        client = _authed_client(TENANT_A, USER_A)
        response = client.post(self.GRANT_URL, {}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['channel'], f'private-vendor-channel.{VENDOR_A}')

    def test_two_tenants_get_two_different_channels(self):
        a = _authed_client(TENANT_A, USER_A).post(self.GRANT_URL, {}, format='json').json()
        b = _authed_client(TENANT_B, USER_B).post(self.GRANT_URL, {}, format='json').json()
        self.assertNotEqual(a['channel'], b['channel'])
        self.assertEqual(b['channel'], f'private-vendor-channel.{VENDOR_B}')

    def test_client_supplied_vendor_uid_is_ignored(self):
        """A body claiming another tenant's vendor must not change the channel."""
        client = _authed_client(TENANT_A, USER_A)
        response = client.post(
            self.GRANT_URL,
            {'vendor_uid': VENDOR_B, 'tenant_id': str(TENANT_B)},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['channel'], f'private-vendor-channel.{VENDOR_A}')

    def test_requesting_another_tenants_channel_is_403(self):
        client = _authed_client(TENANT_A, USER_A)
        response = client.post(
            self.GRANT_URL,
            {'socket_id': '12345.67890', 'channel_name': f'private-vendor-channel.{VENDOR_B}'},
            format='json',
        )
        self.assertEqual(response.status_code, 403)
        self.assertNotIn('auth', response.json())

    def test_tenant_without_vendor_config_gets_503_not_a_guess(self):
        tenant_c = uuid.uuid4()
        client = _authed_client(tenant_c, USER_A)
        response = client.post(self.GRANT_URL, {}, format='json')
        self.assertEqual(response.status_code, 503)

    def test_inactive_vendor_config_is_not_used(self):
        WhatsAppVendorConfig.objects.filter(tenant_id=TENANT_A).update(is_active=False)
        client = _authed_client(TENANT_A, USER_A)
        self.assertEqual(client.post(self.GRANT_URL, {}, format='json').status_code, 503)

    # -- the credential itself --------------------------------------------

    def test_grant_never_leaks_the_vendor_token_or_pusher_secret(self):
        client = _authed_client(TENANT_A, USER_A)
        body = client.post(
            self.GRANT_URL, {'socket_id': '12345.67890'}, format='json',
        ).content.decode()
        for secret in (VENDOR_TOKEN_A, VENDOR_TOKEN_B, PUSHER_SECRET, PUSHER_APP_ID):
            self.assertNotIn(secret, body)

    def test_signature_matches_the_pusher_private_channel_protocol(self):
        client = _authed_client(TENANT_A, USER_A)
        socket_id = '98765.43210'
        data = client.post(self.GRANT_URL, {'socket_id': socket_id}, format='json').json()

        channel = f'private-vendor-channel.{VENDOR_A}'
        expected = hmac.new(
            PUSHER_SECRET.encode(), f'{socket_id}:{channel}'.encode(), hashlib.sha256,
        ).hexdigest()
        self.assertEqual(data['auth'], f'{PUSHER_KEY}:{expected}')

    def test_signature_is_bound_to_one_socket(self):
        client = _authed_client(TENANT_A, USER_A)
        one = client.post(self.GRANT_URL, {'socket_id': '1.1'}, format='json').json()['auth']
        two = client.post(self.GRANT_URL, {'socket_id': '2.2'}, format='json').json()['auth']
        self.assertNotEqual(one, two)

    def test_no_socket_id_returns_metadata_without_a_signature(self):
        client = _authed_client(TENANT_A, USER_A)
        data = client.post(self.GRANT_URL, {}, format='json').json()
        self.assertNotIn('auth', data)
        self.assertEqual(data['key'], PUSHER_KEY)
        self.assertEqual(data['cluster'], PUSHER_CLUSTER)
        self.assertEqual(data['event'], 'VendorChannelBroadcast')
        self.assertEqual(data['echo_event'], '.VendorChannelBroadcast')

    def test_malformed_socket_id_is_refused(self):
        """A socket id carrying ':' could splice the signed string."""
        client = _authed_client(TENANT_A, USER_A)
        for bad in ('abc', '1.1:private-vendor-channel.evil', '', '.', '1.'):
            response = client.post(self.GRANT_URL, {'socket_id': bad}, format='json')
            self.assertEqual(response.status_code, 403, bad)

    def test_missing_pusher_secret_degrades_to_503(self):
        with override_settings(PUSHER_SECRET=''):
            client = _authed_client(TENANT_A, USER_A)
            self.assertEqual(client.post(self.GRANT_URL, {}, format='json').status_code, 503)

    def test_resolve_vendor_uid_reads_only_the_config_table(self):
        self.assertEqual(realtime.resolve_vendor_uid(TENANT_A), VENDOR_A)
        self.assertEqual(realtime.resolve_vendor_uid(TENANT_B), VENDOR_B)


# ---------------------------------------------------------------------------
# B. The pinned normalised envelope — all 11 types + fallback
# ---------------------------------------------------------------------------

ENVELOPE_KEYS = {
    'id', 'wamid', 'direction', 'type', 'status', 'timestamp', 'text',
    'media', 'location', 'contacts', 'interactive', 'template',
    'reply_to', 'error',
}

MEDIA_LINK = (
    'https://whatsappapi.celiyo.com/api/vendor-uid-aaaa-0001/media/'
    'vendors/vendor-uid-aaaa-0001/whatsapp_media/images/68a1b2c3d4e5f.jpg'
)


def _media_row(media_type, mime, filename, caption=None):
    """A Laravel inbound media row: everything flattened into media_values."""
    return {
        '_uid': f'msg-{media_type}',
        'wamid': f'wamid.{media_type}',
        'is_incoming_message': 1,
        'status': 'received',
        'message': None,
        'messaged_at': '2026-08-20T10:00:00+00:00',
        'message_type': 'text',  # Laravel hardcodes this for every inbound row
        'media_values': {
            'type': media_type,
            'link': MEDIA_LINK.replace('images', f'{media_type}s').replace('.jpg', filename[-4:]),
            'caption': caption,
            'mime_type': mime,
            'file_name': filename,
            'original_filename': filename,
        },
    }


class NormalizedEnvelopeTest(TestCase):

    def _assert_envelope(self, envelope):
        self.assertEqual(set(envelope), ENVELOPE_KEYS)
        self.assertIn(envelope['direction'], ('in', 'out'))
        self.assertIn(
            envelope['status'], ('pending', 'sent', 'delivered', 'read', 'failed'),
        )

    # -- 1. text ----------------------------------------------------------

    def test_text(self):
        e = normalize_message({
            '_uid': 'm1', 'wamid': 'wamid.1', 'is_incoming_message': 1,
            'status': 'read', 'message': 'hello there',
            'messaged_at': '2026-08-20T10:00:00+00:00', 'message_type': 'text',
        }, TENANT_A)
        self._assert_envelope(e)
        self.assertEqual(e['type'], 'text')
        self.assertEqual(e['direction'], 'in')
        self.assertEqual(e['status'], 'read')
        self.assertEqual(e['text'], 'hello there')
        self.assertIsNone(e['media'])

    # -- 2-6. the five media types ---------------------------------------

    def test_image(self):
        e = normalize_message(_media_row('image', 'image/jpeg', 'abc.jpg', 'a caption'), TENANT_A)
        self._assert_envelope(e)
        self.assertEqual(e['type'], 'image')
        self.assertEqual(e['media']['mime'], 'image/jpeg')
        self.assertEqual(e['media']['filename'], 'abc.jpg')
        self.assertEqual(e['media']['caption'], 'a caption')
        # Caption is surfaced as text so a plain renderer still shows something.
        self.assertEqual(e['text'], 'a caption')
        self.assertTrue(e['media']['url'].startswith('/api/whatsapp/media/'))
        self.assertTrue(e['media']['url'].endswith('/'))

    def test_video(self):
        e = normalize_message(_media_row('video', 'video/mp4', 'clip.mp4'), TENANT_A)
        self._assert_envelope(e)
        self.assertEqual(e['type'], 'video')
        self.assertEqual(e['media']['mime'], 'video/mp4')

    def test_audio(self):
        e = normalize_message(_media_row('audio', 'audio/ogg', 'note.ogg'), TENANT_A)
        self._assert_envelope(e)
        self.assertEqual(e['type'], 'audio')
        self.assertIsNone(e['media']['caption'])

    def test_document(self):
        e = normalize_message(_media_row('document', 'application/pdf', 'quote.pdf'), TENANT_A)
        self._assert_envelope(e)
        self.assertEqual(e['type'], 'document')
        self.assertEqual(e['media']['filename'], 'quote.pdf')

    def test_sticker(self):
        e = normalize_message(_media_row('sticker', 'image/webp', 'sticker.webp'), TENANT_A)
        self._assert_envelope(e)
        self.assertEqual(e['type'], 'sticker')

    def test_media_url_is_never_the_raw_laravel_public_route(self):
        e = normalize_message(_media_row('image', 'image/png', 'x.png'), TENANT_A)
        self.assertNotIn('whatsappapi.celiyo.com', e['media']['url'])
        self.assertNotIn('whatsapp_media', e['media']['url'])

    # -- 7. location ------------------------------------------------------

    def test_location(self):
        e = normalize_message({
            '_uid': 'm7', 'is_incoming_message': 1, 'status': 'received',
            'message': None, 'messaged_at': '2026-08-20T10:00:00+00:00',
            'message_type': 'text',
            'other_message_data': {
                'type': 'location',
                'data': {
                    'latitude': 19.076, 'longitude': 72.8777,
                    'name': 'Mumbai HQ', 'address': 'Bandra Kurla Complex',
                },
            },
        }, TENANT_A)
        self._assert_envelope(e)
        self.assertEqual(e['type'], 'location')
        self.assertAlmostEqual(e['location']['lat'], 19.076)
        self.assertAlmostEqual(e['location']['lng'], 72.8777)
        self.assertEqual(e['location']['name'], 'Mumbai HQ')
        self.assertEqual(e['location']['address'], 'Bandra Kurla Complex')

    def test_location_nested_under_dunder_data(self):
        """The raw model shape leaves other_message_data inside __data."""
        e = normalize_message({
            '_uid': 'm7b', 'is_incoming_message': 1, 'status': 'received',
            'messaged_at': '2026-08-20T10:00:00+00:00',
            '__data': {'other_message_data': {
                'type': 'location', 'data': {'latitude': 1.5, 'longitude': 2.5},
            }},
        }, TENANT_A)
        self.assertEqual(e['type'], 'location')
        self.assertAlmostEqual(e['location']['lat'], 1.5)

    # -- 8. contacts ------------------------------------------------------

    def test_contacts(self):
        card = {'name': {'formatted_name': 'Asha Rao'},
                'phones': [{'phone': '+919000000001', 'type': 'CELL'}]}
        e = normalize_message({
            '_uid': 'm8', 'is_incoming_message': 1, 'status': 'received',
            'messaged_at': '2026-08-20T10:00:00+00:00',
            'other_message_data': {'type': 'contacts', 'data': [card]},
        }, TENANT_A)
        self._assert_envelope(e)
        self.assertEqual(e['type'], 'contacts')
        self.assertEqual(e['contacts'], [card])

    # -- 9. interactive ---------------------------------------------------

    def test_interactive_button_reply(self):
        e = normalize_message({
            '_uid': 'm9', 'is_incoming_message': 1, 'status': 'received',
            'message': 'Book a demo',
            'messaged_at': '2026-08-20T10:00:00+00:00',
            'interaction_message_data': {
                'type': 'button_reply',
                'button_reply': {'id': 'btn_1', 'title': 'Book a demo'},
            },
        }, TENANT_A)
        self._assert_envelope(e)
        self.assertEqual(e['type'], 'interactive')
        self.assertEqual(e['interactive']['type'], 'button_reply')
        self.assertEqual(e['text'], 'Book a demo')

    def test_interactive_flow_reply(self):
        e = normalize_message({
            '_uid': 'm9b', 'is_incoming_message': 1, 'status': 'received',
            'messaged_at': '2026-08-20T10:00:00+00:00',
            'other_message_data': {
                'type': 'flow_reply',
                'flow_reply_data': {'screen': 'SURVEY', 'answer': 'yes'},
            },
        }, TENANT_A)
        self.assertEqual(e['type'], 'interactive')
        self.assertEqual(e['interactive']['kind'], 'flow_reply')
        self.assertEqual(e['interactive']['data']['answer'], 'yes')

    # -- 10. button -------------------------------------------------------

    def test_button(self):
        e = normalize_message({
            '_uid': 'm10', 'is_incoming_message': 1, 'status': 'received',
            'message': 'Stop promotions',
            'messaged_at': '2026-08-20T10:00:00+00:00',
            'button': {'text': 'Stop promotions', 'payload': 'STOP'},
        }, TENANT_A)
        self._assert_envelope(e)
        self.assertEqual(e['type'], 'button')
        self.assertEqual(e['interactive']['payload'], 'STOP')
        self.assertEqual(e['text'], 'Stop promotions')

    # -- 11. template -----------------------------------------------------

    def test_template(self):
        components = [
            {'type': 'HEADER', 'format': 'TEXT', 'text': 'Hi {{1}}'},
            {'type': 'BODY', 'text': 'Your appointment is on {{1}}.'},
        ]
        e = normalize_message({
            '_uid': 'm11', 'wamid': 'wamid.11', 'is_incoming_message': 0,
            'status': 'delivered', 'messaged_at': '2026-08-20T10:00:00+00:00',
            'message_type': 'template',
            'template_name': 'appointment_reminder',
            'template_data': {'components': components, 'language': 'en'},
        }, TENANT_A)
        self._assert_envelope(e)
        self.assertEqual(e['type'], 'template')
        self.assertEqual(e['direction'], 'out')
        self.assertEqual(e['template']['name'], 'appointment_reminder')
        self.assertEqual(e['template']['components'], components)

    def test_template_components_fall_back_across_laravel_key_names(self):
        for key in ('template_components', 'template_component_values',
                    'submitted_template_components'):
            e = normalize_message({
                '_uid': f'm11-{key}', 'is_incoming_message': 0, 'status': 'sent',
                'messaged_at': '2026-08-20T10:00:00+00:00',
                'template_name': 't', key: [{'type': 'BODY', 'text': 'x'}],
            }, TENANT_A)
            self.assertEqual(e['type'], 'template', key)
            self.assertEqual(len(e['template']['components']), 1, key)

    # -- 12. the unsupported fallback -------------------------------------

    def test_unknown_type_degrades_to_unsupported_and_keeps_text(self):
        e = normalize_message({
            '_uid': 'm12', 'is_incoming_message': 1, 'status': 'received',
            'message': 'Message type not supported',
            'messaged_at': '2026-08-20T10:00:00+00:00',
            'message_type': 'order',  # a Meta type we do not model
        }, TENANT_A)
        self._assert_envelope(e)
        self.assertEqual(e['type'], 'unsupported')
        self.assertEqual(e['text'], 'Message type not supported')

    def test_empty_row_degrades_rather_than_raising(self):
        e = normalize_message({}, TENANT_A)
        self._assert_envelope(e)
        self.assertEqual(e['type'], 'unsupported')

    def test_junk_input_never_raises_and_is_never_dropped(self):
        for junk in (None, 'a string', 12345, [], {'__data': 'not a dict'},
                     {'media_values': 'not a dict'}, {'status': 12}):
            e = normalize_message(junk, TENANT_A)
            self.assertEqual(set(e), ENVELOPE_KEYS, junk)
            self.assertIn(e['type'], ('unsupported', 'text'))

    def test_normalize_messages_keeps_every_row(self):
        rows = [{'_uid': str(i), 'message': f'm{i}', 'messaged_at': '2026-08-20T10:00:00+00:00'}
                for i in range(5)]
        rows.append('garbage')
        self.assertEqual(len(normalize_messages(rows, TENANT_A)), 6)

    def test_all_eleven_types_are_representable(self):
        self.assertEqual(
            {'text', 'image', 'video', 'audio', 'document', 'sticker',
             'location', 'contacts', 'interactive', 'button', 'template'},
            {
                normalize_message(row, TENANT_A)['type']
                for row in [
                    {'message': 'hi'},
                    _media_row('image', 'image/jpeg', 'a.jpg'),
                    _media_row('video', 'video/mp4', 'a.mp4'),
                    _media_row('audio', 'audio/ogg', 'a.ogg'),
                    _media_row('document', 'application/pdf', 'a.pdf'),
                    _media_row('sticker', 'image/webp', 'a.webp'),
                    {'other_message_data': {'type': 'location',
                                            'data': {'latitude': 1, 'longitude': 2}}},
                    {'other_message_data': {'type': 'contacts', 'data': [{'name': {}}]}},
                    {'interaction_message_data': {'type': 'list_reply'}},
                    {'button': {'text': 'x'}},
                    {'template_name': 'x', 'template_data': {'components': []}},
                ]
            },
        )

    # -- status / direction -----------------------------------------------

    def test_status_vocabulary_is_collapsed_to_the_pinned_enum(self):
        cases = {
            'pending': 'pending', 'queued': 'pending', 'accepted': 'sent',
            'sent': 'sent', 'delivered': 'delivered', 'received': 'delivered',
            'read': 'read', 'failed': 'failed', 'weird-new-status': 'delivered',
        }
        for raw, expected in cases.items():
            e = normalize_message({'status': raw, 'is_incoming_message': 1}, TENANT_A)
            self.assertEqual(e['status'], expected, raw)

    def test_outbound_without_status_is_pending_not_delivered(self):
        e = normalize_message({'is_incoming_message': 0, 'message': 'x'}, TENANT_A)
        self.assertEqual(e['status'], 'pending')

    def test_error_details_are_surfaced_and_empty_string_is_not(self):
        self.assertIsNone(
            normalize_message({'whatsapp_message_error': ''}, TENANT_A)['error'])
        self.assertEqual(
            normalize_message({'whatsapp_message_error': 'Re-engagement message'},
                              TENANT_A)['error'],
            'Re-engagement message',
        )


# ---------------------------------------------------------------------------
# C2. Reply-window normalisation
# ---------------------------------------------------------------------------

class ReplyWindowTest(TestCase):

    def test_laravels_key_name_is_mapped_to_expires_at(self):
        window = normalize_reply_window({
            'reply_window_open': True,
            'reply_window_expires_at': '2026-08-21T10:00:00+00:00',
            'requires_template': False,
        })
        self.assertTrue(window['open'])
        self.assertEqual(window['expires_at'], '2026-08-21T10:00:00+00:00')
        self.assertFalse(window['requires_template'])

    def test_requires_template_is_derived_when_laravel_omits_it(self):
        """The vendor chat routes emit only is_reply_window_open."""
        window = normalize_reply_window({
            'is_reply_window_open': False,
            'reply_window_expires_at': '2026-08-21T10:00:00+00:00',
        })
        self.assertFalse(window['open'])
        self.assertTrue(window['requires_template'])

    def test_absent_window_is_null_not_a_guess(self):
        window = normalize_reply_window({})
        self.assertIsNone(window['open'])
        self.assertIsNone(window['expires_at'])
        self.assertIsNone(window['requires_template'])


@override_settings(**PUSHER_SETTINGS)
class ChatEndpointTest(TestCase):
    """The chat endpoint must emit the normalised envelope, newest-last."""

    def setUp(self):
        WhatsAppVendorConfig.objects.create(
            tenant_id=TENANT_A, vendor_uid=VENDOR_A, api_token=VENDOR_TOKEN_A,
        )
        self.client_a = _authed_client(TENANT_A, USER_A)

    @patch('whatsapp_integration.views._adapter_from_request')
    def test_history_is_normalised_and_reversed_to_newest_last(self, mock_adapter):
        mock_adapter.return_value.get_chat_history.return_value = {
            'result': 'success',
            'reply_window_open': True,
            'reply_window_expires_at': '2026-08-21T10:00:00+00:00',
            'messages': [
                {'_uid': 'newest', 'message': 'second',
                 'messaged_at': '2026-08-20T11:00:00+00:00', 'is_incoming_message': 1},
                {'_uid': 'oldest', 'message': 'first',
                 'messaged_at': '2026-08-20T10:00:00+00:00', 'is_incoming_message': 1},
            ],
            'pagination': {'current_page': 1, 'last_page': 3, 'has_more': True},
        }
        response = self.client_a.get('/api/whatsapp/chat/?contact=919000000001')
        self.assertEqual(response.status_code, 200)
        body = response.json()

        self.assertEqual([m['id'] for m in body['messages']], ['oldest', 'newest'])
        self.assertEqual(set(body['messages'][0]), ENVELOPE_KEYS)
        self.assertEqual(body['next_cursor'], 'p2')
        self.assertTrue(body['has_more'])

        # C2: the expiry must arrive under every name a frontend might read.
        self.assertEqual(body['reply_window']['expires_at'], '2026-08-21T10:00:00+00:00')
        self.assertEqual(body['window_expires_at'], '2026-08-21T10:00:00+00:00')
        self.assertEqual(body['expires_at'], '2026-08-21T10:00:00+00:00')

    def test_contact_is_required(self):
        self.assertEqual(self.client_a.get('/api/whatsapp/chat/').status_code, 400)

    def test_unauthenticated_history_is_rejected(self):
        response = APIClient().get('/api/whatsapp/chat/?contact=919000000001')
        self.assertIn(response.status_code, (401, 403))

    @patch('whatsapp_integration.views._adapter_from_request')
    def test_send_requires_the_send_permission(self, mock_adapter):
        view_only = _authed_client(
            TENANT_A, USER_A, permissions={'whatsapp': {'messages': {'view': 'all'}}},
        )
        response = view_only.post(
            '/api/whatsapp/chat/send/',
            {'contact': '919000000001', 'text': 'hi'}, format='json',
        )
        self.assertEqual(response.status_code, 403)
        mock_adapter.return_value.send_text_message.assert_not_called()

    @patch('whatsapp_integration.views._adapter_from_request')
    def test_send_returns_an_optimistic_envelope(self, mock_adapter):
        mock_adapter.return_value.send_text_message.return_value = {
            'result': 'success', 'wa_message_id': 'wamid.OUT1',
        }
        response = self.client_a.post(
            '/api/whatsapp/chat/send/',
            {'contact': '9000000001', 'text': 'hello'}, format='json',
        )
        self.assertEqual(response.status_code, 200)
        message = response.json()['message']
        self.assertEqual(set(message), ENVELOPE_KEYS)
        self.assertEqual(message['direction'], 'out')
        self.assertEqual(message['type'], 'text')
        self.assertEqual(message['wamid'], 'wamid.OUT1')

    @patch('whatsapp_integration.views._adapter_from_request')
    def test_send_template_passes_components_through(self, mock_adapter):
        mock_adapter.return_value.send_message.return_value = {'wa_message_id': 'wamid.T1'}
        components = [{'type': 'body', 'parameters': [{'type': 'text', 'text': 'Asha'}]}]
        response = self.client_a.post(
            '/api/whatsapp/chat/send-template/',
            {'contact': '9000000001', 'template_uid': 'tpl-1', 'components': components},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        kwargs = mock_adapter.return_value.send_message.call_args.kwargs
        self.assertEqual(kwargs['template_components'], components)
        self.assertEqual(response.json()['message']['type'], 'template')

    @patch('whatsapp_integration.views._adapter_from_request')
    def test_send_template_requires_a_template_uid(self, mock_adapter):
        response = self.client_a.post(
            '/api/whatsapp/chat/send-template/',
            {'contact': '9000000001'}, format='json',
        )
        self.assertEqual(response.status_code, 400)

    @patch('whatsapp_integration.views._adapter_from_request')
    def test_conversations_carry_last_message_and_unread_count(self, mock_adapter):
        mock_adapter.return_value.get_conversations.return_value = {
            'data': {
                'contacts': [{
                    '_uid': 'c1', 'wa_id': '919000000001', 'full_name': 'Asha Rao',
                    'unread_messages_count': 3,
                    'is_reply_window_open': True,
                    'reply_window_expires_at': '2026-08-21T10:00:00+00:00',
                    'last_message': {
                        '_uid': 'lm1', 'message': 'see you then',
                        'is_incoming_message': 1, 'status': 'read',
                        'messaged_at': '2026-08-20T11:00:00+00:00',
                    },
                }],
                'pagination': {'total': 1, 'has_more': False},
            },
        }
        body = self.client_a.get('/api/whatsapp/chat/conversations/').json()
        row = body['results'][0]
        self.assertEqual(row['unread_count'], 3)
        self.assertEqual(row['contact']['wa_id'], '919000000001')
        self.assertEqual(set(row['last_message']), ENVELOPE_KEYS)
        self.assertEqual(row['window_expires_at'], '2026-08-21T10:00:00+00:00')
        self.assertIsNone(body['next_cursor'])


# ---------------------------------------------------------------------------
# D. Media proxy
# ---------------------------------------------------------------------------

class MediaReferenceTest(TestCase):

    GOOD = 'vendors/v1/whatsapp_media/images/68a1b2c3.jpg'

    def test_traversal_shapes_are_all_refused(self):
        hostile = [
            '../.env', '../../.env', 'a/../../.env', './../.env',
            'vendors/../../.env', '/etc/passwd', '//evil.com/x',
            'http://evil.com/x', 'C:\\Windows\\win.ini', 'a\\..\\b',
            'a/..', '..', 'x\x00.jpg', 'storage/../.env',
        ]
        for path in hostile:
            self.assertFalse(is_safe_media_path(path), path)
            with self.assertRaises(MediaReferenceError, msg=path):
                make_media_id(TENANT_A, path)

    def test_a_normal_media_path_is_accepted(self):
        self.assertTrue(is_safe_media_path(self.GOOD))
        self.assertEqual(resolve_media_id(TENANT_A, make_media_id(TENANT_A, self.GOOD)), self.GOOD)

    def test_ids_are_deterministic_so_urls_stay_cacheable(self):
        self.assertEqual(make_media_id(TENANT_A, self.GOOD), make_media_id(TENANT_A, self.GOOD))

    def test_an_id_minted_for_one_tenant_does_not_verify_for_another(self):
        media_id = make_media_id(TENANT_A, self.GOOD)
        with self.assertRaises(MediaReferenceError):
            resolve_media_id(TENANT_B, media_id)

    def test_a_forged_id_is_refused(self):
        import base64
        forged = base64.urlsafe_b64encode(b'../.env').decode().rstrip('=') + '.' + 'f' * 32
        with self.assertRaises(MediaReferenceError):
            resolve_media_id(TENANT_A, forged)

    def test_tampering_with_the_payload_breaks_the_signature(self):
        media_id = make_media_id(TENANT_A, self.GOOD)
        encoded, _, signature = media_id.rpartition('.')
        import base64
        evil = base64.urlsafe_b64encode(b'../.env').decode().rstrip('=')
        with self.assertRaises(MediaReferenceError):
            resolve_media_id(TENANT_A, f'{evil}.{signature}')

    def test_content_type_allowlist(self):
        self.assertEqual(safe_content_type('image/jpeg'), 'image/jpeg')
        self.assertEqual(safe_content_type('application/pdf; charset=binary'), 'application/pdf')
        for hostile in ('text/html', 'image/svg+xml', 'application/javascript', '', None):
            self.assertEqual(safe_content_type(hostile), 'application/octet-stream')


@override_settings(**PUSHER_SETTINGS)
class MediaProxyViewTest(TestCase):

    GOOD = 'vendors/v1/whatsapp_media/images/68a1b2c3.jpg'

    def setUp(self):
        WhatsAppVendorConfig.objects.create(
            tenant_id=TENANT_A, vendor_uid=VENDOR_A, api_token=VENDOR_TOKEN_A,
        )
        self.media_id = make_media_id(TENANT_A, self.GOOD)
        self.url = f'/api/whatsapp/media/{self.media_id}/'

    def test_unauthenticated_caller_is_rejected(self):
        self.assertIn(APIClient().get(self.url).status_code, (401, 403))

    @patch('whatsapp_integration.views._adapter_from_request')
    def test_authenticated_caller_gets_hardened_headers(self, mock_adapter):
        mock_adapter.return_value.fetch_media.return_value = (b'\xff\xd8\xff', 'image/jpeg', {})
        response = _authed_client(TENANT_A, USER_A).get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/jpeg')
        self.assertTrue(response['Content-Disposition'].startswith('attachment;'))
        self.assertIn('68a1b2c3.jpg', response['Content-Disposition'])
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        # The path we actually fetched is the signed one, not anything a
        # caller supplied.
        self.assertEqual(mock_adapter.return_value.fetch_media.call_args.args[0], self.GOOD)

    @patch('whatsapp_integration.views._adapter_from_request')
    def test_a_hostile_content_type_is_collapsed_to_octet_stream(self, mock_adapter):
        mock_adapter.return_value.fetch_media.return_value = (
            b'<script>alert(1)</script>', 'text/html', {},
        )
        response = _authed_client(TENANT_A, USER_A).get(self.url)
        self.assertEqual(response['Content-Type'], 'application/octet-stream')

    @patch('whatsapp_integration.views._adapter_from_request')
    def test_another_tenants_media_id_is_a_404(self, mock_adapter):
        response = _authed_client(TENANT_B, USER_B).get(self.url)
        self.assertEqual(response.status_code, 404)
        mock_adapter.return_value.fetch_media.assert_not_called()

    @patch('whatsapp_integration.views._adapter_from_request')
    def test_traversal_through_the_legacy_path_route_is_refused(self, mock_adapter):
        """
        This route used to hand `<path:filename>` straight to Laravel's
        unguarded public_path(), making the gateway's arbitrary file read
        reachable through DigiCRM. It must never call out now.
        """
        client = _authed_client(TENANT_A, USER_A)
        for hostile in ('../.env', '../../.env', 'vendors/../../.env', 'a/../../../etc/passwd'):
            response = client.get(f'/api/whatsapp/media/{hostile}/')
            self.assertIn(response.status_code, (400, 404), hostile)
        mock_adapter.return_value.fetch_media.assert_not_called()

    @patch('whatsapp_integration.views._adapter_from_request')
    def test_a_garbage_media_id_is_a_404(self, mock_adapter):
        client = _authed_client(TENANT_A, USER_A)
        self.assertEqual(client.get('/api/whatsapp/media/not-a-real-id/').status_code, 404)
        mock_adapter.return_value.fetch_media.assert_not_called()


# ---------------------------------------------------------------------------
# C3. Laravel returns HTTP 200 on auth failure
# ---------------------------------------------------------------------------

class LaravelFailedBodyTest(TestCase):
    """
    ApiVendorAccessCheckpost rejects bad credentials through
    processExternalApiResponse(), which calls response()->json() with no status
    argument — so every auth failure is HTTP 200 with {"result":"failed"}.
    Checking only status_code >= 400 reported "message sent" for a rotated
    token.
    """

    def test_success_bodies_pass_through(self):
        for body in ({'result': 'success', 'wa_message_id': 'x'},
                     {'wa_message_id': 'x'},
                     {'result': 'SUCCESS'},
                     [],
                     None):
            _raise_on_failed_body(body)  # must not raise

    def test_invalid_token_is_raised_as_an_error(self):
        with self.assertRaises(LaravelAdapterError) as ctx:
            _raise_on_failed_body({'result': 'failed', 'message': 'Invalid Token'})
        self.assertIn('vendor credentials', str(ctx.exception))
        self.assertEqual(ctx.exception.status_code, 502)

    def test_invalid_vendor_and_inactive_vendor_are_raised(self):
        for message in ('Invalid Vendor', 'Vendor account is not in active state'):
            with self.assertRaises(LaravelAdapterError):
                _raise_on_failed_body({'result': 'failed', 'message': message})

    def test_a_non_auth_failure_still_raises_rather_than_looking_like_a_send(self):
        with self.assertRaises(LaravelAdapterError) as ctx:
            _raise_on_failed_body({'result': 'failed',
                                   'message': 'An error occurred while fetching contacts'})
        self.assertEqual(ctx.exception.status_code, 502)

    @patch('whatsapp_integration.services.laravel_adapter.requests.request')
    def test_a_200_auth_failure_does_not_reach_the_caller_as_success(self, mock_request):
        from whatsapp_integration.services.laravel_adapter import LaravelWhatsAppAdapter
        mock_request.return_value.status_code = 200
        mock_request.return_value.json.return_value = {
            'result': 'failed', 'message': 'Invalid Token',
        }
        adapter = LaravelWhatsAppAdapter(
            tenant_id=str(TENANT_A), vendor_uid=VENDOR_A, api_token='wrong-token',
        )
        with self.assertRaises(LaravelAdapterError):
            adapter.send_text_message(phone='919000000001', name='x', text='hi')


# ---------------------------------------------------------------------------
# C1. template_data extraction
# ---------------------------------------------------------------------------

class TemplateBodyTest(TestCase):
    """
    WhatsAppTemplateController::apiGetTemplates returns the raw Meta object
    under `template_data`, not `components`. Reading `components` made every
    AI template body an empty string.
    """

    BODY = 'Hi {{1}}, your appointment is on {{2}}.'

    def test_body_is_read_from_template_data(self):
        self.assertEqual(_template_body_text({
            '_uid': 't1', 'template_name': 'appointment',
            'template_data': {'components': [
                {'type': 'HEADER', 'text': 'Reminder'},
                {'type': 'BODY', 'text': self.BODY},
            ]},
        }), self.BODY)

    def test_a_flat_components_key_still_works(self):
        self.assertEqual(
            _template_body_text({'components': [{'type': 'BODY', 'text': self.BODY}]}),
            self.BODY,
        )

    def test_missing_or_malformed_templates_return_empty_string(self):
        for template in ({}, {'template_data': None}, {'template_data': {}},
                         {'template_data': {'components': 'nope'}},
                         {'template_data': {'components': [{'type': 'HEADER'}]}},
                         {'template_data': {'components': ['junk']}},
                         None):
            self.assertEqual(_template_body_text(template), '')
