import logging

from django.db import transaction
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from common.permissions import JWTAuthentication

from .models import Notification
from .permissions import HasJWTIdentity
from .realtime import get_pusher_client, notification_channel
from .serializers import NotificationSerializer

logger = logging.getLogger(__name__)


class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = NotificationSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [HasJWTIdentity]
    ordering = ['-created_at']

    def get_queryset(self):
        queryset = Notification.objects.filter(
            tenant_id=self.request.tenant_id,
            recipient_user_id=self.request.user_id,
        ).select_related('lead')
        unread = self.request.query_params.get('unread')
        if unread and unread.lower() in ('1', 'true', 'yes'):
            queryset = queryset.filter(read_at__isnull=True)
        return queryset

    @action(detail=False, methods=['get'], url_path='unread-count')
    def unread_count(self, request):
        count = self.get_queryset().filter(read_at__isnull=True).count()
        return Response({'count': count})

    @action(detail=True, methods=['post'], url_path='mark-read')
    def mark_read(self, request, pk=None):
        notification = self.get_object()
        if notification.read_at is None:
            notification.read_at = timezone.now()
            notification.seen_at = notification.seen_at or notification.read_at
            notification.save(update_fields=['read_at', 'seen_at'])
        return Response(self.get_serializer(notification).data)

    @action(detail=False, methods=['post'], url_path='mark-all-read')
    def mark_all_read(self, request):
        now = timezone.now()
        updated = self.get_queryset().filter(read_at__isnull=True).update(
            read_at=now,
            seen_at=now,
        )
        return Response({'updated': updated})

    @action(detail=False, methods=['post'], url_path='mark-seen')
    def mark_seen(self, request):
        now = timezone.now()
        updated = self.get_queryset().filter(seen_at__isnull=True).update(seen_at=now)
        return Response({'updated': updated})


class NotificationRealtimeAuthView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [HasJWTIdentity]

    def post(self, request):
        socket_id = request.data.get('socket_id')
        channel_name = request.data.get('channel_name')
        expected = notification_channel(request.tenant_id, request.user_id)
        if not socket_id or channel_name != expected:
            return Response({'detail': 'Invalid realtime channel.'}, status=status.HTTP_403_FORBIDDEN)

        client = get_pusher_client()
        if client is None:
            return Response({'detail': 'Realtime is not configured.'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response(client.authenticate(channel=channel_name, socket_id=socket_id))


class FirebaseCustomTokenView(APIView):
    """
    POST /api/notifications/firebase-token/

    Mints a Firebase custom token for the requesting CRM user, so the
    Flutter app can silently sign into Firebase (for FCM token association
    and Firestore security rules) without a second, separate credential --
    Firebase Auth never sees a password, only a token this backend vouches
    for using the SAME JWT session the request already carries.

    `uid` is `request.user_id` -- digicrm keeps no local user row of its
    own; this is the stable identifier the JWT middleware already resolves
    from superadmin (a UUID), the same one every other authed view here
    reads via `request.user_id`.

    Reuses `telephony.services.push_service`'s lazy Firebase Admin app
    initializer rather than calling `firebase_admin.initialize_app()`
    again here -- a second, independent init in the same process raises
    `ValueError`, which would break the existing call wake-up push the
    first time both code paths ran.
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [HasJWTIdentity]

    def post(self, request):
        # Local import: keeps this view importable (and the app loadable)
        # even if `telephony` or `firebase_admin` itself is ever missing --
        # matches push_service's own "Firebase is optional" stance.
        from telephony.services.push_service import _get_app

        app = _get_app()
        if app is None:
            return Response(
                {'detail': 'Firebase is not configured on this server.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        from firebase_admin import auth as firebase_auth

        try:
            token = firebase_auth.create_custom_token(str(request.user_id), app=app)
        except Exception:  # noqa: BLE001 -- surface as a clean 502, not a 500 traceback page
            logger.exception('Firebase custom token minting failed for user_id=%s', request.user_id)
            return Response(
                {'detail': 'Could not create a Firebase token.'},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response({'custom_token': token.decode()})

