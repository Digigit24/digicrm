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

