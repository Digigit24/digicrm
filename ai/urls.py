from django.urls import path

from .views import (
    AIChatView,
    AIVoiceTranscribeView,
    AIChatSessionListView,
    AIChatSessionCreateView,
    AIChatSessionDetailView,
    AIChatSessionUpdateView,
    AIChatSessionDeleteView,
    AIChatSessionMessagesAppendView,
)

urlpatterns = [
    path("chat/", AIChatView.as_view(), name="ai-chat"),
    path("voice-transcribe/", AIVoiceTranscribeView.as_view(), name="ai-voice-transcribe"),
    # Session persistence endpoints
    path("sessions/", AIChatSessionListView.as_view(), name="ai-session-list"),
    path("sessions/", AIChatSessionCreateView.as_view(), name="ai-session-create"),
    path("sessions/<int:id>/", AIChatSessionDetailView.as_view(), name="ai-session-detail"),
    path("sessions/<int:id>/", AIChatSessionUpdateView.as_view(), name="ai-session-update"),
    path("sessions/<int:id>/", AIChatSessionDeleteView.as_view(), name="ai-session-delete"),
    path("sessions/<int:id>/messages/", AIChatSessionMessagesAppendView.as_view(), name="ai-session-messages-append"),
]
