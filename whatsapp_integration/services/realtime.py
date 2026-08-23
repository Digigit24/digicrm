"""
WhatsApp realtime grant.

The problem this solves
-----------------------
Until now the browser subscribed to the tenant's Pusher private channel by
POSTing to Laravel's ``/api/broadcasting/auth`` with the **vendor API access
token** — a long-lived, tenant-wide credential that was kept in
``localStorage``.  That token authorises *every* vendor-scoped Laravel route
(send message, dump contacts, delete templates...), so putting it in a browser
is equivalent to handing every CRM user the tenant's WhatsApp root key.  It
also made a mobile app impossible: there is nowhere safe to keep it.

What we do instead
------------------
Laravel broadcasts ``VendorChannelBroadcast`` on the Pusher private channel
``private-vendor-channel.{vendorUid}``.  Pusher's private-channel protocol is
just an HMAC:

    auth = "{key}:" + HMAC_SHA256(secret, "{socket_id}:{channel_name}")

The signature is bound to **one channel** and **one socket id**, and it is
useless for anything else — it is not a bearer token for the Pusher HTTP API,
it cannot publish, and it cannot be replayed onto another channel or another
websocket connection.  That is exactly the "short-lived, per-user,
single-channel credential" we want.

DigiCRM holds the Pusher secret server-side (``settings.PUSHER_SECRET``) and
signs the grant itself.  No round-trip to Laravel, and the vendor API token
never leaves the server.

The channel is derived from ``WhatsAppVendorConfig`` keyed on the JWT tenant.
A client-supplied channel name is only ever *compared* against the derived
one — never trusted.
"""

import hashlib
import hmac
import logging

from django.conf import settings

from whatsapp_integration.models import WhatsAppVendorConfig

logger = logging.getLogger(__name__)

# Must match app/Events/VendorChannelBroadcast.php::broadcastAs().
# Raw pusher-js binds this verbatim; laravel-echo needs a leading dot
# (".VendorChannelBroadcast") to suppress its App\Events\ namespacing.
BROADCAST_EVENT = 'VendorChannelBroadcast'

# Must match VendorChannelBroadcast::broadcastOn() -> PrivateChannel(...),
# which puts "private-" on the wire.
CHANNEL_PREFIX = 'private-vendor-channel.'


class RealtimeNotConfigured(Exception):
    """Raised when the Pusher server credentials are missing."""


class RealtimeGrantDenied(Exception):
    """Raised when the caller asked for a channel that is not theirs."""


def channel_for_vendor(vendor_uid: str) -> str:
    return f'{CHANNEL_PREFIX}{vendor_uid}'


def resolve_vendor_uid(tenant_id) -> str:
    """
    Resolve the Laravel vendor uid for a tenant.

    Deliberately reads ONLY ``WhatsAppVendorConfig``: never a request header,
    never a query param, never the request body.  This is the whole point of
    the endpoint — the caller does not get to say which tenant's realtime
    stream it would like to listen to.
    """
    config = (
        WhatsAppVendorConfig.objects
        .filter(tenant_id=tenant_id, is_active=True)
        .only('vendor_uid')
        .first()
    )
    if config is None or not config.vendor_uid:
        raise RealtimeNotConfigured(
            'No active WhatsApp vendor config for this tenant. '
            'Configure it in Admin -> WhatsApp Vendor Config.'
        )
    return config.vendor_uid


def _credentials():
    key = getattr(settings, 'PUSHER_KEY', '') or ''
    secret = getattr(settings, 'PUSHER_SECRET', '') or ''
    cluster = getattr(settings, 'PUSHER_CLUSTER', '') or ''
    if not (key and secret and cluster):
        raise RealtimeNotConfigured(
            'Pusher is not configured on this server. Set PUSHER_APP_ID, '
            'PUSHER_SECRET (and PUSHER_KEY / PUSHER_CLUSTER if they differ '
            'from the defaults) in the environment.'
        )
    return key, secret, cluster


def sign_channel(channel_name: str, socket_id: str) -> str:
    """
    Return the Pusher private-channel ``auth`` string.

    This is the entire Pusher private-channel protocol:
    https://pusher.com/docs/channels/library_auth_reference/auth-signatures/
    """
    key, secret, _ = _credentials()
    signature = hmac.new(
        secret.encode('utf-8'),
        f'{socket_id}:{channel_name}'.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()
    return f'{key}:{signature}'


# Pusher's own socket_id format: "<int>.<int>". Rejecting anything else stops a
# caller from smuggling a ":" into the signed string and shifting the channel
# boundary (signature-splicing).
def _valid_socket_id(socket_id: str) -> bool:
    if not socket_id or not isinstance(socket_id, str) or len(socket_id) > 64:
        return False
    left, _, right = socket_id.partition('.')
    return bool(left) and bool(right) and left.isdigit() and right.isdigit()


def build_grant(tenant_id, socket_id: str = None, requested_channel: str = None) -> dict:
    """
    Build the realtime grant for a tenant.

    Without ``socket_id`` this returns connection metadata only (public key,
    cluster, channel name, event name) so the client can construct its Pusher
    connection.  With a ``socket_id`` it additionally returns the signed
    ``auth`` for that one socket on that one channel.

    Never returns the vendor API token, the Pusher secret, or the app id.
    """
    vendor_uid = resolve_vendor_uid(tenant_id)
    channel = channel_for_vendor(vendor_uid)
    key, _secret, cluster = _credentials()

    if requested_channel and requested_channel != channel:
        raise RealtimeGrantDenied(
            'Requested channel does not belong to this tenant.'
        )

    grant = {
        'key': key,
        'cluster': cluster,
        'channel': channel,
        'event': BROADCAST_EVENT,
        # Echo/laravel-echo users must bind the dotted form.
        'echo_event': f'.{BROADCAST_EVENT}',
    }

    if socket_id is not None:
        if not _valid_socket_id(socket_id):
            raise RealtimeGrantDenied('Invalid socket_id.')
        grant['auth'] = sign_channel(channel, socket_id)
        grant['socket_id'] = socket_id

    return grant
