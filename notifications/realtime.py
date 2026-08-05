import logging

from django.conf import settings

logger = logging.getLogger(__name__)

_client = None


def get_pusher_client():
    global _client
    app_id = getattr(settings, 'PUSHER_APP_ID', '')
    key = getattr(settings, 'PUSHER_KEY', '')
    secret = getattr(settings, 'PUSHER_SECRET', '')
    cluster = getattr(settings, 'PUSHER_CLUSTER', '')
    if not all((app_id, key, secret, cluster)):
        return None
    if _client is None:
        import pusher
        _client = pusher.Pusher(
            app_id=app_id,
            key=key,
            secret=secret,
            cluster=cluster,
            ssl=True,
        )
    return _client


def notification_channel(tenant_id, user_id):
    return f'private-crm-notifications.{tenant_id}.{user_id}'


def publish_notification(notification):
    """Publish after the DB commit. Failure never rolls back durable delivery."""
    client = get_pusher_client()
    if client is None:
        return False
    payload = {
        'id': notification.id,
        'notification_type': notification.notification_type,
        'title': notification.title,
        'body': notification.body,
        'lead': notification.lead_id,
        'lead_name_snapshot': notification.lead_name_snapshot,
        'action_url': notification.action_url,
        'payload': notification.payload,
        'is_read': False,
        'read_at': None,
        'seen_at': None,
        'created_at': notification.created_at.isoformat(),
    }
    try:
        client.trigger(
            notification_channel(notification.tenant_id, notification.recipient_user_id),
            'notification.created',
            payload,
        )
        return True
    except Exception:
        logger.exception('Failed to publish notification %s', notification.id)
        return False

