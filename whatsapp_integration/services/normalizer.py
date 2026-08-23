"""
Normalise Laravel WhatsApp message rows into DigiCRM's pinned envelope.

Why this exists
---------------
Laravel emits at least three different shapes for the same message
(``AdapterController::chatHistoryByPhone``, ``apiGetContactMessages``,
``apiGetChatContacts``), none of them stable, and the real message type is not
in the field called ``message_type``: for every inbound message that field is
hardcoded to ``'text'``.  The type has to be *derived* from the presence of
``media_values`` / ``other_message_data`` / ``interaction_message_data``.

The frontend and the mobile app both code against one shape, defined here:

    {
      "id", "wamid", "direction": "in|out",
      "type": "text|image|video|audio|document|sticker|location|contacts|
               interactive|button|template|unsupported",
      "status": "pending|sent|delivered|read|failed",
      "timestamp": ISO-8601, "text": str|None,
      "media": {"url","mime","filename","caption"}|None,
      "location": {"lat","lng","name","address"}|None,
      "contacts": [...]|None,
      "interactive": {...}|None,
      "template": {"name","components"}|None,
      "reply_to": str|None, "error": str|None
    }

Hard rule: **this module never raises and never drops a message.**  An input it
cannot make sense of degrades to ``type: "unsupported"`` carrying whatever text
was available.  A single malformed attachment must not blank out a whole
conversation.
"""

import logging
import re
from datetime import datetime, timezone as dt_timezone

from whatsapp_integration.services.media import media_url

logger = logging.getLogger(__name__)

KNOWN_TYPES = {
    'text', 'image', 'video', 'audio', 'document', 'sticker',
    'location', 'contacts', 'interactive', 'button', 'template',
}
MEDIA_TYPES = {'image', 'video', 'audio', 'document', 'sticker'}
UNSUPPORTED = 'unsupported'

# Laravel/Meta status vocabulary -> pinned envelope vocabulary.
# 'received' is what an inbound row carries before anyone opens the chat; from
# the CRM's point of view that message has arrived, i.e. delivered.
_STATUS_MAP = {
    'pending': 'pending',
    'queued': 'pending',
    'scheduled': 'pending',
    'accepted': 'sent',
    'sent': 'sent',
    'delivered': 'delivered',
    'received': 'delivered',
    'read': 'read',
    'failed': 'failed',
    'error': 'failed',
    'rejected': 'failed',
    'deleted': 'failed',
}

# Laravel rewrites media links to /api/{vendorUid}/media/<relative path>.
_MEDIA_PATH_RE = re.compile(r'/api/[^/]+/media/(?P<path>.+)$')


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def _as_list(value):
    return value if isinstance(value, list) else []


def _clean_text(value):
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    return value or None


def _first(*values):
    for value in values:
        if value not in (None, '', [], {}):
            return value
    return None


def _iso(value):
    """Coerce whatever Laravel sent into an ISO-8601 string, or None."""
    if value in (None, ''):
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=dt_timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        # A bare unix timestamp arrives as a string on some Laravel paths.
        if text.isdigit() and len(text) in (10, 13):
            seconds = int(text) / (1000 if len(text) == 13 else 1)
            try:
                return datetime.fromtimestamp(seconds, tz=dt_timezone.utc).isoformat()
            except (OverflowError, OSError, ValueError):
                return None
        return text
    return None


def _media_path_from_link(link):
    """
    Turn a Laravel media URL into the relative path DigiCRM will re-fetch.

    Accepts both the rewritten form
    ``https://host/api/{vendorUid}/media/vendors/x/whatsapp_media/images/a.jpg``
    and the raw asset form ``https://host/vendors/x/whatsapp_media/....``.
    """
    if not link or not isinstance(link, str):
        return None
    match = _MEDIA_PATH_RE.search(link)
    if match:
        return match.group('path').lstrip('/')
    # Raw asset URL: keep everything after the host.
    without_scheme = re.sub(r'^[a-zA-Z][a-zA-Z0-9+.\-]*://[^/]+', '', link)
    return without_scheme.lstrip('/') or None


# ---------------------------------------------------------------------------
# Field extraction
# ---------------------------------------------------------------------------

def _extract_data(raw):
    """
    Laravel's ``__data`` blob, whether it was flattened into the row (the
    adapter shape) or left nested (the raw model shape).
    """
    return _as_dict(raw.get('__data'))


def _get(raw, data, *keys):
    """Read a key from the flattened row first, then from ``__data``."""
    for key in keys:
        if raw.get(key) not in (None, '', [], {}):
            return raw.get(key)
    for key in keys:
        if data.get(key) not in (None, '', [], {}):
            return data.get(key)
    return None


def _direction(raw):
    incoming = raw.get('is_incoming_message')
    if incoming is not None:
        # Laravel casts this to an integer, and some paths stringify it.
        if isinstance(incoming, str):
            return 'in' if incoming.strip() not in ('', '0', 'false', 'False') else 'out'
        return 'in' if incoming else 'out'
    direction = (raw.get('direction') or '').lower()
    if direction in ('in', 'inbound', 'incoming'):
        return 'in'
    if direction in ('out', 'outbound', 'outgoing'):
        return 'out'
    return 'out'


def _status(raw, direction):
    value = raw.get('status')
    value = value.strip().lower() if isinstance(value, str) else ''
    mapped = _STATUS_MAP.get(value)
    if mapped:
        return mapped
    if value:
        logger.debug('Unrecognised WhatsApp message status %r', value)
    # No status at all: an inbound message is by definition delivered; an
    # outbound one that Laravel has not stamped yet is still in flight.
    return 'delivered' if direction == 'in' else 'pending'


def _error(raw):
    # WhatsAppMessageLogModel::whatsappMessageError is tri-valued: null, '',
    # or a details string. Only the last is a real error.
    return _clean_text(raw.get('whatsapp_message_error') or raw.get('error'))


def _derive_type(raw, data, media_values, other_data, interaction, template_bits):
    """
    Derive the true message type.

    ``message_type`` is checked LAST on purpose: the adapter hardcodes it to
    ``'text'`` for every inbound message, so trusting it first would flatten
    every image, location and button reply into a blank text bubble.
    """
    media_type = (media_values.get('type') or '').lower()
    if media_type in MEDIA_TYPES:
        return media_type

    other_type = (other_data.get('type') or '').lower()
    if other_type in ('location', 'contacts'):
        return other_type
    if other_type in ('flow_reply', 'interactive', 'nfm_reply'):
        return 'interactive'

    if interaction:
        return 'interactive'

    if raw.get('button') or data.get('button'):
        return 'button'

    if template_bits:
        return 'template'

    declared = (_get(raw, data, 'message_type', 'type') or '').lower()
    if declared in KNOWN_TYPES:
        # A declared media type with no media_values block means the file is
        # gone (MediaEngine swallows unmapped mime types and stores nothing).
        # Report it honestly rather than pretending it is text.
        if declared in MEDIA_TYPES and not media_values:
            return declared
        return declared
    if declared and declared not in KNOWN_TYPES:
        return UNSUPPORTED

    if _clean_text(_get(raw, data, 'text', 'message', 'message_raw')):
        return 'text'

    return UNSUPPORTED


def _build_media(tenant_id, media_values):
    if not media_values:
        return None
    link = _first(media_values.get('url'), media_values.get('link'))
    path = _media_path_from_link(link)
    url = media_url(tenant_id, path) if path else None
    filename = _first(
        media_values.get('original_filename'),
        media_values.get('file_name'),
        media_values.get('filename'),
    )
    return {
        'url': url,
        'mime': media_values.get('mime_type') or media_values.get('mime'),
        'filename': filename,
        'caption': _clean_text(media_values.get('caption')),
    }


def _build_location(other_data, raw, data):
    payload = _as_dict(other_data.get('data')) or _as_dict(_get(raw, data, 'location'))
    if not payload:
        return None
    lat = _first(payload.get('latitude'), payload.get('lat'))
    lng = _first(payload.get('longitude'), payload.get('lng'), payload.get('long'))
    try:
        lat = float(lat) if lat is not None else None
        lng = float(lng) if lng is not None else None
    except (TypeError, ValueError):
        lat = lng = None
    if lat is None and lng is None:
        return None
    return {
        'lat': lat,
        'lng': lng,
        'name': _clean_text(payload.get('name')),
        'address': _clean_text(payload.get('address')),
    }


def _build_contacts(other_data, raw, data):
    payload = _first(other_data.get('data'), _get(raw, data, 'contacts'))
    if isinstance(payload, dict):
        # Meta sometimes nests the array under a "contacts" key.
        payload = _first(payload.get('contacts'), [payload])
    contacts = _as_list(payload)
    return contacts or None


def _build_interactive(interaction, other_data, raw, data):
    if other_data.get('type') in ('flow_reply', 'nfm_reply'):
        return {
            'kind': 'flow_reply',
            'data': other_data.get('flow_reply_data') or other_data.get('data'),
        }
    payload = _first(interaction, _get(raw, data, 'interactive'))
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, list):
        return {'kind': 'interactive', 'data': payload}
    return None


def _template_components(raw, data, template_data, proforma):
    """
    The Meta component array.

    Laravel scatters this across five different keys depending on which code
    path wrote the row, and ``template_data.components`` is the one the audit
    found DigiCRM was missing (bug C1).
    """
    for candidate in (
        _as_dict(template_data).get('components'),
        raw.get('template_components'),
        data.get('template_components'),
        _as_dict(proforma).get('components'),
        raw.get('template_component_values'),
        data.get('template_component_values'),
        raw.get('submitted_template_components'),
        data.get('submitted_template_components'),
    ):
        if isinstance(candidate, list) and candidate:
            return candidate
    return []


def _build_template(raw, data, template_data, proforma):
    name = _first(
        _get(raw, data, 'template_name'),
        _as_dict(proforma).get('name'),
        _as_dict(template_data).get('name'),
    )
    components = _template_components(raw, data, template_data, proforma)
    if not name and not components:
        return None
    return {
        'name': name,
        'components': components,
        'language': _first(
            _as_dict(proforma).get('language'),
            _get(raw, data, 'template_language'),
            _as_dict(template_data).get('language'),
        ),
    }


def _reply_to(raw, data):
    return _first(
        raw.get('reply_to'),
        raw.get('replied_to_message_uid'),
        raw.get('replied_to_whatsapp_message_logs__uid'),
        data.get('replied_to_message_uid'),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalize_message(raw, tenant_id=None) -> dict:
    """
    Map one Laravel message row onto the pinned envelope.

    Never raises.  Anything unparseable becomes ``type: "unsupported"``.
    """
    if not isinstance(raw, dict):
        return _fallback_envelope(raw)

    try:
        data = _extract_data(raw)
        media_values = _as_dict(_get(raw, data, 'media_values'))
        other_data = _as_dict(_get(raw, data, 'other_message_data'))
        interaction = _as_dict(_get(raw, data, 'interaction_message_data'))
        template_data = _as_dict(_get(raw, data, 'template_data'))
        proforma = _as_dict(_get(raw, data, 'template_proforma'))
        template_bits = bool(
            template_data or proforma
            or _get(raw, data, 'template_name')
            or _get(raw, data, 'template_components')
            or _get(raw, data, 'template_component_values')
        )

        direction = _direction(raw)
        msg_type = _derive_type(raw, data, media_values, other_data, interaction, template_bits)

        envelope = {
            'id': str(_first(raw.get('_uid'), raw.get('id'), raw.get('uid')) or ''),
            'wamid': _first(raw.get('wamid'), raw.get('wa_id'), data.get('wa_message_id')),
            'direction': direction,
            'type': msg_type,
            'status': _status(raw, direction),
            'timestamp': _iso(_first(
                raw.get('messaged_at'), raw.get('sent_at'),
                raw.get('timestamp'), raw.get('created_at'),
            )),
            'text': _clean_text(_get(raw, data, 'text', 'message', 'message_raw')),
            'media': None,
            'location': None,
            'contacts': None,
            'interactive': None,
            'template': None,
            'reply_to': _reply_to(raw, data),
            'error': _error(raw),
        }

        if msg_type in MEDIA_TYPES:
            envelope['media'] = _build_media(tenant_id, media_values)
            if envelope['media'] and envelope['text'] is None:
                envelope['text'] = envelope['media'].get('caption')
        elif msg_type == 'location':
            envelope['location'] = _build_location(other_data, raw, data)
        elif msg_type == 'contacts':
            envelope['contacts'] = _build_contacts(other_data, raw, data)
        elif msg_type == 'interactive':
            envelope['interactive'] = _build_interactive(interaction, other_data, raw, data)
        elif msg_type == 'template':
            envelope['template'] = _build_template(raw, data, template_data, proforma)
        elif msg_type == 'button':
            envelope['interactive'] = _as_dict(_get(raw, data, 'button')) or None

        # A media block can ride along with any type (e.g. a template with a
        # header image), so attach it whenever one exists and we have not
        # already set it.
        if envelope['media'] is None and media_values and msg_type != UNSUPPORTED:
            envelope['media'] = _build_media(tenant_id, media_values)

        return envelope
    except Exception:
        logger.exception('Failed to normalise WhatsApp message; degrading to unsupported')
        return _fallback_envelope(raw)


def _fallback_envelope(raw) -> dict:
    """Last-resort envelope. Keeps the message visible rather than dropping it."""
    raw_dict = raw if isinstance(raw, dict) else {}
    text = None
    try:
        text = _clean_text(
            raw_dict.get('text') or raw_dict.get('message') or raw_dict.get('message_raw')
        )
    except Exception:
        text = None
    return {
        'id': str(raw_dict.get('_uid') or raw_dict.get('id') or ''),
        'wamid': raw_dict.get('wamid'),
        'direction': 'in' if raw_dict.get('is_incoming_message') else 'out',
        'type': UNSUPPORTED,
        'status': 'delivered' if raw_dict.get('is_incoming_message') else 'sent',
        'timestamp': _iso(raw_dict.get('messaged_at') or raw_dict.get('created_at')),
        'text': text,
        'media': None,
        'location': None,
        'contacts': None,
        'interactive': None,
        'template': None,
        'reply_to': None,
        'error': None,
    }


def normalize_messages(rows, tenant_id=None) -> list:
    """Normalise a list of rows. Rows Laravel sent newest-first are reversed."""
    return [normalize_message(row, tenant_id) for row in _as_list(rows)]


# ---------------------------------------------------------------------------
# Reply window
# ---------------------------------------------------------------------------

def normalize_reply_window(payload) -> dict:
    """
    Collapse Laravel's four different reply-window key names into one shape.

    Laravel emits ``reply_window_expires_at``; both frontends read
    ``window_expires_at`` / ``expires_at``, so the countdown has always been
    null (bug C2).  We emit all three names plus a canonical block, so nothing
    in flight breaks while the frontends converge on ``reply_window``.
    """
    payload = _as_dict(payload)
    expires_at = _iso(_first(
        payload.get('reply_window_expires_at'),
        payload.get('window_expires_at'),
        payload.get('expires_at'),
    ))
    open_value = _first(
        payload.get('reply_window_open'),
        payload.get('is_reply_window_open'),
    )
    if open_value is None:
        is_open = None
    else:
        is_open = bool(open_value) and str(open_value).lower() not in ('0', 'false')

    requires_template = payload.get('requires_template')
    if requires_template is None and is_open is not None:
        requires_template = not is_open

    return {
        'open': is_open,
        'expires_at': expires_at,
        'requires_template': (
            bool(requires_template) if requires_template is not None else None
        ),
        'expires_human': payload.get('reply_window_expires_human'),
    }
