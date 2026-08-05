"""
Telephony API views.

All authenticated endpoints follow the same tenant/permission pattern as
the rest of the CRM: tenant_id and user_id come from JWT middleware attributes
(request.tenant_id, request.user_id) set by JWTAuthenticationMiddleware.

Webhook endpoints (CDR, live events) are public but should be protected
by a webhook_secret header check in production.
"""
import logging
import hmac
import hashlib
import json
from datetime import timedelta

from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters

from common.authentication import JWTRequestAuthentication
from common.mixins import TenantViewSetMixin
from common.permissions import HasDigiPermission, is_admin_request
from telephony.models import (
    TeleCMICredential, TeleCMIAgent, CallLog, SMSLog,
    TeleCMICampaign, CallDirectionEnum, SMSStatusEnum,
)
from telephony.serializers import (
    TeleCMICredentialSerializer, TeleCMIAgentSerializer,
    CallLogSerializer, SMSLogSerializer,
    ClickToCallSerializer, HangupSerializer, SMSSendSerializer,
    CallerIDUpdateSerializer, CDRSyncSerializer, AddNoteSerializer,
    CallOutcomeSerializer, TeleCMICampaignSerializer,
    CampaignLeadPushSerializer, CampaignGroupPushSerializer,
)
from telephony.services import telecmi_client as client
from telephony.services.token_service import (
    get_agent_token, invalidate_token, get_tenant_credential, TokenServiceError,
)
from telephony.services.call_log_service import (
    process_cdr_record, sync_cdr_for_agent, set_call_outcome,
)
from telephony.services.analytics_service import (
    get_agent_summary, get_team_summary, get_missed_unattended,
    get_outcome_breakdown, get_agent_daily_stats,
)
from telephony.services.campaign_service import (
    create_campaign_in_telecmi, sync_campaign_to_telecmi,
    delete_campaign_in_telecmi, push_leads_to_campaign_sync,
    push_group_to_campaign_sync,
)
from telephony.services.callback_service import create_callback_task_if_needed
from telephony.services.realtime import publish_live_event

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────

def _tenant_id(request):
    return getattr(request, 'tenant_id', None)


def _user_id(request):
    return getattr(request, 'user_id', None)


def _get_token_or_error(request):
    """Return (token, None) or (None, Response)."""
    try:
        token = get_agent_token(_tenant_id(request), _user_id(request))
        return token, None
    except TokenServiceError as exc:
        return None, Response(
            {'error': str(exc)}, status=status.HTTP_424_FAILED_DEPENDENCY
        )


# ──────────────────────────────────────────────────────────────
# Credential management (one per tenant, admin only)
# ──────────────────────────────────────────────────────────────

class TeleCMICredentialViewSet(TenantViewSetMixin, viewsets.ModelViewSet):
    """
    Manage the TeleCMI account credentials for this tenant.

    There is at most one active credential per tenant.
    POST creates it; PATCH/PUT updates it; DELETE deactivates it.
    The secret is write-only.
    """
    queryset = TeleCMICredential.objects.all()
    serializer_class = TeleCMICredentialSerializer
    authentication_classes = [JWTRequestAuthentication]
    permission_classes = [HasDigiPermission]
    permission_module = 'telephony'
    permission_resource = 'settings'
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']


class TeleCMIAgentViewSet(TenantViewSetMixin, viewsets.ModelViewSet):
    """
    Manage per-user TeleCMI agent credentials.

    Each CRM user who will make/receive calls via this CRM needs one record.
    Password is write-only; it is encrypted before storage.
    """
    queryset = TeleCMIAgent.objects.all()
    serializer_class = TeleCMIAgentSerializer
    authentication_classes = [JWTRequestAuthentication]
    permission_classes = [HasDigiPermission]
    permission_module = 'telephony'
    permission_resource = 'agents'
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        qs = super().get_queryset()
        # Non-admin users only see their own agent record
        user_id = _user_id(self.request)
        if not is_admin_request(self.request):
            qs = qs.filter(user_id=user_id)
        return qs

    @action(detail=False, methods=['post'], url_path='refresh-token')
    def refresh_token(self, request):
        """Force a fresh token fetch for the current user's agent."""
        invalidate_token(_tenant_id(request), _user_id(request))
        token, err = _get_token_or_error(request)
        if err:
            return err
        return Response({'detail': 'Token refreshed successfully.'})


# ──────────────────────────────────────────────────────────────
# Call control
# ──────────────────────────────────────────────────────────────

class ClickToCallView(APIView):
    """
    POST /api/telephony/calls/click-to-call/

    Initiates a Click-To-Call via TeleCMI.
    TeleCMI rings the agent's softphone first, then dials to_number.
    """
    authentication_classes = [JWTRequestAuthentication]
    permission_classes = [HasDigiPermission]
    permission_module = 'telephony'
    permission_resource = 'calls'

    def post(self, request):
        serializer = ClickToCallSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        token, err = _get_token_or_error(request)
        if err:
            return err

        data = serializer.validated_data
        # Merge lead_id into extra_params so TeleCMI webhooks carry it back
        extra_params = dict(data.get('extra_params') or {})
        if data.get('lead_id'):
            extra_params['lead_id'] = str(data['lead_id'])
            extra_params['crm'] = 'true'

        try:
            result = client.click_to_call(
                token=token,
                to_number=data['to_number'],
                caller_id=data.get('caller_id') or None,
                extra_params=extra_params or None,
            )
        except client.TeleCMIError as exc:
            if exc.status_code == 404:
                # Token rejected by TeleCMI — clear cache and surface error
                invalidate_token(_tenant_id(request), _user_id(request))
            return Response({'error': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        return Response(result, status=status.HTTP_200_OK)


class HangupView(APIView):
    """
    POST /api/telephony/calls/hangup/
    Hang up an active call by its TeleCMI cmiuuid.
    """
    authentication_classes = [JWTRequestAuthentication]
    permission_classes = [HasDigiPermission]
    permission_module = 'telephony'
    permission_resource = 'calls'

    def post(self, request):
        serializer = HangupSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        token, err = _get_token_or_error(request)
        if err:
            return err

        try:
            result = client.hangup_call(token, serializer.validated_data['cmiuuid'])
        except client.TeleCMIError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        return Response(result)


# ──────────────────────────────────────────────────────────────
# Call logs
# ──────────────────────────────────────────────────────────────

class CallLogViewSet(TenantViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """
    GET /api/telephony/calls/
    GET /api/telephony/calls/<id>/

    List and retrieve CDR records for this tenant.
    Supports filtering by direction, call_type, lead_id, date range.
    """
    queryset = CallLog.objects.select_related().order_by('-call_time')
    serializer_class = CallLogSerializer
    authentication_classes = [JWTRequestAuthentication]
    permission_classes = [HasDigiPermission]
    permission_module = 'telephony'
    permission_resource = 'calls'
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['direction', 'call_type', 'lead_id', 'agent_user_id']
    ordering_fields = ['call_time', 'duration', 'created_at']
    ordering = ['-call_time']

    @action(detail=False, methods=['post'], url_path='sync')
    def sync(self, request):
        """
        POST /api/telephony/calls/sync/
        Manually trigger a CDR sync from TeleCMI for the current user.
        """
        serializer = CDRSyncSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        result = sync_cdr_for_agent(
            _tenant_id(request),
            _user_id(request),
            hours_back=serializer.validated_data['hours_back'],
        )
        return Response(result)

    @action(detail=True, methods=['get'], url_path='recording')
    def recording(self, request, pk=None):
        """
        GET /api/telephony/calls/<pk>/recording/
        Proxy-streams the call recording audio from TeleCMI.
        Uses tenant app_id + secret — never exposes credentials to frontend.
        """
        from django.http import StreamingHttpResponse
        from integrations.utils.encryption import EncryptionError
        from telephony.services.crypto import decrypt_secret

        call_log = self.get_object()
        if not call_log.recording_file:
            return Response(
                {'error': 'No recording available for this call'},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            credential = get_tenant_credential(_tenant_id(request))
        except TokenServiceError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_424_FAILED_DEPENDENCY)

        try:
            secret = decrypt_secret(credential)
        except EncryptionError as exc:
            logger.error('Failed to decrypt TeleCMI secret for tenant %s: %s', _tenant_id(request), exc)
            # 424, not 500: the server is healthy, the stored credential just
            # can't be read with this environment's ENCRYPTION_KEY (typical on a
            # local box pointed at a database seeded elsewhere). 500 makes this
            # look like a crash and buries the actionable message.
            return Response(
                {
                    'error': 'TeleCMI credentials cannot be decrypted. '
                             'The encryption key may have changed. '
                             'Go to Settings → TeleCMI, re-enter the App Secret, and save.'
                },
                status=status.HTTP_424_FAILED_DEPENDENCY,
            )

        try:
            telecmi_resp = client.stream_recording(credential.app_id, secret, call_log.recording_file)
        except client.TeleCMIError as exc:
            http_status = status.HTTP_404_NOT_FOUND if exc.status_code == 404 else status.HTTP_502_BAD_GATEWAY
            return Response({'error': str(exc)}, status=http_status)

        # stream_recording() has already rejected non-audio bodies, so anything
        # reaching here is real audio. Still normalise the type: TeleCMI sends
        # application/octet-stream for some files and browsers won't decode an
        # octet-stream in <audio>, which is what makes a player sit at 0:00.
        upstream_type = (telecmi_resp.headers.get('Content-Type') or '').split(';')[0].strip().lower()
        if not upstream_type.startswith('audio/'):
            upstream_type = 'audio/mpeg' if str(call_log.recording_file).lower().endswith('.mp3') else 'audio/wav'

        streaming = StreamingHttpResponse(
            telecmi_resp.iter_content(chunk_size=8192),
            content_type=upstream_type,
        )
        streaming['Content-Disposition'] = f'inline; filename="{call_log.recording_file}"'
        # Content-Length is what lets the browser compute a duration up front;
        # without it the scrubber stays at 0:00 even while audio plays.
        if 'Content-Length' in telecmi_resp.headers:
            streaming['Content-Length'] = telecmi_resp.headers['Content-Length']
        streaming['Accept-Ranges'] = 'none'
        streaming['Cache-Control'] = 'private, max-age=300'
        return streaming


# ──────────────────────────────────────────────────────────────
# SMS
# ──────────────────────────────────────────────────────────────

class CallOutcomeView(APIView):
    """Set the disposition and optional note for a call."""
    authentication_classes = [JWTRequestAuthentication]
    permission_classes = [HasDigiPermission]
    permission_module = 'telephony'
    permission_resource = 'calls'

    def patch(self, request, pk):
        serializer = CallOutcomeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            call_log = set_call_outcome(
                pk,
                _tenant_id(request),
                data['outcome'],
                data.get('note', ''),
                _user_id(request),
            )
        except CallLog.DoesNotExist:
            return Response(
                {'detail': 'Call log not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(CallLogSerializer(call_log).data)


def _days_query_param(request, default):
    try:
        return max(1, int(request.query_params.get('days', default)))
    except (TypeError, ValueError):
        return default


class AnalyticsDashboardView(APIView):
    """Return team and per-agent analytics for the requested date window."""
    authentication_classes = [JWTRequestAuthentication]
    permission_classes = [HasDigiPermission]
    permission_module = 'telephony'
    permission_resource = 'analytics'

    def get(self, request):
        days = _days_query_param(request, 30)
        date_to = timezone.localdate()
        date_from = date_to - timedelta(days=days - 1)
        tenant_id = _tenant_id(request)
        return Response({
            'date_from': date_from,
            'date_to': date_to,
            'team_summary': get_team_summary(tenant_id, date_from, date_to),
            'agent_summary': get_agent_summary(tenant_id, date_from, date_to),
            'missed_unattended': get_missed_unattended(tenant_id),
            'outcome_breakdown': get_outcome_breakdown(
                tenant_id, date_from, date_to,
            ),
        })


class AgentDailyStatsView(APIView):
    """Return daily stats, restricted to the current agent for non-admins."""
    authentication_classes = [JWTRequestAuthentication]
    permission_classes = [HasDigiPermission]
    permission_module = 'telephony'
    permission_resource = 'analytics'

    def get(self, request):
        days = _days_query_param(request, 7)
        date_to = timezone.localdate()
        date_from = date_to - timedelta(days=days - 1)
        agent_user_id = None if is_admin_request(request) else _user_id(request)
        return Response({
            'date_from': date_from,
            'date_to': date_to,
            'results': get_agent_daily_stats(
                _tenant_id(request),
                date_from,
                date_to,
                agent_user_id=agent_user_id,
            ),
        })


class CampaignViewSet(TenantViewSetMixin, viewsets.ModelViewSet):
    """Manage tenant campaigns and synchronize them with TeleCMI."""
    queryset = TeleCMICampaign.objects.all()
    serializer_class = TeleCMICampaignSerializer
    authentication_classes = [JWTRequestAuthentication]
    permission_classes = [HasDigiPermission]
    permission_module = 'telephony'
    permission_resource = 'campaigns'
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def perform_create(self, serializer):
        campaign = serializer.save(
            tenant_id=_tenant_id(self.request),
            created_by_id=_user_id(self.request),
        )
        create_campaign_in_telecmi(_tenant_id(self.request), campaign)

    def perform_update(self, serializer):
        campaign = serializer.save()
        sync_campaign_to_telecmi(_tenant_id(self.request), campaign)

    def perform_destroy(self, instance):
        delete_campaign_in_telecmi(_tenant_id(self.request), instance)
        instance.delete()

    @action(detail=True, methods=['post'], url_path='push-leads')
    def push_leads(self, request, pk=None):
        serializer = CampaignLeadPushSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = push_leads_to_campaign_sync(
            _tenant_id(request),
            serializer.validated_data['lead_ids'],
            self.get_object(),
        )
        return Response(result)

    @action(detail=True, methods=['post'], url_path='push-group')
    def push_group(self, request, pk=None):
        """Seed this campaign from a CRM lead group's current members."""
        from crm.models import LeadGroup

        serializer = CampaignGroupPushSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tenant_id = _tenant_id(request)
        group_id = serializer.validated_data['group_id']

        if not LeadGroup.objects.filter(tenant_id=tenant_id, id=group_id).exists():
            return Response(
                {'detail': 'Lead group not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        campaign = self.get_object()
        campaign.source_group_id = group_id
        campaign.save(update_fields=['source_group', 'updated_at'])

        result = push_group_to_campaign_sync(tenant_id, group_id, campaign)
        return Response(result)

    @action(detail=True, methods=['post'], url_path='toggle-active')
    def toggle_active(self, request, pk=None):
        campaign = self.get_object()
        campaign.is_active = not campaign.is_active
        campaign.save(update_fields=['is_active', 'updated_at'])
        sync_campaign_to_telecmi(_tenant_id(request), campaign)
        return Response(self.get_serializer(campaign).data)

class SMSSendView(APIView):
    """
    POST /api/telephony/sms/send/
    Send an SMS via TeleCMI and log it in SMSLog + LeadActivity.
    """
    authentication_classes = [JWTRequestAuthentication]
    permission_classes = [HasDigiPermission]
    permission_module = 'telephony'
    permission_resource = 'sms'

    def post(self, request):
        serializer = SMSSendSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        token, err = _get_token_or_error(request)
        if err:
            return err

        data = serializer.validated_data
        telecmi_response = None
        sms_status = SMSStatusEnum.SENT
        error_message = None

        try:
            telecmi_response = client.send_sms(token, data['to_number'], data['message'])
        except client.TeleCMIError as exc:
            sms_status = SMSStatusEnum.FAILED
            error_message = str(exc)
            logger.warning('SMS send failed to %s: %s', data['to_number'], exc)

        sms_log = SMSLog.objects.create(
            tenant_id=_tenant_id(request),
            to_number=data['to_number'],
            message=data['message'],
            status=sms_status,
            lead_id=data.get('lead_id'),
            sent_by_user_id=_user_id(request),
            telecmi_response=telecmi_response,
            error_message=error_message,
        )

        # Create a CRM Activity for this SMS if linked to a lead
        if data.get('lead_id') and sms_status == SMSStatusEnum.SENT:
            _create_sms_activity(
                tenant_id=_tenant_id(request),
                lead_id=data['lead_id'],
                to_number=data['to_number'],
                message=data['message'],
                user_id=_user_id(request),
            )

        if sms_status == SMSStatusEnum.FAILED:
            return Response(
                {'error': error_message, 'sms_log_id': sms_log.id},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response(SMSLogSerializer(sms_log).data, status=status.HTTP_201_CREATED)


def _create_sms_activity(tenant_id, lead_id, to_number, message, user_id):
    from crm.models import LeadActivity, ActivityTypeEnum
    from django.utils import timezone
    try:
        LeadActivity.objects.create(
            tenant_id=tenant_id,
            lead_id=lead_id,
            type=ActivityTypeEnum.SMS,
            content=f'SMS to {to_number}:\n{message}',
            happened_at=timezone.now(),
            by_user_id=user_id,
            meta={'to_number': to_number, 'source': 'telecmi'},
        )
    except Exception as exc:
        logger.error('Failed to create SMS activity for lead %s: %s', lead_id, exc)


class SMSLogViewSet(TenantViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """
    GET /api/telephony/sms/
    List all outgoing SMS logs for this tenant.
    """
    queryset = SMSLog.objects.order_by('-created_at')
    serializer_class = SMSLogSerializer
    authentication_classes = [JWTRequestAuthentication]
    permission_classes = [HasDigiPermission]
    permission_module = 'telephony'
    permission_resource = 'sms'
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['status', 'lead_id', 'sent_by_user_id']
    ordering_fields = ['created_at']
    ordering = ['-created_at']


# ──────────────────────────────────────────────────────────────
# Caller ID
# ──────────────────────────────────────────────────────────────

class CallerIDView(APIView):
    """
    GET  /api/telephony/caller-ids/  — list available caller IDs
    POST /api/telephony/caller-ids/  — set active caller ID
    """
    authentication_classes = [JWTRequestAuthentication]
    permission_classes = [HasDigiPermission]
    permission_module = 'telephony'
    permission_resource = 'settings'

    def get(self, request):
        token, err = _get_token_or_error(request)
        if err:
            return err
        try:
            result = client.get_caller_ids(token)
        except client.TeleCMIError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response(result)

    def post(self, request):
        serializer = CallerIDUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        token, err = _get_token_or_error(request)
        if err:
            return err
        try:
            result = client.set_caller_id(token, serializer.validated_data['caller_id'])
        except client.TeleCMIError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response(result)


# ──────────────────────────────────────────────────────────────
# Break management
# ──────────────────────────────────────────────────────────────

class BreakView(APIView):
    """
    GET /api/telephony/break/
    Returns break records for the current agent (last 24h by default).
    """
    authentication_classes = [JWTRequestAuthentication]
    permission_classes = [HasDigiPermission]
    permission_module = 'telephony'
    permission_resource = 'agents'

    def get(self, request):
        token, err = _get_token_or_error(request)
        if err:
            return err
        from_date_ms = request.query_params.get('from_date_ms')
        try:
            result = client.get_break_records(
                token,
                from_date_ms=int(from_date_ms) if from_date_ms else None,
            )
        except client.TeleCMIError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response(result)


# ──────────────────────────────────────────────────────────────
# Notes
# ──────────────────────────────────────────────────────────────

class AddNoteView(APIView):
    """
    POST /api/telephony/calls/add-note/
    Adds a note to a call record in TeleCMI.
    """
    authentication_classes = [JWTRequestAuthentication]
    permission_classes = [HasDigiPermission]
    permission_module = 'telephony'
    permission_resource = 'calls'

    def post(self, request):
        serializer = AddNoteSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        token, err = _get_token_or_error(request)
        if err:
            return err
        d = serializer.validated_data
        try:
            result = client.add_note(
                token=token,
                caller_name=d.get('caller_name', ''),
                from_number=d['from_number'],
                timestamp_ms=d['timestamp_ms'],
                message=d['message'],
            )
        except client.TeleCMIError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response(result)


# ──────────────────────────────────────────────────────────────
# Callbacks list
# ──────────────────────────────────────────────────────────────

class CallbackListView(APIView):
    """
    GET /api/telephony/callbacks/
    List callback records from TeleCMI for the current agent.
    Query params: from_ts, to_ts (UTC ms), page, limit.
    """
    authentication_classes = [JWTRequestAuthentication]
    permission_classes = [HasDigiPermission]
    permission_module = 'telephony'
    permission_resource = 'callbacks'

    def get(self, request):
        token, err = _get_token_or_error(request)
        if err:
            return err

        import time
        to_ts = int(request.query_params.get('to_ts', int(time.time() * 1000)))
        from_ts = int(request.query_params.get('from_ts', to_ts - 86400000))
        page = int(request.query_params.get('page', 1))
        limit = min(int(request.query_params.get('limit', 10)), 10)

        try:
            result = client.get_callbacks(token, from_ts, to_ts, page=page, limit=limit)
        except client.TeleCMIError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response(result)


# ──────────────────────────────────────────────────────────────
# WebRTC config endpoint (for frontend PIOPIY SDK setup)
# ──────────────────────────────────────────────────────────────

class WebRTCConfigView(APIView):
    """
    GET /api/telephony/webrtc-config/

    Returns the config the frontend PIOPIY SDK needs to call piopiy.login().
    Does NOT expose the password — the frontend should use the TeleCMI
    user_id + token approach, or the superadmin sets up agent credentials.
    """
    authentication_classes = [JWTRequestAuthentication]
    permission_classes = [HasDigiPermission]
    permission_module = 'telephony'
    permission_resource = 'calls'

    def get(self, request):
        try:
            cred = get_tenant_credential(_tenant_id(request))
        except TokenServiceError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_424_FAILED_DEPENDENCY)

        from telephony.models import TeleCMIAgent
        try:
            agent = TeleCMIAgent.objects.get(
                tenant_id=_tenant_id(request),
                user_id=_user_id(request),
                is_active=True,
            )
        except TeleCMIAgent.DoesNotExist:
            return Response(
                {'error': 'No TeleCMI agent configured for your account.'},
                status=status.HTTP_424_FAILED_DEPENDENCY,
            )

        return Response({
            'telecmi_user_id': agent.telecmi_user_id,
            'sbc_host': cred.sbc_host,
            'default_caller_id': cred.default_caller_id,
        })


# ──────────────────────────────────────────────────────────────
# Webhooks (public, CSRF-exempt)
# ──────────────────────────────────────────────────────────────

@method_decorator(csrf_exempt, name='dispatch')
class CDRWebhookView(APIView):
    """
    POST /api/telephony/webhook/cdr/

    Receives TeleCMI CDR webhooks (call detail records after call ends).
    Configure this URL in the TeleCMI dashboard under Settings → Webhooks.

    If a webhook_secret is set on the TeleCMICredential, the request
    must include X-Webhook-Secret header with the matching value.
    """
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        payload = request.data
        logger.debug('CDR webhook received: %s', payload)

        # Identify tenant via tenant_id query param (set as part of webhook URL)
        tenant_id = request.query_params.get('tenant_id')
        if not tenant_id:
            return Response({'error': 'tenant_id query param required'}, status=400)

        # Optional webhook secret verification
        if not _verify_webhook_secret(request, tenant_id):
            return Response({'error': 'Invalid webhook secret'}, status=401)

        direction = _detect_direction(payload)
        log = process_cdr_record(tenant_id, payload, direction, synced_via='webhook')

        if log and log.call_type == 'missed':
            create_callback_task_if_needed(tenant_id, log)

        if log:
            # Final event for this call — the CDR is the only place the
            # recording filename becomes available, so this is what tells
            # the frontend "the recording (if any) is ready to fetch now".
            publish_live_event(tenant_id, 'ended', {
                'cmiuid': log.cmiuid,
                'call_log_id': log.id,
                'lead_id': log.lead_id,
                'direction': log.direction,
                'call_type': log.call_type,
                'duration': log.duration,
                'from': log.from_number,
                'to': log.to_number,
                'has_recording': bool(log.recording_file),
            })

        return Response({'status': 'ok'})


@method_decorator(csrf_exempt, name='dispatch')
class LiveEventWebhookView(APIView):
    """
    POST /api/telephony/webhook/live/

    Receives TeleCMI live call events (ringing, answered, ended-mid-call) and
    republishes them to the frontend via Pusher on channel
    `telephony.<tenant_id>` — see telephony/services/realtime.py and
    sepratecrm src/hooks/useTelephonyLiveEvents.ts.

    TeleCMI's public docs don't fully enumerate this payload's field names,
    so _normalize_live_event() below is deliberately defensive: it tries a
    handful of common shapes and falls back to logging + skipping the
    publish (never guesses wrong and mislabels an event) rather than
    crashing the webhook. Tighten the field list here once you've captured
    a few real payloads from the TeleCMI dashboard's webhook log.

    Configure this URL in the TeleCMI dashboard under Settings → Webhooks.
    """
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        payload = request.data
        tenant_id = request.query_params.get('tenant_id')
        logger.info('Live event webhook (tenant=%s): %s', tenant_id, payload)

        event_name, cmiuid = _normalize_live_event(payload)
        if event_name:
            publish_live_event(tenant_id, event_name, {
                'cmiuid': cmiuid,
                'from': payload.get('from') or payload.get('from_number'),
                'to': payload.get('to') or payload.get('to_number'),
            })
        else:
            logger.warning(
                'Live event webhook (tenant=%s): could not classify payload as '
                'ringing/answered/ended, skipping Pusher publish: %s',
                tenant_id, payload,
            )

        return Response({'status': 'ok'})


def _normalize_live_event(payload: dict):
    """
    Best-effort mapping of a TeleCMI live-event payload to one of
    ('ringing' | 'answered' | 'ended', cmiuid). Returns (None, None) if the
    payload doesn't match a recognized shape — callers must not publish in
    that case.
    """
    if not isinstance(payload, dict):
        return None, None

    cmiuid = payload.get('cmiuid') or payload.get('cmiuuid') or payload.get('uuid')

    # Some TeleCMI live endpoints send an explicit 'event' or 'status' field;
    # others only send a hangup-style payload with no explicit event name.
    raw_event = (payload.get('event') or payload.get('status') or '').strip().lower()

    if raw_event in ('ringing', 'ring', 'incoming', 'dialing'):
        return 'ringing', cmiuid
    if raw_event in ('answered', 'in-progress', 'active'):
        return 'answered', cmiuid
    if raw_event in ('ended', 'hangup', 'completed', 'bye') or 'hangup' in payload:
        return 'ended', cmiuid

    return None, None


def _detect_direction(payload: dict) -> str:
    """
    Infer call direction from CDR payload.
    TeleCMI CDR webhook uses 'call_type': 'inbound' / 'outbound' in some fields.
    Fall back to 'inbound' if unclear.
    """
    ct = payload.get('call_type') or payload.get('type') or ''
    if 'out' in str(ct).lower():
        return 'outbound'
    return 'inbound'


def _verify_webhook_secret(request, tenant_id) -> bool:
    """Return True if no secret is configured or the header matches."""
    try:
        from telephony.models import TeleCMICredential
        cred = TeleCMICredential.objects.filter(
            tenant_id=tenant_id, is_active=True
        ).values('webhook_secret').first()
        if not cred or not cred['webhook_secret']:
            return True
        incoming = request.headers.get('X-Webhook-Secret', '')
        return hmac.compare_digest(incoming, cred['webhook_secret'])
    except Exception:
        return True
