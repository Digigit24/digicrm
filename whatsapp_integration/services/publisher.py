"""
Publish full WhatsApp message events from DigiCRM's own inbound webhook.

Why this exists
---------------
Laravel already broadcasts ``VendorChannelBroadcast`` on
``private-vendor-channel.{vendorUid}``, but that payload is a NOTIFICATION, not
a message::

    {contactUid, contactWaId, isNewIncomingMessage, lastMessageUid, message_status?}

No body, no wamid, no media.  Every client that wants to render the message has
to turn around and refetch the whole conversation.  That is tolerable on a
desktop browser and wrong on a phone: one inbound message costs a full
conversation GET over a mobile network.

DigiCRM's inbound webhook (``WhatsAppWebhookView``) already receives the body
and the wamid and currently throws them away after writing a ``LeadActivity``.
So we publish them ourselves, on the SAME tenant channel, under a DIFFERENT
event name, carrying the SAME normalised envelope that
``GET /api/whatsapp/chat/`` returns.  A client that binds the new event renders
instantly; a client that does not is completely unaffected.

Strictly additive
-----------------
* Laravel's ``VendorChannelBroadcast`` keeps firing, untouched.
* We never publish under Laravel's event name, so the two never collide.
* Clients discover the new names from the grant endpoint
  (``digicrm_event`` / ``digicrm_status_event``), the same way they already
  discover ``event`` / ``echo_event``.

Best-effort, always
-------------------
Publishing is a side channel.  If Pusher is unconfigured, slow, or broken, we
log and return False; the webhook still records the message.  Postgres is the
source of truth and Pusher only makes the UI feel instant.  This mirrors
``telephony/services/realtime.py`` exactly.

Tenant scoping
--------------
The channel is derived from ``WhatsAppVendorConfig`` keyed on the webhook's
tenant (``realtime.resolve_vendor_uid``) and NEVER from anything in the request
body.  A caller cannot name the channel it would like its message published to.
"""

import json
import logging

from django.conf import settings

from whatsapp_integration.services.normalizer import normalize_message
from whatsapp_integration.services.realtime import (
    DIGICRM_MESSAGE_EVENT,
    DIGICRM_STATUS_EVENT,
    RealtimeNotConfigured,
    channel_for_vendor,
    resolve_vendor_uid,
)

logger = logging.getLogger(__name__)

# Pusher rejects a payload over 10KB outright. A long message plus a template
# block can get close, and the rejection would be swallowed by our own
# best-effort wrapper -- i.e. realtime would silently stop working for exactly
# the biggest messages. Above the threshold we publish the envelope with its
# heavy fields stripped and let the client refetch that one message.
_MAX_PAYLOAD_BYTES = 9000

_client = None
_unconfigured_logged = False


def _get_client():
    """Lazily build a pusher.Pusher client. Returns None if not configured."""
    global _client, _unconfigured_logged

    app_id = getattr(settings, 'PUSHER_APP_ID', '') or ''
    key = getattr(settings, 'PUSHER_KEY', '') or ''
    secret = getattr(settings, 'PUSHER_SECRET', '') or ''
    cluster = getattr(settings, 'PUSHER_CLUSTER', '') or ''

    if not (app_id and key and secret and cluster):
        if not _unconfigured_logged:
            logger.info(
                '[WA Publish] Pusher not configured (PUSHER_APP_ID/PUSHER_SECRET '
                'missing) - inbound WhatsApp messages will not be broadcast. '
                'Clients fall back to the existing notify-then-refetch path.'
            )
            _unconfigured_logged = True
        return None

    if _client is None:
        try:
            import pusher
            _client = pusher.Pusher(
                app_id=app_id, key=key, secret=secret, cluster=cluster, ssl=True,
            )
        except Exception:
            # A missing `pusher` package or a bad credential shape must not
            # propagate into webhook handling. Every public function below is
            # documented as never raising, and this is the only import that
            # could break that promise.
            logger.exception('[WA Publish] could not build a Pusher client')
            return None
    return _client


# ---------------------------------------------------------------------------
# Webhook payload -> Laravel-shaped row -> pinned envelope
# ---------------------------------------------------------------------------

def _first(*values):
    for value in values:
        if value not in (None, '', [], {}):
            return value
    return None


def webhook_data_to_row(payload_data, *, incoming=True) -> dict:
    """
    Map the webhook's ``data`` block onto the row shape ``normalize_message``
    understands, so the envelope is produced by the SAME normaliser the REST
    surface uses rather than a second, drifting implementation.

    The mapping is deliberately generous about key names. DigiCRM's webhook is
    fed by n8n, which reshapes Laravel's ``message.received`` payload, and only
    three keys are actually pinned by existing code (``phone``,
    ``message_body``, ``message_wamid``). Everything else is accepted under both
    the n8n-flattened name and Laravel's own name, so the envelope gets richer
    if n8n starts forwarding more without needing a change here.
    """
    data = payload_data if isinstance(payload_data, dict) else {}
    # n8n may forward Laravel's nested `message` object verbatim.
    message = data.get('message') if isinstance(data.get('message'), dict) else {}

    row = {
        '_uid': _first(
            data.get('message_uid'), data.get('uid'), message.get('uid'),
        ),
        'wamid': _first(
            data.get('message_wamid'), data.get('wamid'),
            data.get('whatsapp_message_id'), message.get('whatsapp_message_id'),
        ),
        'is_incoming_message': incoming,
        'status': _first(
            data.get('message_status'), data.get('status'), message.get('status'),
        ),
        'messaged_at': _first(
            data.get('messaged_at'), data.get('timestamp'),
            message.get('messaged_at'),
        ),
        'message': _first(
            data.get('message_body'), data.get('body'), data.get('text'),
            message.get('body'),
        ),
        'whatsapp_message_error': _first(
            data.get('whatsapp_message_error'), data.get('error'),
            message.get('whatsapp_message_error'), message.get('error'),
        ),
        'media_values': _first(data.get('media'), message.get('media')),
        'other_message_data': data.get('other_message_data'),
        'interaction_message_data': data.get('interaction_message_data'),
        'template_data': data.get('template_data'),
        'replied_to_whatsapp_message_id': _first(
            data.get('replied_to_whatsapp_message_id'),
            message.get('replied_to_whatsapp_message_id'),
        ),
        # `message_type` is checked LAST by the normaliser (the adapter hardcodes
        # it to 'text' for every inbound message), so passing it through is safe.
        'message_type': _first(data.get('message_type'), message.get('message_type')),
    }
    # Drop the keys we have nothing for: the normaliser treats absent and empty
    # identically, and a row full of None is harder to read in a log.
    return {k: v for k, v in row.items() if v is not None}


def _contact_wa_id(payload_data) -> str:
    data = payload_data if isinstance(payload_data, dict) else {}
    contact = data.get('contact') if isinstance(data.get('contact'), dict) else {}
    value = _first(
        data.get('phone'), data.get('wa_id'), data.get('contact_wa_id'),
        data.get('contactWaId'), contact.get('phone_number'), contact.get('wa_id'),
    )
    return str(value) if value is not None else ''


def _contact_uid(payload_data) -> str:
    data = payload_data if isinstance(payload_data, dict) else {}
    contact = data.get('contact') if isinstance(data.get('contact'), dict) else {}
    value = _first(
        data.get('contact_uid'), data.get('contactUid'), contact.get('uid'),
    )
    return str(value) if value is not None else ''


def _shrink(envelope: dict) -> dict:
    """Strip the fields that can blow the Pusher size limit, keep it renderable."""
    trimmed = dict(envelope)
    trimmed['template'] = None
    trimmed['contacts'] = None
    trimmed['interactive'] = None
    text = trimmed.get('text')
    if isinstance(text, str) and len(text) > 2000:
        trimmed['text'] = text[:2000]
        trimmed['truncated'] = True
    return trimmed


def _trigger(tenant_id, event: str, payload: dict) -> bool:
    """Publish to this tenant's vendor channel. Never raises."""
    client = _get_client()
    if client is None:
        return False

    try:
        vendor_uid = resolve_vendor_uid(tenant_id)
    except RealtimeNotConfigured:
        # No active vendor config for this tenant. Nothing to publish to; the
        # webhook itself is unaffected.
        logger.info('[WA Publish] no active vendor config for tenant=%s', tenant_id)
        return False
    except Exception:
        logger.exception('[WA Publish] could not resolve vendor uid for tenant=%s', tenant_id)
        return False

    channel = channel_for_vendor(vendor_uid)

    try:
        if len(json.dumps(payload, default=str).encode('utf-8')) > _MAX_PAYLOAD_BYTES:
            message = payload.get('message')
            if isinstance(message, dict):
                payload = {**payload, 'message': _shrink(message)}
    except Exception:
        logger.exception('[WA Publish] could not size payload; publishing as-is')

    try:
        client.trigger(channel, event, payload)
        return True
    except Exception:
        # Best effort by design: an inbound message must be recorded even if
        # the broadcast fails.
        logger.exception('[WA Publish] failed to publish %s on %s', event, channel)
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def publish_inbound_message(tenant_id, payload_data) -> bool:
    """
    Broadcast one inbound message with its FULL body.

    The payload is shaped for the parser the frontend already ships
    (``sepratecrm/src/services/whatsappRealtimeService.ts::readMessageEvent``):
    a ``message`` object holding the pinned envelope and a ``contact`` string
    holding the wa_id. No client change is needed beyond binding the new event.
    """
    if not tenant_id:
        return False

    row = webhook_data_to_row(payload_data, incoming=True)
    envelope = normalize_message(row, tenant_id)

    return _trigger(tenant_id, DIGICRM_MESSAGE_EVENT, {
        'message': envelope,
        'contact': _contact_wa_id(payload_data),
        'contact_uid': _contact_uid(payload_data),
    })


def publish_message_status(tenant_id, payload_data) -> bool:
    """
    Broadcast a delivery-status change (sent/delivered/read/failed).

    Flat, because that is what ``readStatusEvent`` reads: ``wamid``, ``id``,
    ``status``, ``error``.
    """
    if not tenant_id:
        return False

    row = webhook_data_to_row(payload_data, incoming=False)
    envelope = normalize_message(row, tenant_id)

    return _trigger(tenant_id, DIGICRM_STATUS_EVENT, {
        'wamid': envelope.get('wamid'),
        'id': envelope.get('id'),
        'status': envelope.get('status'),
        'error': envelope.get('error'),
        'contact': _contact_wa_id(payload_data),
        'contact_uid': _contact_uid(payload_data),
    })
