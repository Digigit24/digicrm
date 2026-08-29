"""
Wake-up push for mobile SIP/WebRTC clients.

Why this exists
----------------
A mobile softphone's SIP WebSocket to the SBC does not survive the OS
suspending or killing the app in the background — unlike the browser widget,
which is always foregrounded while a tab is open. Without a way to reach a
backgrounded phone, an inbound call ringing on the tenant's SBC would simply
never surface in the app. `LiveEventWebhookView`'s "ringing" event is the
only server-side signal that a call has started ringing an extension, so it
is also the only place a wake-up push can originate from.

Deliberately never fatal
-------------------------
Exactly like `softphone_service.push_caller_id`: a push failure, a missing
Firebase project, or an unresolvable extension must never break the webhook
response TeleCMI is waiting on. Every failure here is caught and logged, not
raised.

Firebase is optional
---------------------
No Firebase project exists yet for this app (no `firebase-admin` credential,
no service account). Until `FIREBASE_CREDENTIALS_JSON` is set, `_get_app()`
returns None and every call in this module becomes a logged no-op — the
`DeviceToken` rows still accumulate normally, so turning push on later is a
config change, not a data migration.
"""
import logging

from django.conf import settings

logger = logging.getLogger(__name__)

_firebase_app = None
_firebase_init_attempted = False


def _get_app():
    """Lazily initialize the Firebase Admin app; None if unconfigured."""
    global _firebase_app, _firebase_init_attempted
    if _firebase_init_attempted:
        return _firebase_app
    _firebase_init_attempted = True

    credentials_path = getattr(settings, 'FIREBASE_CREDENTIALS_JSON', None)
    if not credentials_path:
        logger.info(
            'FIREBASE_CREDENTIALS_JSON not set — call wake-up pushes are '
            'disabled (DeviceToken rows still accumulate normally).'
        )
        return None

    try:
        import firebase_admin
        from firebase_admin import credentials

        cred = credentials.Certificate(credentials_path)
        _firebase_app = firebase_admin.initialize_app(cred)
    except Exception:  # noqa: BLE001 — push is best-effort, never fatal
        logger.exception('Could not initialize Firebase Admin SDK.')
        _firebase_app = None
    return _firebase_app


def resolve_user_ids_for_extension(tenant_id, extension):
    """
    Map a ringing TeleCMI extension back to the CRM user_id(s) registered to
    it, using the same resolution surfaces `resolve_softphone_auth` reads
    from (per-user agent rows, then calling-profile assignments).

    Returns a list (usually 0 or 1 entries — a shared/default extension with
    no assignment resolves to nobody in particular here; only a personal
    agent row or an explicit assignment identifies a person to wake).
    """
    if not extension:
        return []

    from telephony.models import TeleCMIAgent, TeleCMIProfileAssignment

    user_ids = set(
        TeleCMIAgent.objects.filter(
            tenant_id=tenant_id, telecmi_user_id=extension, is_active=True,
        ).values_list('user_id', flat=True)
    )
    user_ids.update(
        TeleCMIProfileAssignment.objects.filter(
            tenant_id=tenant_id, profile__telecmi_user_id=extension,
        ).values_list('user_id', flat=True)
    )
    return list(user_ids)


def send_call_wake_push(tenant_id, user_ids, data):
    """
    Send a high-priority, data-only FCM message to every device registered
    to any of `user_ids`. `data` values must be strings (FCM requirement) —
    callers should stringify before calling.

    No-ops (with a debug log line) if Firebase isn't configured or no
    matching devices exist — never raises.
    """
    if not user_ids:
        return

    app = _get_app()
    if app is None:
        return

    from telephony.models import DeviceToken

    tokens = list(
        DeviceToken.objects.filter(
            tenant_id=tenant_id, user_id__in=user_ids,
        ).values_list('fcm_token', flat=True)
    )
    if not tokens:
        logger.debug(
            'send_call_wake_push: no DeviceToken rows for tenant=%s users=%s',
            tenant_id, user_ids,
        )
        return

    from firebase_admin import messaging

    message = messaging.MulticastMessage(
        data={str(k): str(v) for k, v in data.items()},
        tokens=tokens,
        android=messaging.AndroidConfig(priority='high'),
        apns=messaging.APNSConfig(
            headers={'apns-priority': '10', 'apns-push-type': 'voip'},
        ),
    )
    try:
        response = messaging.send_each_for_multicast(message, app=app)
        if response.failure_count:
            logger.warning(
                'send_call_wake_push: %d/%d deliveries failed for tenant=%s',
                response.failure_count, len(tokens), tenant_id,
            )
    except Exception:  # noqa: BLE001 — never block the webhook on push delivery
        logger.exception('send_call_wake_push failed for tenant=%s', tenant_id)
