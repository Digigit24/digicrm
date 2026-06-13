import logging
from datetime import timedelta

from django.utils import timezone
from drf_spectacular.utils import (
    extend_schema, extend_schema_view,
    OpenApiParameter, OpenApiResponse, OpenApiExample,
)
from rest_framework import viewsets, filters, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from django_filters.rest_framework import DjangoFilterBackend

from common.authentication import JWTRequestAuthentication
from common.mixins import TenantViewSetMixin
from common.permissions import HasCRMPermission, JWTAuthentication
from crm.models import Lead, LeadActivity, LeadGroup
from crm.serializers import LeadActivitySerializer

from .models import (
    WhatsAppVendorConfig, WhatsAppCampaign, WhatsAppSequence,
    WhatsAppSequenceStep, LeadSequenceEnrollment, AgentActionLog,
    CampaignStatusEnum, SequenceEnrollmentStatusEnum,
    AgentActionTypeEnum, AgentActionStatusEnum,
)
from .serializers import (
    WhatsAppVendorConfigSerializer,
    WhatsAppCampaignSerializer, WhatsAppCampaignListSerializer,
    WhatsAppSequenceSerializer, WhatsAppSequenceListSerializer,
    WhatsAppSequenceStepSerializer,
    LeadSequenceEnrollmentSerializer,
    EnrollLeadSerializer, BulkEnrollSerializer,
    AgentSendWhatsAppSerializer, AgentCreateCampaignSerializer,
    AgentUpdateLeadStatusSerializer, AgentLogActivitySerializer,
    AgentActionLogSerializer,
)
from .services.laravel_adapter import LaravelWhatsAppAdapter, LaravelAdapterError

logger = logging.getLogger(__name__)


def _log_agent_action(tenant_id, action_type, payload_in, payload_out=None,
                      triggered_by='claude-agent', status=AgentActionStatusEnum.SUCCESS,
                      error_message=None):
    """Helper: create an AgentActionLog record."""
    try:
        AgentActionLog.objects.create(
            tenant_id=tenant_id,
            action_type=action_type,
            payload_in=payload_in,
            payload_out=payload_out,
            triggered_by=triggered_by,
            status=status,
            error_message=error_message,
        )
    except Exception as e:
        logger.error(f"Failed to write AgentActionLog: {e}")


# ---------------------------------------------------------------------------
# Vendor Config ViewSet
# ---------------------------------------------------------------------------

@extend_schema_view(
    list=extend_schema(description='List WhatsApp vendor configuration for this tenant.'),
    retrieve=extend_schema(description='Get the active WhatsApp vendor configuration.'),
    create=extend_schema(description='Create or update the WhatsApp vendor configuration.'),
    update=extend_schema(description='Update the WhatsApp vendor configuration.'),
    partial_update=extend_schema(description='Partially update the WhatsApp vendor configuration.'),
    destroy=extend_schema(description='Delete the WhatsApp vendor configuration.'),
)
class WhatsAppVendorConfigViewSet(TenantViewSetMixin, viewsets.ModelViewSet):
    """
    Manage the Laravel WhatsApp adapter credentials for this tenant.

    Store the vendor_uid and api_token from your Laravel WhatsApp account here.
    All other WhatsApp endpoints read this config automatically.
    """
    queryset = WhatsAppVendorConfig.objects.all()
    serializer_class = WhatsAppVendorConfigSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [HasCRMPermission]
    permission_resource = 'whatsapp_config'
    http_method_names = ['get', 'post', 'put', 'patch', 'delete', 'head', 'options']


# ---------------------------------------------------------------------------
# Campaign ViewSet
# ---------------------------------------------------------------------------

@extend_schema_view(
    list=extend_schema(
        description='List all WhatsApp campaigns for this tenant.',
        parameters=[
            OpenApiParameter('status', description='Filter by status: DRAFT, SCHEDULED, RUNNING, COMPLETED, FAILED'),
            OpenApiParameter('lead_group', description='Filter by lead group ID'),
        ]
    ),
    retrieve=extend_schema(description='Get campaign detail.'),
    create=extend_schema(description='Create a new WhatsApp campaign (status starts as DRAFT).'),
    update=extend_schema(description='Update a DRAFT campaign.'),
    partial_update=extend_schema(description='Partially update a DRAFT campaign.'),
    destroy=extend_schema(description='Delete a DRAFT campaign only.'),
)
class WhatsAppCampaignViewSet(TenantViewSetMixin, viewsets.ModelViewSet):
    """
    Plan and manage WhatsApp campaigns from DigiCRM.

    Campaigns target a DigiCRM lead group and send a WhatsApp template message.
    Execution happens via the Laravel WhatsApp adapter.

    Workflow:
      1. POST /campaigns/ — create draft
      2. POST /campaigns/{id}/launch/ — submit to Laravel, status → RUNNING
      3. GET /campaigns/{id}/analytics/ — live delivery stats from Laravel
      4. GET /campaigns/{id}/replies/ — who replied (for follow-up segmentation)
    """
    queryset = WhatsAppCampaign.objects.select_related('lead_group')
    authentication_classes = [JWTAuthentication]
    permission_classes = [HasCRMPermission]
    permission_resource = 'whatsapp_campaigns'
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'lead_group']
    search_fields = ['name', 'template_name']
    ordering_fields = ['created_at', 'scheduled_at', 'name']
    ordering = ['-created_at']

    def get_serializer_class(self):
        if self.action == 'list':
            return WhatsAppCampaignListSerializer
        return WhatsAppCampaignSerializer

    def perform_create(self, serializer):
        serializer.save(
            tenant_id=self.request.tenant_id,
            created_by=self.request.user_id,
            status=CampaignStatusEnum.DRAFT,
        )

    def destroy(self, request, *args, **kwargs):
        campaign = self.get_object()
        if campaign.status not in [CampaignStatusEnum.DRAFT, CampaignStatusEnum.FAILED]:
            return Response(
                {'detail': 'Only DRAFT or FAILED campaigns can be deleted.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        return super().destroy(request, *args, **kwargs)

    @extend_schema(
        description=(
            'Launch a DRAFT campaign: fetch leads from the linked lead group, '
            'submit them to the Laravel WhatsApp adapter, and update status to RUNNING.'
        ),
        responses={
            200: OpenApiResponse(description='Campaign launched successfully.'),
            400: OpenApiResponse(description='Campaign is not in DRAFT status or lead group is empty.'),
            503: OpenApiResponse(description='Laravel adapter unavailable.'),
        }
    )
    @action(detail=True, methods=['post'])
    def launch(self, request, pk=None):
        """Submit a DRAFT campaign to the Laravel WhatsApp adapter for execution."""
        campaign = self.get_object()

        if campaign.status != CampaignStatusEnum.DRAFT:
            return Response(
                {'detail': f'Campaign is {campaign.status}, not DRAFT. Cannot launch.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not campaign.lead_group_id:
            return Response({'detail': 'Campaign has no lead group.'}, status=status.HTTP_400_BAD_REQUEST)

        # Build contact list from lead group
        leads = Lead.objects.filter(
            tenant_id=request.tenant_id,
            group_memberships__group_id=campaign.lead_group_id,
        ).values('id', 'name', 'phone')

        if not leads:
            return Response(
                {'detail': 'Lead group is empty. Add leads before launching.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        contacts = [
            {'phone': l['phone'], 'name': l['name'] or l['phone']}
            for l in leads if l.get('phone')
        ]

        if not contacts:
            return Response(
                {'detail': 'No leads with phone numbers found in this group.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        scheduled_iso = campaign.scheduled_at.isoformat() if campaign.scheduled_at else None

        try:
            adapter = LaravelWhatsAppAdapter(tenant_id=request.tenant_id)
            result = adapter.create_campaign(
                name=campaign.name,
                contacts=contacts,
                template_uid=campaign.template_uid,
                template_components=campaign.template_components or [],
                scheduled_at=scheduled_iso,
                digicrm_campaign_id=campaign.id,
            )
        except LaravelAdapterError as e:
            campaign.status = CampaignStatusEnum.FAILED
            campaign.save(update_fields=['status', 'updated_at'])
            return Response(
                {'detail': str(e)},
                status=e.status_code if e.status_code < 600 else 503
            )

        campaign.laravel_campaign_uid = result.get('campaign_uid')
        campaign.laravel_group_uid    = result.get('group_uid')
        campaign.total_contacts       = result.get('total_contacts', len(contacts))
        campaign.status               = CampaignStatusEnum.RUNNING
        campaign.launched_at          = timezone.now()
        campaign.save(update_fields=[
            'laravel_campaign_uid', 'laravel_group_uid',
            'total_contacts', 'status', 'launched_at', 'updated_at'
        ])

        return Response({
            'detail': 'Campaign launched successfully.',
            'campaign_uid': campaign.laravel_campaign_uid,
            'total_contacts': campaign.total_contacts,
        })

    @extend_schema(
        description='Get live delivery analytics for this campaign from the Laravel adapter.',
        responses={200: OpenApiResponse(description='Analytics data.')}
    )
    @action(detail=True, methods=['get'])
    def analytics(self, request, pk=None):
        """Proxy campaign delivery analytics from the Laravel adapter."""
        campaign = self.get_object()
        if not campaign.laravel_campaign_uid:
            return Response(
                {'detail': 'Campaign has not been launched yet. No Laravel campaign UID.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        try:
            adapter = LaravelWhatsAppAdapter(tenant_id=request.tenant_id)
            data = adapter.get_campaign_analytics(campaign.laravel_campaign_uid)
        except LaravelAdapterError as e:
            return Response({'detail': str(e)}, status=503)

        # Update local status to COMPLETED if Laravel reports all done
        if data.get('pending', 1) == 0 and campaign.status == CampaignStatusEnum.RUNNING:
            campaign.status = CampaignStatusEnum.COMPLETED
            campaign.save(update_fields=['status', 'updated_at'])

        return Response(data)

    @extend_schema(
        description='Get list of contacts who replied to this campaign. Use for follow-up segmentation.',
    )
    @action(detail=True, methods=['get'])
    def replies(self, request, pk=None):
        """Get contacts who replied to this campaign."""
        campaign = self.get_object()
        if not campaign.laravel_campaign_uid:
            return Response(
                {'detail': 'Campaign has not been launched yet.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        try:
            adapter  = LaravelWhatsAppAdapter(tenant_id=request.tenant_id)
            page     = int(request.query_params.get('page', 1))
            per_page = int(request.query_params.get('per_page', 50))
            data = adapter.get_campaign_replies(campaign.laravel_campaign_uid, page, per_page)
        except LaravelAdapterError as e:
            return Response({'detail': str(e)}, status=503)
        return Response(data)


# ---------------------------------------------------------------------------
# Sequence ViewSet
# ---------------------------------------------------------------------------

@extend_schema_view(
    list=extend_schema(description='List all follow-up sequences.'),
    retrieve=extend_schema(description='Get sequence detail with steps.'),
    create=extend_schema(description='Create a new follow-up sequence.'),
    update=extend_schema(description='Update a sequence.'),
    partial_update=extend_schema(description='Partially update a sequence.'),
    destroy=extend_schema(description='Delete a sequence (only if no active enrollments).'),
)
class WhatsAppSequenceViewSet(TenantViewSetMixin, viewsets.ModelViewSet):
    """
    Manage WhatsApp follow-up sequences.

    A sequence is an ordered list of template messages sent at defined intervals
    after a lead is enrolled. Example: Day 0 intro → Day 2 follow-up → Day 5
    case study → Day 10 final nudge.

    After creating a sequence and adding steps, enroll leads via:
      POST /api/whatsapp/leads/{lead_id}/enroll/
    or the agent action endpoint:
      POST /api/agent/actions/enroll-sequence/
    """
    queryset = WhatsAppSequence.objects.prefetch_related('steps')
    authentication_classes = [JWTAuthentication]
    permission_classes = [HasCRMPermission]
    permission_resource = 'whatsapp_sequences'
    filter_backends = [filters.SearchFilter, DjangoFilterBackend]
    filterset_fields = ['is_active']
    search_fields = ['name', 'description']
    ordering = ['name']

    def get_serializer_class(self):
        if self.action == 'list':
            return WhatsAppSequenceListSerializer
        return WhatsAppSequenceSerializer

    def perform_create(self, serializer):
        serializer.save(
            tenant_id=self.request.tenant_id,
            created_by=self.request.user_id,
        )

    def destroy(self, request, *args, **kwargs):
        seq = self.get_object()
        active = seq.enrollments.filter(status=SequenceEnrollmentStatusEnum.ACTIVE).count()
        if active > 0:
            return Response(
                {'detail': f'Cannot delete: {active} leads are actively enrolled. Pause or unenroll them first.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        return super().destroy(request, *args, **kwargs)

    @extend_schema(description='List steps for this sequence.')
    @action(detail=True, methods=['get'])
    def steps(self, request, pk=None):
        """List all steps for a sequence."""
        seq = self.get_object()
        steps = seq.steps.order_by('step_number')
        return Response(WhatsAppSequenceStepSerializer(steps, many=True).data)

    @extend_schema(
        description='Add a new step to this sequence.',
        request=WhatsAppSequenceStepSerializer,
        responses={201: WhatsAppSequenceStepSerializer},
    )
    @action(detail=True, methods=['post'], url_path='steps/add')
    def add_step(self, request, pk=None):
        """Add a step to this sequence."""
        seq = self.get_object()
        serializer = WhatsAppSequenceStepSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        step = serializer.save(sequence=seq)
        return Response(WhatsAppSequenceStepSerializer(step).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        description='Update a sequence step.',
        request=WhatsAppSequenceStepSerializer,
        responses={200: WhatsAppSequenceStepSerializer},
    )
    @action(detail=True, methods=['put', 'patch'], url_path='steps/(?P<step_id>[0-9]+)')
    def update_step(self, request, pk=None, step_id=None):
        """Update a step by its ID."""
        seq = self.get_object()
        step = seq.steps.filter(id=step_id).first()
        if not step:
            return Response({'detail': 'Step not found.'}, status=status.HTTP_404_NOT_FOUND)
        serializer = WhatsAppSequenceStepSerializer(
            step, data=request.data, partial=(request.method == 'PATCH')
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(WhatsAppSequenceStepSerializer(step).data)

    @extend_schema(description='Delete a sequence step.')
    @action(detail=True, methods=['delete'], url_path='steps/(?P<step_id>[0-9]+)/delete')
    def delete_step(self, request, pk=None, step_id=None):
        """Delete a step from this sequence."""
        seq = self.get_object()
        step = seq.steps.filter(id=step_id).first()
        if not step:
            return Response({'detail': 'Step not found.'}, status=status.HTTP_404_NOT_FOUND)
        step.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Lead WhatsApp Actions ViewSet
# ---------------------------------------------------------------------------

class LeadWhatsAppViewSet(viewsets.ViewSet):
    """
    WhatsApp actions scoped to a specific DigiCRM lead.

    All endpoints require the lead to belong to the authenticated tenant.
    Write actions (send, enroll) go through the Laravel adapter service.
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [HasCRMPermission]
    permission_resource = 'leads'

    def _get_lead(self, request, lead_id):
        try:
            return Lead.objects.get(id=lead_id, tenant_id=request.tenant_id)
        except Lead.DoesNotExist:
            return None

    @extend_schema(
        description=(
            'Get paginated WhatsApp chat history for a lead by phone number. '
            'Fetched live from the Laravel adapter.'
        ),
        parameters=[
            OpenApiParameter('page', int, description='Page number.'),
            OpenApiParameter('per_page', int, description='Results per page (max 100).'),
        ]
    )
    @action(detail=False, methods=['get'], url_path='(?P<lead_id>[0-9]+)/chat')
    def chat(self, request, lead_id=None):
        """Get WhatsApp chat history for a lead."""
        lead = self._get_lead(request, lead_id)
        if not lead:
            return Response({'detail': 'Lead not found.'}, status=status.HTTP_404_NOT_FOUND)
        if not lead.phone:
            return Response({'detail': 'Lead has no phone number.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            adapter  = LaravelWhatsAppAdapter(tenant_id=request.tenant_id)
            page     = int(request.query_params.get('page', 1))
            per_page = int(request.query_params.get('per_page', 50))
            data = adapter.get_chat_history(lead.phone, page, per_page)
        except LaravelAdapterError as e:
            return Response({'detail': str(e)}, status=503)
        return Response(data)

    @extend_schema(
        description='Send a WhatsApp template message to this lead.',
        request=AgentSendWhatsAppSerializer,
        responses={200: OpenApiResponse(description='Message sent.')}
    )
    @action(detail=False, methods=['post'], url_path='(?P<lead_id>[0-9]+)/send')
    def send(self, request, lead_id=None):
        """Send a WhatsApp template message to a lead."""
        lead = self._get_lead(request, lead_id)
        if not lead:
            return Response({'detail': 'Lead not found.'}, status=status.HTTP_404_NOT_FOUND)
        if not lead.phone:
            return Response({'detail': 'Lead has no phone number.'}, status=status.HTTP_400_BAD_REQUEST)

        serializer = AgentSendWhatsAppSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        try:
            adapter = LaravelWhatsAppAdapter(tenant_id=request.tenant_id)
            result = adapter.send_message(
                phone=lead.phone,
                name=lead.name,
                template_uid=d['template_uid'],
                template_components=d.get('template_components', []),
                digicrm_lead_id=lead.id,
            )
        except LaravelAdapterError as e:
            return Response({'detail': str(e)}, status=503)

        # Log activity on the lead
        if d.get('note'):
            LeadActivity.objects.create(
                tenant_id=request.tenant_id,
                lead=lead,
                type='SMS',
                content=d['note'],
                happened_at=timezone.now(),
                by_user_id=request.user_id,
            )

        # Update lead's last_contacted_at
        lead.last_contacted_at = timezone.now()
        lead.save(update_fields=['last_contacted_at'])

        return Response({'detail': 'Message sent.', **result})

    @extend_schema(
        description='Get active sequence enrollments for a lead.',
        responses={200: LeadSequenceEnrollmentSerializer(many=True)}
    )
    @action(detail=False, methods=['get'], url_path='(?P<lead_id>[0-9]+)/enrollments')
    def enrollments(self, request, lead_id=None):
        """List sequence enrollments for a lead."""
        lead = self._get_lead(request, lead_id)
        if not lead:
            return Response({'detail': 'Lead not found.'}, status=status.HTTP_404_NOT_FOUND)
        enrollments = LeadSequenceEnrollment.objects.filter(
            lead=lead, tenant_id=request.tenant_id
        ).select_related('sequence', 'current_step')
        return Response(LeadSequenceEnrollmentSerializer(enrollments, many=True).data)

    @extend_schema(
        description='Enroll a lead in a WhatsApp follow-up sequence.',
        request=EnrollLeadSerializer,
        responses={
            201: OpenApiResponse(description='Lead enrolled.'),
            400: OpenApiResponse(description='Already enrolled or sequence not found.'),
        }
    )
    @action(detail=False, methods=['post'], url_path='(?P<lead_id>[0-9]+)/enroll')
    def enroll(self, request, lead_id=None):
        """Enroll a lead in a sequence."""
        lead = self._get_lead(request, lead_id)
        if not lead:
            return Response({'detail': 'Lead not found.'}, status=status.HTTP_404_NOT_FOUND)

        serializer = EnrollLeadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            seq = WhatsAppSequence.objects.get(
                id=serializer.validated_data['sequence_id'],
                tenant_id=request.tenant_id,
                is_active=True,
            )
        except WhatsAppSequence.DoesNotExist:
            return Response({'detail': 'Sequence not found or inactive.'}, status=status.HTTP_404_NOT_FOUND)

        first_step = seq.steps.order_by('step_number').first()

        enrollment, created = LeadSequenceEnrollment.objects.get_or_create(
            lead=lead, sequence=seq,
            defaults={
                'tenant_id': request.tenant_id,
                'status': SequenceEnrollmentStatusEnum.ACTIVE,
                'next_step_at': timezone.now() + timedelta(days=first_step.delay_days if first_step else 0),
                'enrolled_by': request.user_id,
            }
        )
        if not created:
            if enrollment.status == SequenceEnrollmentStatusEnum.ACTIVE:
                return Response(
                    {'detail': 'Lead is already actively enrolled in this sequence.'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            # Re-enroll if previously stopped
            enrollment.status = SequenceEnrollmentStatusEnum.ACTIVE
            enrollment.current_step = None
            enrollment.next_step_at = timezone.now() + timedelta(days=first_step.delay_days if first_step else 0)
            enrollment.completed_at = None
            enrollment.stopped_reason = None
            enrollment.save()

        return Response(
            LeadSequenceEnrollmentSerializer(enrollment).data,
            status=status.HTTP_201_CREATED
        )

    @extend_schema(description='Remove a lead from a sequence.')
    @action(detail=False, methods=['delete'], url_path='(?P<lead_id>[0-9]+)/unenroll')
    def unenroll(self, request, lead_id=None):
        """Unenroll a lead from a sequence."""
        lead = self._get_lead(request, lead_id)
        if not lead:
            return Response({'detail': 'Lead not found.'}, status=status.HTTP_404_NOT_FOUND)

        sequence_id = request.data.get('sequence_id')
        qs = LeadSequenceEnrollment.objects.filter(lead=lead, tenant_id=request.tenant_id)
        if sequence_id:
            qs = qs.filter(sequence_id=sequence_id)

        qs.update(
            status=SequenceEnrollmentStatusEnum.OPTED_OUT,
            stopped_reason='manual_unenroll',
            completed_at=timezone.now(),
        )
        return Response({'detail': 'Lead unenrolled from sequence(s).'})


# ---------------------------------------------------------------------------
# Inbound Webhook Views (called by n8n, not the frontend)
# ---------------------------------------------------------------------------

class WhatsAppWebhookView(APIView):
    """
    Inbound webhooks from n8n (which receives events from the Laravel adapter).

    These endpoints update lead state in DigiCRM when WhatsApp events happen.
    They are NOT for the frontend — they are called by n8n automation workflows.

    Authentication: X-Adapter-Secret header matched against WhatsAppVendorConfig.webhook_secret
    """
    authentication_classes = []
    permission_classes = []

    def _verify_secret(self, request, tenant_id):
        """Verify inbound webhook using shared secret."""
        try:
            config = WhatsAppVendorConfig.objects.get(tenant_id=tenant_id, is_active=True)
        except WhatsAppVendorConfig.DoesNotExist:
            return False
        if not config.webhook_secret:
            return True  # No secret configured = open (warn in logs)
        return request.headers.get('X-Adapter-Secret') == config.webhook_secret

    @extend_schema(
        description=(
            'Called by n8n when a WhatsApp reply is received. '
            'Updates lead last_contacted_at, logs activity, and stops active sequences.'
        )
    )
    def post(self, request, event_type):
        data      = request.data
        phone     = data.get('data', {}).get('phone') or data.get('phone')
        tenant_id = data.get('tenant_id') or request.headers.get('X-Tenant-ID')

        if not tenant_id or not phone:
            return Response({'detail': 'tenant_id and phone required.'}, status=400)

        if not self._verify_secret(request, tenant_id):
            return Response({'detail': 'Unauthorized.'}, status=401)

        clean_phone = ''.join(filter(str.isdigit, phone))

        if event_type == 'message-replied':
            # Find lead by phone
            lead = Lead.objects.filter(tenant_id=tenant_id, phone__contains=clean_phone).first()
            if lead:
                lead.last_contacted_at = timezone.now()
                lead.save(update_fields=['last_contacted_at'])

                # Log inbound message as activity
                LeadActivity.objects.create(
                    tenant_id=tenant_id,
                    lead=lead,
                    type='SMS',
                    content=f"WhatsApp reply: {data.get('data', {}).get('message_body', '(media/no text)')}",
                    happened_at=timezone.now(),
                    by_user_id='00000000-0000-0000-0000-000000000000',  # system user
                    meta={'source': 'whatsapp_inbound', 'wamid': data.get('data', {}).get('message_wamid')},
                )

                # Stop active sequences if stop_on_reply is True
                active_enrollments = LeadSequenceEnrollment.objects.filter(
                    lead=lead,
                    tenant_id=tenant_id,
                    status=SequenceEnrollmentStatusEnum.ACTIVE,
                    sequence__stop_on_reply=True,
                ).select_related('sequence')

                for enrollment in active_enrollments:
                    enrollment.status = SequenceEnrollmentStatusEnum.REPLIED
                    enrollment.stopped_reason = 'lead_replied'
                    enrollment.completed_at = timezone.now()
                    enrollment.save(update_fields=['status', 'stopped_reason', 'completed_at', 'updated_at'])

            return Response({'detail': 'ok'})

        elif event_type == 'campaign-completed':
            campaign_uid = data.get('data', {}).get('campaign_uid') or data.get('campaign_uid')
            if campaign_uid:
                WhatsAppCampaign.objects.filter(
                    laravel_campaign_uid=campaign_uid,
                    tenant_id=tenant_id,
                ).update(status=CampaignStatusEnum.COMPLETED)
            return Response({'detail': 'ok'})

        return Response({'detail': f'Unknown event type: {event_type}'}, status=400)


# ---------------------------------------------------------------------------
# Agent Action Endpoints
# ---------------------------------------------------------------------------

class AgentSendWhatsAppView(APIView):
    """
    Agent action: Send a WhatsApp message to a lead.

    This is the ONLY write endpoint the Claude agent uses to send WhatsApp messages.
    All actions are logged in AgentActionLog for full audit trail.
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [HasCRMPermission]

    @extend_schema(
        request=AgentSendWhatsAppSerializer,
        responses={200: OpenApiResponse(description='Message sent and activity logged.')},
        description='Agent action: Send a WhatsApp template message to a lead by ID.'
    )
    def post(self, request):
        serializer = AgentSendWhatsAppSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        try:
            lead = Lead.objects.get(id=d['lead_id'], tenant_id=request.tenant_id)
        except Lead.DoesNotExist:
            return Response({'detail': 'Lead not found.'}, status=404)

        if not lead.phone:
            return Response({'detail': 'Lead has no phone number.'}, status=400)

        try:
            adapter = LaravelWhatsAppAdapter(tenant_id=request.tenant_id)
            result = adapter.send_message(
                phone=lead.phone,
                name=lead.name,
                template_uid=d['template_uid'],
                template_components=d.get('template_components', []),
                digicrm_lead_id=lead.id,
            )
        except LaravelAdapterError as e:
            _log_agent_action(
                request.tenant_id, AgentActionTypeEnum.SEND_WHATSAPP,
                d, error_message=str(e), status=AgentActionStatusEnum.FAILED
            )
            return Response({'detail': str(e)}, status=503)

        lead.last_contacted_at = timezone.now()
        lead.save(update_fields=['last_contacted_at'])

        _log_agent_action(request.tenant_id, AgentActionTypeEnum.SEND_WHATSAPP, d, result)
        return Response({'detail': 'Message sent.', **result})


class AgentEnrollSequenceView(APIView):
    """
    Agent action: Enroll one or more leads in a WhatsApp sequence.
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [HasCRMPermission]

    @extend_schema(
        request=BulkEnrollSerializer,
        responses={200: OpenApiResponse(description='Enrollment results.')},
        description='Agent action: Enroll multiple leads in a WhatsApp follow-up sequence.'
    )
    def post(self, request):
        serializer = BulkEnrollSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        try:
            seq = WhatsAppSequence.objects.get(
                id=d['sequence_id'], tenant_id=request.tenant_id, is_active=True
            )
        except WhatsAppSequence.DoesNotExist:
            return Response({'detail': 'Sequence not found or inactive.'}, status=404)

        first_step   = seq.steps.order_by('step_number').first()
        first_delay  = first_step.delay_days if first_step else 0
        next_step_at = timezone.now() + timedelta(days=first_delay)

        enrolled = []
        skipped  = []

        for lead_id in d['lead_ids']:
            try:
                lead = Lead.objects.get(id=lead_id, tenant_id=request.tenant_id)
            except Lead.DoesNotExist:
                skipped.append({'lead_id': lead_id, 'reason': 'not found'})
                continue

            enrollment, created = LeadSequenceEnrollment.objects.get_or_create(
                lead=lead, sequence=seq,
                defaults={
                    'tenant_id': request.tenant_id,
                    'status': SequenceEnrollmentStatusEnum.ACTIVE,
                    'next_step_at': next_step_at,
                    'enrolled_by': request.user_id,
                }
            )
            if not created and enrollment.status == SequenceEnrollmentStatusEnum.ACTIVE:
                skipped.append({'lead_id': lead_id, 'reason': 'already enrolled'})
            else:
                if not created:
                    enrollment.status = SequenceEnrollmentStatusEnum.ACTIVE
                    enrollment.next_step_at = next_step_at
                    enrollment.current_step = None
                    enrollment.completed_at = None
                    enrollment.stopped_reason = None
                    enrollment.save()
                enrolled.append(lead_id)

        result = {'enrolled': enrolled, 'skipped': skipped, 'sequence': seq.name}
        _log_agent_action(request.tenant_id, AgentActionTypeEnum.ENROLL_SEQUENCE, d, result)
        return Response(result)


class AgentCreateCampaignView(APIView):
    """
    Agent action: Create and launch a WhatsApp campaign from a lead group.
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [HasCRMPermission]

    @extend_schema(
        request=AgentCreateCampaignSerializer,
        responses={201: OpenApiResponse(description='Campaign created and launched.')},
        description='Agent action: Create and launch a WhatsApp campaign targeting a lead group.'
    )
    def post(self, request):
        serializer = AgentCreateCampaignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        try:
            group = LeadGroup.objects.get(id=d['lead_group_id'], tenant_id=request.tenant_id)
        except LeadGroup.DoesNotExist:
            return Response({'detail': 'Lead group not found.'}, status=404)

        # Create campaign record
        campaign = WhatsAppCampaign.objects.create(
            tenant_id=request.tenant_id,
            name=d['name'],
            lead_group=group,
            template_uid=d['template_uid'],
            template_components=d.get('template_components', []),
            scheduled_at=d.get('scheduled_at'),
            notes=d.get('notes', ''),
            status=CampaignStatusEnum.DRAFT,
            created_by=request.user_id,
        )

        # Get leads and launch
        leads = Lead.objects.filter(
            tenant_id=request.tenant_id,
            group_memberships__group_id=group.id,
        ).values('id', 'name', 'phone')

        contacts = [
            {'phone': l['phone'], 'name': l['name'] or l['phone']}
            for l in leads if l.get('phone')
        ]

        if not contacts:
            campaign.status = CampaignStatusEnum.FAILED
            campaign.save(update_fields=['status'])
            _log_agent_action(
                request.tenant_id, AgentActionTypeEnum.CREATE_CAMPAIGN,
                d, error_message='No contacts with phone numbers',
                status=AgentActionStatusEnum.FAILED
            )
            return Response({'detail': 'No leads with phone numbers in this group.'}, status=400)

        try:
            adapter = LaravelWhatsAppAdapter(tenant_id=request.tenant_id)
            scheduled_iso = campaign.scheduled_at.isoformat() if campaign.scheduled_at else None
            result = adapter.create_campaign(
                name=campaign.name,
                contacts=contacts,
                template_uid=campaign.template_uid,
                template_components=campaign.template_components or [],
                scheduled_at=scheduled_iso,
                digicrm_campaign_id=campaign.id,
            )
        except LaravelAdapterError as e:
            campaign.status = CampaignStatusEnum.FAILED
            campaign.save(update_fields=['status'])
            _log_agent_action(
                request.tenant_id, AgentActionTypeEnum.CREATE_CAMPAIGN,
                d, error_message=str(e), status=AgentActionStatusEnum.FAILED
            )
            return Response({'detail': str(e)}, status=503)

        campaign.laravel_campaign_uid = result.get('campaign_uid')
        campaign.laravel_group_uid    = result.get('group_uid')
        campaign.total_contacts       = result.get('total_contacts', len(contacts))
        campaign.status               = CampaignStatusEnum.RUNNING
        campaign.launched_at          = timezone.now()
        campaign.save()

        out = {
            'campaign_id': campaign.id,
            'campaign_uid': campaign.laravel_campaign_uid,
            'total_contacts': campaign.total_contacts,
        }
        _log_agent_action(request.tenant_id, AgentActionTypeEnum.CREATE_CAMPAIGN, d, out)
        return Response(out, status=status.HTTP_201_CREATED)


class AgentUpdateLeadStatusView(APIView):
    """
    Agent action: Move a lead to a different pipeline status.
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [HasCRMPermission]

    @extend_schema(
        request=AgentUpdateLeadStatusSerializer,
        responses={200: OpenApiResponse(description='Lead status updated.')},
        description='Agent action: Update a lead pipeline status.'
    )
    def post(self, request):
        from crm.models import LeadStatus
        serializer = AgentUpdateLeadStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        try:
            lead = Lead.objects.get(id=d['lead_id'], tenant_id=request.tenant_id)
        except Lead.DoesNotExist:
            return Response({'detail': 'Lead not found.'}, status=404)

        try:
            new_status = LeadStatus.objects.get(id=d['status_id'], tenant_id=request.tenant_id)
        except LeadStatus.DoesNotExist:
            return Response({'detail': 'Status not found.'}, status=404)

        old_status_name = lead.status.name if lead.status else None
        lead.status = new_status
        lead.save(update_fields=['status', 'updated_at'])

        if d.get('note'):
            LeadActivity.objects.create(
                tenant_id=request.tenant_id, lead=lead, type='NOTE',
                content=d['note'], happened_at=timezone.now(), by_user_id=request.user_id,
            )

        out = {'lead_id': lead.id, 'old_status': old_status_name, 'new_status': new_status.name}
        _log_agent_action(request.tenant_id, AgentActionTypeEnum.UPDATE_LEAD_STATUS, d, out)
        return Response(out)


class AgentLogActivityView(APIView):
    """
    Agent action: Log a note or activity on a lead.
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [HasCRMPermission]

    @extend_schema(
        request=AgentLogActivitySerializer,
        responses={201: OpenApiResponse(description='Activity logged.')},
        description='Agent action: Log a call, note, email, or other activity on a lead.'
    )
    def post(self, request):
        serializer = AgentLogActivitySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        try:
            lead = Lead.objects.get(id=d['lead_id'], tenant_id=request.tenant_id)
        except Lead.DoesNotExist:
            return Response({'detail': 'Lead not found.'}, status=404)

        activity = LeadActivity.objects.create(
            tenant_id=request.tenant_id,
            lead=lead,
            type=d['activity_type'],
            content=d['content'],
            happened_at=d.get('happened_at') or timezone.now(),
            by_user_id=request.user_id,
            meta={'triggered_by': 'claude-agent'},
        )

        out = {'activity_id': activity.id, 'lead_id': lead.id, 'type': activity.type}
        _log_agent_action(request.tenant_id, AgentActionTypeEnum.LOG_ACTIVITY, d, out)
        return Response(out, status=status.HTTP_201_CREATED)


class AgentActionLogListView(APIView):
    """
    Read the audit log of all agent actions for this tenant.
    Useful for reviewing what the Claude agent has done.
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [HasCRMPermission]

    @extend_schema(
        description='List recent agent action audit logs for this tenant.',
        parameters=[
            OpenApiParameter('action_type', description='Filter by action type.'),
            OpenApiParameter('limit', int, description='Max results (default 50).'),
        ]
    )
    def get(self, request):
        qs = AgentActionLog.objects.filter(tenant_id=request.tenant_id)
        action_type = request.query_params.get('action_type')
        if action_type:
            qs = qs.filter(action_type=action_type)
        limit = min(int(request.query_params.get('limit', 50)), 200)
        qs = qs[:limit]
        return Response(AgentActionLogSerializer(qs, many=True).data)


# ---------------------------------------------------------------------------
# Templates Proxy
# ---------------------------------------------------------------------------

class WhatsAppTemplatesProxyView(APIView):
    """
    Proxy to list available WhatsApp templates from the Laravel adapter.
    Used by the campaign creation UI to populate the template dropdown.
    Results are cached 10 minutes.
    """
    authentication_classes = [JWTAuthentication]
    permission_classes = [HasCRMPermission]

    @extend_schema(description='List available WhatsApp templates from the Laravel adapter.')
    def get(self, request):
        try:
            adapter   = LaravelWhatsAppAdapter(tenant_id=request.tenant_id)
            templates = adapter.get_templates()
        except LaravelAdapterError as e:
            return Response({'detail': str(e)}, status=503)
        return Response({'templates': templates})
