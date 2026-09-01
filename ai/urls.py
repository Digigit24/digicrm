from django.urls import path

from .views import (
    AIChatView,
    AIVoiceTranscribeView,
    AIChatSessionListView,
    AIChatSessionDetailView,
    AIChatSessionMessagesAppendView,
)

# Each URL below used to have 2-3 `path()` entries pointing at separate
# single-method view classes (list vs. create both on "sessions/", detail
# vs. update vs. delete all on "sessions/<id>/"). Django/DRF route by first
# URL match, not by HTTP method, so only the first-registered class of each
# group ever received a request — POST/PATCH/DELETE all 405'd against a
# GET-only view, and the other classes were unreachable dead code. Found by
# actually calling the endpoints, not from reading the code. Fixed by
# merging each URL's methods into one view class (see
# `AIChatSessionListView`/`AIChatSessionDetailView` in views.py) and
# collapsing back down to one `path()` per unique URL, which is what this
# list should have been from the start.
urlpatterns = [
    path("chat/", AIChatView.as_view(), name="ai-chat"),
    path("voice-transcribe/", AIVoiceTranscribeView.as_view(), name="ai-voice-transcribe"),
    # Session persistence endpoints
    path("sessions/", AIChatSessionListView.as_view(), name="ai-session-list"),
    path("sessions/<int:id>/", AIChatSessionDetailView.as_view(), name="ai-session-detail"),
    path("sessions/<int:id>/messages/", AIChatSessionMessagesAppendView.as_view(), name="ai-session-messages-append"),
]
