from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    WhatsAppVendorConfigViewSet,
    WhatsAppCampaignViewSet,
    WhatsAppSequenceViewSet,
    LeadWhatsAppViewSet,
    LeadSequenceEnrollmentUpdateView,
    WhatsAppWebhookView,
    ContactChatByPhoneView,
    ContactSendTextByPhoneView,
    AgentSendWhatsAppView,
    AgentEnrollSequenceView,
    AgentCreateCampaignView,
    AgentUpdateLeadStatusView,
    AgentLogActivityView,
    AgentActionLogListView,
    WhatsAppTemplatesProxyView,
    # AI support endpoints
    AIContextView,
    AITemplatesView,
    AICampaignLaunchView,
    AISequencesView,
)

router = DefaultRouter()
router.register(r'config', WhatsAppVendorConfigViewSet, basename='whatsapp-vendor-config')
router.register(r'campaigns', WhatsAppCampaignViewSet, basename='whatsapp-campaign')
router.register(r'sequences', WhatsAppSequenceViewSet, basename='whatsapp-sequence')
router.register(r'leads', LeadWhatsAppViewSet, basename='whatsapp-lead')

urlpatterns = [
    # Router-generated CRUD + custom actions
    path('', include(router.urls)),

    # Templates proxy (dropdown data for campaign creation)
    path('templates/', WhatsAppTemplatesProxyView.as_view(), name='whatsapp-templates-proxy'),

    # Inbound webhooks
    path('webhooks/<str:event_type>/', WhatsAppWebhookView.as_view(), name='whatsapp-webhook'),

    # Contact (by-phone) endpoints — not scoped to a CRM lead. Used by CeliyoHMS
    # OPD/IPD detail pages to view/reply to a patient's WhatsApp conversation.
    path('contacts/by-phone/chat/', ContactChatByPhoneView.as_view(), name='whatsapp-contact-chat-by-phone'),
    path('contacts/by-phone/send_text/', ContactSendTextByPhoneView.as_view(), name='whatsapp-contact-send-text-by-phone'),

    # Enrollment update (pause/resume/cancel)
    path('enrollments/<int:pk>/', LeadSequenceEnrollmentUpdateView.as_view(), name='enrollment-update'),

    # Agent action endpoints (write-only, all logged)
    path('agent/send/', AgentSendWhatsAppView.as_view(), name='agent-send-whatsapp'),
    path('agent/enroll/', AgentEnrollSequenceView.as_view(), name='agent-enroll-sequence'),
    path('agent/campaign/', AgentCreateCampaignView.as_view(), name='agent-create-campaign'),
    path('agent/update-status/', AgentUpdateLeadStatusView.as_view(), name='agent-update-status'),
    path('agent/log-activity/', AgentLogActivityView.as_view(), name='agent-log-activity'),
    path('agent/logs/', AgentActionLogListView.as_view(), name='agent-action-logs'),

    # AI support endpoints — dynamic resource discovery, no hardcoded UUIDs needed
    # GET  /api/whatsapp/ai/context/          → templates + sequences + statuses + lead groups
    # GET  /api/whatsapp/ai/templates/        → templates with uid, name, category, body
    # POST /api/whatsapp/ai/campaign/launch/  → create & launch campaign in one step
    # GET  /api/whatsapp/ai/sequences/        → active sequences with steps
    path('ai/context/', AIContextView.as_view(), name='ai-context'),
    path('ai/templates/', AITemplatesView.as_view(), name='ai-templates'),
    path('ai/campaign/launch/', AICampaignLaunchView.as_view(), name='ai-campaign-launch'),
    path('ai/sequences/', AISequencesView.as_view(), name='ai-sequences'),
]
