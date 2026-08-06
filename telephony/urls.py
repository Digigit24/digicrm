"""
URL configuration for telephony app.

Authenticated endpoints:
  GET/POST   /api/telephony/credentials/              TeleCMI tenant credentials
  GET/POST   /api/telephony/agents/                   Per-user agent credentials
  POST       /api/telephony/agents/refresh-token/     Force token refresh
  POST       /api/telephony/calls/click-to-call/      Initiate a call
  POST       /api/telephony/calls/hangup/             Hang up active call
  GET        /api/telephony/calls/                    List CDR records
  GET        /api/telephony/calls/<id>/               CDR detail
  POST       /api/telephony/calls/sync/               Manual CDR sync
  POST       /api/telephony/calls/add-note/           Add note to a call
  PATCH      /api/telephony/calls/<pk>/outcome/       Set call disposition
  POST       /api/telephony/sms/send/                 Send SMS
  GET        /api/telephony/sms/                      SMS log list
  GET        /api/telephony/caller-ids/               List caller IDs
  POST       /api/telephony/caller-ids/               Set active caller ID
  GET        /api/telephony/break/                    Get break records
  GET        /api/telephony/callbacks/                List callbacks from TeleCMI
  GET        /api/telephony/webrtc-config/            PIOPIY SDK config for browser
  GET        /api/telephony/analytics/                Admin analytics dashboard
  GET        /api/telephony/analytics/daily/          Per-agent per-day stats
  GET/POST   /api/telephony/campaigns/                Campaign list/create
  PATCH/DEL  /api/telephony/campaigns/<pk>/           Campaign detail/update/delete
  POST       /api/telephony/campaigns/<pk>/push-leads/ Push leads to campaign
  POST       /api/telephony/campaigns/<pk>/toggle-active/ Toggle campaign active state

Public webhook endpoints (configure in TeleCMI dashboard):
  POST       /api/telephony/webhook/cdr/?tenant_id=<uuid>    CDR (post-call)
  POST       /api/telephony/webhook/live/?tenant_id=<uuid>   Live events
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from telephony import views

router = DefaultRouter()
router.register(r'credentials', views.TeleCMICredentialViewSet, basename='telephony-credential')
router.register(r'storage-credentials', views.ZataStorageCredentialViewSet, basename='telephony-storage-credential')
router.register(r'agents', views.TeleCMIAgentViewSet, basename='telephony-agent')
router.register(r'calls', views.CallLogViewSet, basename='telephony-call')
router.register(r'sms', views.SMSLogViewSet, basename='telephony-sms')
router.register(r'campaigns', views.CampaignViewSet, basename='telephony-campaign')

app_name = 'telephony'

urlpatterns = [
    # Explicit paths MUST come before router.urls so they are not swallowed by the
    # router's /{pk}/ pattern (which would match e.g. "click-to-call" as a pk).

    # Call control
    path('calls/click-to-call/', views.ClickToCallView.as_view(), name='click-to-call'),
    path('calls/hangup/', views.HangupView.as_view(), name='hangup'),
    path('calls/add-note/', views.AddNoteView.as_view(), name='add-note'),

    # Call outcome (disposition)
    path('calls/<int:pk>/outcome/', views.CallOutcomeView.as_view(), name='call-outcome'),

    # SMS
    path('sms/send/', views.SMSSendView.as_view(), name='sms-send'),

    # Settings
    path('caller-ids/', views.CallerIDView.as_view(), name='caller-ids'),
    path('break/', views.BreakView.as_view(), name='break'),
    path('callbacks/', views.CallbackListView.as_view(), name='callbacks'),
    path('webrtc-config/', views.WebRTCConfigView.as_view(), name='webrtc-config'),

    # Analytics
    path('analytics/', views.AnalyticsDashboardView.as_view(), name='analytics-dashboard'),
    path('analytics/daily/', views.AgentDailyStatsView.as_view(), name='analytics-daily'),

    # Public webhooks
    path('webhook/cdr/', views.CDRWebhookView.as_view(), name='webhook-cdr'),
    path('webhook/live/', views.LiveEventWebhookView.as_view(), name='webhook-live'),

    # Router (ViewSet list/detail/custom-action URLs) — registered last to avoid
    # the pk pattern consuming the explicit paths above.
    path('', include(router.urls)),
]
