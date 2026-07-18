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
    WhatsAppTemplateDetailProxyView,
    WhatsAppTemplateSyncProxyView,
    WhatsAppTemplateSendProxyView,
    WhatsAppTemplateBulkSendProxyView,
    WhatsAppContactsProxyView,
    WhatsAppContactDetailProxyView,
    WhatsAppContactsImportProxyView,
    WhatsAppContactsImportStatusProxyView,
    WhatsAppLabelsProxyView,
    WhatsAppLabelDetailProxyView,
    WhatsAppContactGroupsProxyView,
    WhatsAppContactGroupDetailProxyView,
    WhatsAppContactGroupMembersProxyView,
    WhatsAppFlowsProxyView,
    WhatsAppFlowStatsProxyView,
    WhatsAppFlowDetailProxyView,
    WhatsAppFlowActionProxyView,
    WhatsAppMediaProxyView,
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

    # Templates proxy / CRUD
    path('templates/', WhatsAppTemplatesProxyView.as_view(), name='whatsapp-templates-proxy'),
    path('templates/sync/', WhatsAppTemplateSyncProxyView.as_view(), name='whatsapp-templates-sync-proxy'),
    path('templates/send/', WhatsAppTemplateSendProxyView.as_view(), name='whatsapp-template-send-proxy'),
    path('templates/send/bulk/', WhatsAppTemplateBulkSendProxyView.as_view(), name='whatsapp-template-bulk-send-proxy'),
    path('templates/<str:template_uid>/', WhatsAppTemplateDetailProxyView.as_view(), name='whatsapp-template-detail-proxy'),

    # Inbound webhooks
    path('webhooks/<str:event_type>/', WhatsAppWebhookView.as_view(), name='whatsapp-webhook'),

    # Contact (by-phone) endpoints — not scoped to a CRM lead. Used by CeliyoHMS
    # OPD/IPD detail pages to view/reply to a patient's WhatsApp conversation.
    path('contacts/by-phone/chat/', ContactChatByPhoneView.as_view(), name='whatsapp-contact-chat-by-phone'),
    path('contacts/by-phone/send_text/', ContactSendTextByPhoneView.as_view(), name='whatsapp-contact-send-text-by-phone'),
    path('contacts/import/', WhatsAppContactsImportProxyView.as_view(), name='whatsapp-contacts-import-proxy'),
    path('contacts/import/<str:import_id>/status/', WhatsAppContactsImportStatusProxyView.as_view(), name='whatsapp-contacts-import-status-proxy'),
    path('contacts/', WhatsAppContactsProxyView.as_view(), name='whatsapp-contacts-proxy'),
    path('contacts/<str:contact_uid>/', WhatsAppContactDetailProxyView.as_view(), name='whatsapp-contact-detail-proxy'),

    # Labels and contact groups for the Contacts UI
    path('labels/', WhatsAppLabelsProxyView.as_view(), name='whatsapp-labels-proxy'),
    path('labels/<str:label_uid>/', WhatsAppLabelDetailProxyView.as_view(), name='whatsapp-label-detail-proxy'),
    path('contact-groups/', WhatsAppContactGroupsProxyView.as_view(), name='whatsapp-contact-groups-proxy'),
    path('contact-groups/<str:group_uid>/', WhatsAppContactGroupDetailProxyView.as_view(), name='whatsapp-contact-group-detail-proxy'),
    path('contact-groups/<str:group_uid>/contacts/', WhatsAppContactGroupMembersProxyView.as_view(), name='whatsapp-contact-group-members-proxy'),

    # WABA flows proxy
    path('flows/', WhatsAppFlowsProxyView.as_view(), name='whatsapp-flows-proxy'),
    path('flows/stats/', WhatsAppFlowStatsProxyView.as_view(), name='whatsapp-flow-stats-proxy'),
    path('flows/<str:flow_id>/', WhatsAppFlowDetailProxyView.as_view(), name='whatsapp-flow-detail-proxy'),
    path('flows/<str:flow_id>/<str:action>/', WhatsAppFlowActionProxyView.as_view(), name='whatsapp-flow-action-proxy'),

    # Authenticated proxy for Laravel-hosted media
    path('media/<path:filename>/', WhatsAppMediaProxyView.as_view(), name='whatsapp-media-proxy'),

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
