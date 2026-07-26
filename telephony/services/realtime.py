"""
Real-time event publisher for telephony live/CDR events.

Publishes to the SAME Pusher app the frontend already subscribes to (see
sepratecrm src/hooks/useTelephonyLiveEvents.ts), on a public channel named
`telephony.<tenant_id>`. Public (not private/presence) channels need no
per-user auth handshake, keeping this backend-agnostic — deliberately
separate from the Laravel WhatsApp backend's private-channel auth flow.

This is a best-effort side channel: if Pusher isn't configured (no
PUSHER_APP_ID/PUSHER_SECRET in settings) or the publish call fails, we log
and continue. Nothing about call handling should ever depend on this
succeeding — the CDR/CallLog record in Postgres is always the source of
truth; Pusher only makes the UI feel instant.
"""
import logging

from django.conf import settings

logger = logging.getLogger(__name__)

_pusher_client = None
_pusher_unconfigured_logged = False


def _get_client():
    """Lazily build a pusher.Pusher client. Returns None if not configured."""
    global _pusher_client, _pusher_unconfigured_logged

    app_id = getattr(settings, 'PUSHER_APP_ID', '')
    key = getattr(settings, 'PUSHER_KEY', '')
    secret = getattr(settings, 'PUSHER_SECRET', '')
    cluster = getattr(settings, 'PUSHER_CLUSTER', '')

    if not (app_id and key and secret and cluster):
        if not _pusher_unconfigured_logged:
            logger.info(
                'Pusher not configured (PUSHER_APP_ID/PUSHER_SECRET missing) — '
                'telephony live events will not be broadcast. Set them in .env '
                'to enable optimistic call-log updates on the frontend.'
            )
            _pusher_unconfigured_logged = True
        return None

    if _pusher_client is None:
        import pusher
        _pusher_client = pusher.Pusher(
            app_id=app_id,
            key=key,
            secret=secret,
            cluster=cluster,
            ssl=True,
        )
    return _pusher_client


def publish_live_event(tenant_id, event_name: str, data: dict) -> bool:
    """
    Publish a telephony event to `telephony.<tenant_id>`.

    event_name should be one of 'ringing' | 'answered' | 'ended' to match
    what useTelephonyLiveEvents.ts on the frontend listens for.

    Returns True if the publish call was made (not a delivery guarantee —
    Pusher is fire-and-forget), False if skipped (not configured / error).
    """
    if not tenant_id:
        return False

    client = _get_client()
    if client is None:
        return False

    channel = f'telephony.{tenant_id}'
    payload = {**data, 'event': event_name}

    try:
        client.trigger(channel, event_name, payload)
        return True
    except Exception:
        logger.exception('Failed to publish telephony live event on %s', channel)
        return False
