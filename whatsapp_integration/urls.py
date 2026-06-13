from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    WhatsAppVendorConfigViewSet,
    WhatsAppCampaignViewSet,
    WhatsAppSequenceViewSet,
    LeadWhatsAppViewSet,
    WhatsAppWebhookView,
    AgentSendWhatsAppView,
    AgentEnrollSequenceView,
    AgentCreateCampaignView,
    AgentUpdateLeadStatusView,
    AgentLogActivityView,
    AgentActionLogListView,
    WhatsAppTemplatesProxyView,
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

    # Inbound webhooks (called by n8n, not the frontend)
    # POST /api/whatsapp/webhooks/{event_type}/
    #   event_type: message-replied | campaign-completed
    path('webhooks/<str:event_type>/', WhatsAppWebhookView.as_view(), name='whatsapp-webhook'),

    # Agent action endpoints (write-only, all logged)
    path('agent/send/', AgentSendWhatsAppView.as_view(), name='agent-send-whatsapp'),
    path('agent/enroll/', AgentEnrollSequenceView.as_view(), name='agent-enroll-sequence'),
    path('agent/campaign/', AgentCreateCampaignView.as_view(), name='agent-create-campaign'),
    path('agent/update-status/', AgentUpdateLeadStatusView.as_view(), name='agent-update-status'),
    path('agent/log-activity/', AgentLogActivityView.as_view(), name='agent-log-activity'),
    path('agent/logs/', AgentActionLogListView.as_view(), name='agent-action-logs'),
]
