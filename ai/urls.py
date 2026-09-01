from django.urls import path

from .views import AIChatView, AIVoiceTranscribeView

urlpatterns = [
    path("chat/", AIChatView.as_view(), name="ai-chat"),
    path("voice-transcribe/", AIVoiceTranscribeView.as_view(), name="ai-voice-transcribe"),
]
