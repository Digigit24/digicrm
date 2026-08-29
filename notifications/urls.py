from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import FirebaseCustomTokenView, NotificationRealtimeAuthView, NotificationViewSet

router = DefaultRouter()
router.register(r'', NotificationViewSet, basename='notification')

urlpatterns = [
    path('realtime/auth/', NotificationRealtimeAuthView.as_view(), name='notification-realtime-auth'),
    path('firebase-token/', FirebaseCustomTokenView.as_view(), name='notification-firebase-token'),
    path('', include(router.urls)),
]

