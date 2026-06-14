"""
DigiCRM MCP Django View
"""
import json
import time
import os
try:
    from decouple import config as _cfg
except ImportError:
    _cfg = lambda k, default="": os.environ.get(k, default)
import logging
import secrets

from django.http import JsonResponse, StreamingHttpResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.urls import path

logger = logging.getLogger(__name__)

MCP_SECRET    = _cfg('MCP_SECRET',        '').strip()
TENANT_ID     = _cfg('DIGICRM_TENANT_ID', '').strip()
OWNER_USER_ID = _cfg('MCP_OWNER_USER_ID', '').strip()
MCP_CLIENT_ID = 'digicrm-mcp'


def _cors(response):
    response['Access-Control-Allow-Origin']  = '*'
    response['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    return response


def _check_auth(request) -> bool:
    if not MCP_SECRET:
        return True
    auth = request.headers.get('Authorization', '')
    if auth.startswith('Bearer ') and auth[7:].strip() == MCP_SECRET:
        return True
    return request.GET.get('secret', '') == MCP_SECRET


def oauth_well_known(request):
    base = f"{'https' if request.is_secure() else 'http'}://{request.get_host()}"
    return _cors(JsonResponse({
        'issuer': base,
        'authorization_endpoint': f'{base}/mcp/oauth/authorize',
        'token_endpoint':         f'{base}/mcp/oauth/token',
        'registration_endpoint':  f'{base}/mcp/oauth/register',
        'response_types_supported':              ['code'],
        'grant_types_supported':                 ['authorization_code', 'client_credentials'],
        'token_endpoint_auth_methods_supported': ['client_secret_post', 'client_secret_basic'],
        'code_challenge_methods_supported':      ['S256'],
    }))


def oauth_protected_resource(request, path=''):
    base = f"{'https' if request.is_secure() else 'http'}://{request.get_host()}"
    return _cors(JsonResponse({
        'resource':                f'{base}/mcp/sse',
        'authorization_servers':   [base],
        'bearer_methods_supported':['header'],
    }))


@csrf_exempt
def oauth_register(request):
    if request.method == 'OPTIONS':
        return _cors(HttpResponse())
    return _cors(JsonResponse({
        'client_id':                     MCP_CLIENT_ID,
        'client_secret':                 MCP_SECRET or 'configure-MCP_SECRET-env-var',
        'client_name':                   'DigiCRM MCP',
        'grant_types':                   ['client_credentials'],
        'token_endpoint_auth_method':    'client_secret_post',
    }, status=201))


@csrf_exempt
def oauth_token(request):
    if request.method == 'OPTIONS':
        return _cors(HttpResponse())
    try:
        body = (json.loads(request.body) if request.content_type and 'json' in request.content_type
                else request.POST.dict() or json.loads(request.body or '{}'))
    except Exception:
        body = {}
    client_secret = body.get('client_secret') or request.POST.get('client_secret', '')
    if MCP_SECRET and client_secret != MCP_SECRET:
        return _cors(JsonResponse({'error': 'invalid_client'}, status=401))
    return _cors(JsonResponse({
        'access_token': MCP_SECRET or 'no-secret-configured',
        'token_type':   'bearer',
        'expires_in':   31536000,
    }))


@csrf_exempt
def oauth_authorize(request):
    redirect_uri = request.GET.get('redirect_uri') or request.POST.get('redirect_uri', '')
    state        = request.GET.get('state')        or request.POST.get('state', '')
    client_id    = request.GET.get('client_id')    or request.POST.get('client_id', '')
    if request.method == 'POST':
        action = request.POST.get('action')
        sep    = '&' if '?' in redirect_uri else '?'
        if action == 'approve':
            code = secrets.token_urlsafe(16)
            return HttpResponse(status=302,
                headers={'Location': f'{redirect_uri}{sep}code={code}&state={state}'})
        return HttpResponse(status=302,
            headers={'Location': f'{redirect_uri}{sep}error=access_denied&state={state}'})
    html = (
        '<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Connect Claude to DigiCRM</title>'
        '</head><body><h2>Connect Claude to DigiCRM</h2>'
        '<form method="POST">'
        f'<input type="hidden" name="redirect_uri" value="{redirect_uri}">'
        f'<input type="hidden" name="state" value="{state}">'
        f'<input type="hidden" name="client_id" value="{client_id}">'
        '<button name="action" value="deny">Deny</button>'
        '<button name="action" value="approve">Allow Access</button>'
        '</form></body></html>'
    )
    return HttpResponse(html)


def _dispatch_tool(name: str, args: dict) -> dict:
    from crm.models import Lead, LeadStatus, LeadActivity, LeadGroup, LeadGroupMembership
    from tasks.models import Task
    from meetings.models import Meeting
    from django.utils import timezone
    from django.db.models import Q

    if not TENANT_ID:
        raise RuntimeError('DIGICRM_TENANT_ID env var not set on server')

    logger.info('MCP tool: %s args=%s', name, list(args.keys()))

    # ── list_leads ──────────────────────────────────────────────────────────────
    if name == 'list_leads':
        qs = Lead.objects.filter(tenant_id=TENANT_ID)
        search = args.get('search', '').strip()
        if search:
            qs = qs.filter(
                Q(name__icontains=search) |
                Q(phone__icontains=search) |
                Q(email__icontains=search)
            )
        page      = int(args.get('page', 1))
        page_size = int(args.get('page_size', 20))
        offset    = (page - 1) * page_size
        total     = qs.count()
        leads = list(qs.select_related('status').values(
            'id', 'name', 'phone', 'email',
            'status__name', 'lead_score', 'source', 'assigned_to', 'created_at',
        )[offset:offset + page_size])
        return {'count': total, 'page': page, 'results': leads}

    # ── get_lead ────────────────────────────────────────────────────────────────
    if name == 'get_lead':
        lead = Lead.objects.select_related('status').get(id=args['lead_id'], tenant_id=TENANT_ID)
        return {
            'id':                lead.id,
            'name':              lead.name,
            'phone':             lead.phone,
            'email':             lead.email,
            'company':           getattr(lead, 'company', None),
            'title':             getattr(lead, 'title', None),
            'status':            lead.status.name if lead.status else None,
            'status_id':         lead.status_id,
            'priority':          getattr(lead, 'priority', None),
            'lead_score':        lead.lead_score,
            'source':            lead.source,
            'notes':             lead.notes,
            'assigned_to':       str(lead.assigned_to) if lead.assigned_to else None,
            'created_at':        str(lead.created_at),
            'updated_at':        str(lead.updated_at),
        }

    # ── list_lead_statuses ──────────────────────────────────────────────────────
    if name == 'list_lead_statuses':
        statuses = list(
            LeadStatus.objects.filter(tenant_id=TENANT_ID).values('id', 'name', 'color_hex', 'order_index')
        )
        return {'results': statuses}

    # ── create_lead ─────────────────────────────────────────────────────────────
    if name == 'create_lead':
        if not OWNER_USER_ID:
            raise RuntimeError('MCP_OWNER_USER_ID env var not set')
        lead = Lead.objects.create(
            tenant_id=TENANT_ID,
            owner_user_id=OWNER_USER_ID,
            name=args['name'],
            phone=args['phone'],
            email=args.get('email') or '',
            source=args.get('source') or '',
            lead_score=args.get('lead_score', 0),
            notes=args.get('notes') or '',
            assigned_to=args.get('assigned_to') or None,
        )
        return {'id': lead.id, 'name': lead.name, 'phone': lead.phone}

    # ── update_lead ─────────────────────────────────────────────────────────────
    if name == 'update_lead':
        lead = Lead.objects.get(id=args['lead_id'], tenant_id=TENANT_ID)
        for f in ['name', 'phone', 'email', 'lead_score', 'notes', 'source',
                  'city', 'state', 'country', 'company', 'title']:
            if f in args:
                setattr(lead, f, args[f])
        if 'assigned_to' in args:
            lead.assigned_to = args['assigned_to'] or None
        lead.save()
        return {'id': lead.id, 'updated': True}

    # ── update_lead_status ──────────────────────────────────────────────────────
    if name == 'update_lead_status':
        lead = Lead.objects.get(id=args['lead_id'], tenant_id=TENANT_ID)
        lead.status_id = args['status_id']
        lead.save(update_fields=['status'])
        return {'id': lead.id, 'status_id': args['status_id']}

    # ── bulk_import_leads ───────────────────────────────────────────────────────
    if name == 'bulk_import_leads':
        if not OWNER_USER_ID:
            raise RuntimeError('MCP_OWNER_USER_ID env var not set')
        success, failure, errors = 0, 0, []
        for i, row in enumerate(args.get('leads', [])):
            try:
                Lead.objects.create(
                    tenant_id=TENANT_ID,
                    owner_user_id=OWNER_USER_ID,
                    name=row['name'],
                    phone=row['phone'],
                    email=row.get('email', ''),
                    source=row.get('source', ''),
                    lead_score=row.get('lead_score', 0),
                    notes=row.get('notes', ''),
                    assigned_to=row.get('assigned_to') or None,
                )
                success += 1
            except Exception as exc:
                failure += 1
                errors.append({'row': i, 'name': row.get('name'), 'error': str(exc)})
        return {'success_count': success, 'failure_count': failure, 'errors': errors}

    # ── add_lead_to_group ───────────────────────────────────────────────────────
    if name == 'add_lead_to_group':
        LeadGroupMembership.objects.get_or_create(
            lead_id=args['lead_id'],
            group_id=args['lead_group_id'],
        )
        return {'lead_id': args['lead_id'], 'group_id': args['lead_group_id'], 'added': True}

    # ── create_lead_activity ────────────────────────────────────────────────────
    if name == 'create_lead_activity':
        activity = LeadActivity.objects.create(
            tenant_id=TENANT_ID,
            lead_id=args['lead_id'],
            type=args['type'],
            content=args['content'],
            happened_at=args.get('happened_at') or timezone.now(),
            by_user_id=OWNER_USER_ID or None,
        )
        return {'id': activity.id, 'lead_id': args['lead_id']}

    # ── create_task ─────────────────────────────────────────────────────────────
    if name == 'create_task':
        if not OWNER_USER_ID:
            raise RuntimeError('MCP_OWNER_USER_ID env var not set')
        task = Task.objects.create(
            tenant_id=TENANT_ID,
            owner_user_id=OWNER_USER_ID,
            title=args['title'],
            description=args.get('description') or '',
            lead_id=args.get('lead_id'),
            due_date=args.get('due_date'),
            priority=args.get('priority', 'MEDIUM'),
            assignee_user_id=args.get('assignee_user_id') or None,
        )
        return {'id': task.id, 'title': task.title}

    # ── update_task ─────────────────────────────────────────────────────────────
    if name == 'update_task':
        task = Task.objects.get(id=args['task_id'], tenant_id=TENANT_ID)
        for f in ['title', 'description', 'status', 'priority', 'due_date']:
            if f in args:
                setattr(task, f, args[f])
        task.save()
        return {'id': task.id, 'updated': True}

    # ── create_meeting ──────────────────────────────────────────────────────────
    if name == 'create_meeting':
        if not OWNER_USER_ID:
            raise RuntimeError('MCP_OWNER_USER_ID env var not set')
        meeting = Meeting.objects.create(
            tenant_id=TENANT_ID,
            owner_user_id=OWNER_USER_ID,
            lead_id=args['lead_id'],
            title=args['title'],
            start_at=args['start_time'],
            end_at=args['end_time'],
            notes=args.get('notes') or '',
        )
        return {'id': meeting.id, 'title': meeting.title}

    # ── update_meeting ──────────────────────────────────────────────────────────
    if name == 'update_meeting':
        meeting = Meeting.objects.get(id=args['meeting_id'], tenant_id=TENANT_ID)
        field_map = {'start_time': 'start_at', 'end_time': 'end_at'}
        for f in ['title', 'notes', 'start_time', 'end_time', 'location']:
            if f in args:
                setattr(meeting, field_map.get(f, f), args[f])
        meeting.save()
        return {'id': meeting.id, 'updated': True}


    # ── WhatsApp — shared adapter setup ────────────────────────────────────────
    from whatsapp_integration.models import (
        WhatsAppSequence, WhatsAppSequenceStep,
        LeadSequenceEnrollment, WhatsAppCampaign, AgentActionLog,
        SequenceEnrollmentStatusEnum, CampaignStatusEnum, AgentActionStatusEnum,
    )
    from whatsapp_integration.services.laravel_adapter import LaravelWhatsAppAdapter

    WA_VENDOR_UID = _cfg('WA_VENDOR_UID', '').strip() or None
    WA_API_TOKEN  = _cfg('WA_API_TOKEN',  '').strip() or None
    WA_BASE_URL   = _cfg('WA_BASE_URL',   '').strip() or None

    def _adapter():
        return LaravelWhatsAppAdapter(
            tenant_id=TENANT_ID,
            vendor_uid=WA_VENDOR_UID,
            api_token=WA_API_TOKEN,
            base_url=WA_BASE_URL,
        )

    def _get_lead(lead_id):
        return Lead.objects.get(id=lead_id, tenant_id=TENANT_ID)

    # ── get_lead_chat ───────────────────────────────────────────────────────────
    if name == 'get_lead_chat':
        lead = _get_lead(args['lead_id'])
        return _adapter().get_chat_history(
            lead.phone,
            page=args.get('page', 1),
            per_page=args.get('per_page', 50),
        )

    # ── get_whatsapp_templates ──────────────────────────────────────────────────
    if name == 'get_whatsapp_templates':
        templates = _adapter().get_templates()
        if args.get('search'):
            q = args['search'].lower()
            templates = [t for t in templates if q in str(t.get('name', '')).lower()]
        return {'results': templates}

    # ── get_lead_enrollments ────────────────────────────────────────────────────
    if name == 'get_lead_enrollments':
        rows = list(
            LeadSequenceEnrollment.objects
            .filter(lead_id=args['lead_id'], tenant_id=TENANT_ID)
            .select_related('sequence')
            .values('id', 'sequence__name', 'status', 'enrolled_at', 'next_step_at', 'completed_at')
        )
        return {'results': rows}

    # ── send_whatsapp_template ──────────────────────────────────────────────────
    if name == 'send_whatsapp_template':
        lead = _get_lead(args['lead_id'])
        return _adapter().send_message(
            phone=lead.phone,
            name=lead.name,
            template_uid=args['template_uid'],
            template_components=args.get('template_components', []),
            digicrm_lead_id=lead.id,
        )

    # ── send_whatsapp_text ──────────────────────────────────────────────────────
    if name == 'send_whatsapp_text':
        lead = _get_lead(args['lead_id'])
        return _adapter().send_text_message(
            phone=lead.phone,
            name=lead.name,
            text=args['text'],
            digicrm_lead_id=lead.id,
        )

    # ── agent_send_whatsapp ─────────────────────────────────────────────────────
    if name == 'agent_send_whatsapp':
        lead = _get_lead(args['lead_id'])
        return _adapter().send_message(
            phone=lead.phone,
            name=lead.name,
            template_uid=args['template_uid'],
            template_components=args.get('template_components', []),
            digicrm_lead_id=lead.id,
        )

    # ── assign_lead_chat_user ───────────────────────────────────────────────────
    if name == 'assign_lead_chat_user':
        lead = _get_lead(args['lead_id'])
        return _adapter().assign_chat_user(phone=lead.phone, user_uid=args['user_uid'])

    # ── mark_chat_read ──────────────────────────────────────────────────────────
    if name == 'mark_chat_read':
        lead = _get_lead(args['lead_id'])
        return _adapter().mark_chat_read(phone=lead.phone)

    # ── block_whatsapp_contact ──────────────────────────────────────────────────
    if name == 'block_whatsapp_contact':
        lead = _get_lead(args['lead_id'])
        return _adapter().block_contact(phone=lead.phone, block=args.get('block', True))

    # ── log_agent_activity ──────────────────────────────────────────────────────
    if name == 'log_agent_activity':
        log = AgentActionLog.objects.create(
            tenant_id=TENANT_ID,
            action_type=args['action_type'],
            payload_in={
                'summary': args['summary'],
                'lead_id': args.get('lead_id'),
            },
            triggered_by='claude-agent',
            status=AgentActionStatusEnum.SUCCESS,
        )
        return {'id': log.id, 'logged': True}


    # ── create_sequence ─────────────────────────────────────────────────────────
    if name == 'create_sequence':
        seq = WhatsAppSequence.objects.create(
            tenant_id=TENANT_ID,
            name=args['name'],
            description=args.get('description', ''),
            stop_on_reply=args.get('stop_on_reply', True),
            created_by=OWNER_USER_ID or TENANT_ID,
        )
        return {'id': seq.id, 'name': seq.name}

    # ── add_sequence_step ───────────────────────────────────────────────────────
    if name == 'add_sequence_step':
        step = WhatsAppSequenceStep.objects.create(
            sequence_id=args['sequence_id'],
            step_number=args['step_number'],
            delay_days=args.get('delay_days', 0),
            template_uid=args['template_uid'],
            template_name=args.get('template_name', ''),
            template_variable_mapping=args.get('template_variable_mapping', {}),
        )
        return {'id': step.id, 'step_number': step.step_number}

    # ── update_sequence_step ────────────────────────────────────────────────────
    if name == 'update_sequence_step':
        step = WhatsAppSequenceStep.objects.get(id=args['step_id'])
        for f in ['delay_days', 'template_uid', 'template_name', 'template_variable_mapping']:
            if f in args:
                setattr(step, f, args[f])
        step.save()
        return {'id': step.id, 'updated': True}

    # ── delete_sequence_step ────────────────────────────────────────────────────
    if name == 'delete_sequence_step':
        deleted, _ = WhatsAppSequenceStep.objects.filter(id=args['step_id']).delete()
        return {'deleted': deleted > 0}

    # ── enroll_lead_in_sequence ─────────────────────────────────────────────────
    if name == 'enroll_lead_in_sequence':
        seq = WhatsAppSequence.objects.get(id=args['sequence_id'], tenant_id=TENANT_ID)
        first_step = seq.steps.order_by('step_number').first()
        delay = first_step.delay_days if first_step else 0
        next_step_at = timezone.now() + timezone.timedelta(days=delay)
        enrollment, created = LeadSequenceEnrollment.objects.update_or_create(
            lead_id=args['lead_id'],
            sequence_id=args['sequence_id'],
            defaults={
                'tenant_id':    TENANT_ID,
                'status':       SequenceEnrollmentStatusEnum.ACTIVE,
                'next_step_at': next_step_at,
                'enrolled_by':  OWNER_USER_ID or None,
            },
        )
        return {'id': enrollment.id, 'created': created, 'next_step_at': str(enrollment.next_step_at)}

    # ── pause_enrollment ────────────────────────────────────────────────────────
    if name == 'pause_enrollment':
        e = LeadSequenceEnrollment.objects.get(id=args['enrollment_id'], tenant_id=TENANT_ID)
        e.status = SequenceEnrollmentStatusEnum.PAUSED
        e.save(update_fields=['status', 'updated_at'])
        return {'id': e.id, 'status': e.status}

    # ── resume_enrollment ───────────────────────────────────────────────────────
    if name == 'resume_enrollment':
        e = LeadSequenceEnrollment.objects.get(id=args['enrollment_id'], tenant_id=TENANT_ID)
        e.status = SequenceEnrollmentStatusEnum.ACTIVE
        e.save(update_fields=['status', 'updated_at'])
        return {'id': e.id, 'status': e.status}

    # ── unenroll_lead ───────────────────────────────────────────────────────────
    if name == 'unenroll_lead':
        e = LeadSequenceEnrollment.objects.get(id=args['enrollment_id'], tenant_id=TENANT_ID)
        e.status = SequenceEnrollmentStatusEnum.OPTED_OUT
        e.stopped_reason = 'manual unenroll via MCP'
        e.save(update_fields=['status', 'stopped_reason', 'updated_at'])
        return {'id': e.id, 'status': e.status}

    # ── create_campaign ─────────────────────────────────────────────────────────
    if name == 'create_campaign':
        campaign = WhatsAppCampaign.objects.create(
            tenant_id=TENANT_ID,
            name=args['name'],
            lead_group_id=args['lead_group_id'],
            template_uid=args['template_uid'],
            template_name=args.get('template_name', ''),
            template_components=args.get('template_components', []),
            scheduled_at=args.get('scheduled_at'),
            created_by=OWNER_USER_ID or TENANT_ID,
        )
        return {'id': campaign.id, 'name': campaign.name, 'status': campaign.status}

    # ── launch_campaign ─────────────────────────────────────────────────────────
    if name == 'launch_campaign':
        campaign = WhatsAppCampaign.objects.get(id=args['campaign_id'], tenant_id=TENANT_ID)
        if campaign.status != CampaignStatusEnum.DRAFT:
            raise RuntimeError(
                'Campaign %s not in DRAFT (is %s)' % (campaign.id, campaign.status)
            )
        memberships = LeadGroupMembership.objects.filter(
            group_id=campaign.lead_group_id
        ).select_related('lead')
        contacts = [
            {'phone': m.lead.phone, 'name': m.lead.name, 'digicrm_lead_id': m.lead.id}
            for m in memberships if m.lead.phone
        ]
        if not contacts:
            raise RuntimeError('Lead group has no leads with phone numbers')
        result = _adapter().create_campaign(
            name=campaign.name,
            contacts=contacts,
            template_uid=campaign.template_uid,
            template_components=campaign.template_components or [],
            scheduled_at=(args.get('scheduled_at') or
                          (str(campaign.scheduled_at) if campaign.scheduled_at else None)),
            digicrm_campaign_id=campaign.id,
        )
        campaign.status               = CampaignStatusEnum.RUNNING
        campaign.laravel_campaign_uid = result.get('campaign_uid')
        campaign.laravel_group_uid    = result.get('group_uid')
        campaign.total_contacts       = len(contacts)
        campaign.launched_at          = timezone.now()
        campaign.save()
        return {
            'id': campaign.id,
            'status': campaign.status,
            'total_contacts': campaign.total_contacts,
            'laravel_campaign_uid': campaign.laravel_campaign_uid,
        }

    # ── get_campaign_analytics ──────────────────────────────────────────────────
    if name == 'get_campaign_analytics':
        campaign = WhatsAppCampaign.objects.get(id=args['campaign_id'], tenant_id=TENANT_ID)
        if not campaign.laravel_campaign_uid:
            raise RuntimeError('Campaign not launched yet (no laravel_campaign_uid)')
        return _adapter().get_campaign_analytics(campaign.laravel_campaign_uid)

    raise RuntimeError('Unknown MCP tool: %s' % name)



def _handle_mcp_request(body: dict) -> dict:
    method = body.get('method')
    req_id = body.get('id')

    if method == 'initialize':
        client_proto = body.get('params', {}).get('protocolVersion', '2024-11-05')
        proto = client_proto if client_proto in {'2024-11-05', '2025-03-26'} else '2025-03-26'
        return {'jsonrpc': '2.0', 'id': req_id, 'result': {
            'protocolVersion': proto,
            'capabilities': {'tools': {}},
            'serverInfo': {'name': 'digicrm', 'version': '1.0.0'},
        }}

    if method == 'notifications/initialized':
        return {'jsonrpc': '2.0', 'id': req_id, 'result': {}}

    if method == 'tools/list':
        from mcp.server import TOOLS
        return {'jsonrpc': '2.0', 'id': req_id, 'result': {'tools': TOOLS}}

    if method == 'tools/call':
        params    = body.get('params', {})
        tool_name = params.get('name')
        tool_args = params.get('arguments', {})
        try:
            result = _dispatch_tool(tool_name, tool_args)
            return {'jsonrpc': '2.0', 'id': req_id,
                    'result': {'content': [{'type': 'text', 'text': json.dumps(result, default=str)}]}}
        except Exception as exc:
            logger.exception('Tool %s failed', tool_name)
            return {'jsonrpc': '2.0', 'id': req_id,
                    'error': {'code': -32603, 'message': str(exc)}}

    return {'jsonrpc': '2.0', 'id': req_id,
            'error': {'code': -32601, 'message': 'Unknown method: %s' % method}}


def mcp_health(request):
    return _cors(JsonResponse({'status': 'ok', 'server': 'digicrm-mcp', 'tools': 31}))


@csrf_exempt
def mcp_sse(request):
    if request.method == 'OPTIONS':
        return _cors(HttpResponse())
    if not _check_auth(request):
        logger.warning('MCP auth FAILED method=%s', request.method)
        return _cors(JsonResponse({'error': 'Unauthorized'}, status=401))

    logger.info('MCP SSE: method=%s', request.method)

    if request.method == 'POST':
        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            return _cors(JsonResponse({'error': 'Invalid JSON'}, status=400))
        method = body.get('method', '?')
        logger.info('MCP POST: method=%s id=%s', method, body.get('id'))
        if body.get('id') is None and method.startswith('notifications/'):
            return _cors(HttpResponse(status=202))
        try:
            result = _handle_mcp_request(body)
            logger.info('MCP POST: %s ok', method)
            return _cors(JsonResponse(result, safe=False))
        except Exception as exc:
            logger.exception('MCP POST %s FAILED', method)
            return _cors(JsonResponse({'jsonrpc': '2.0', 'id': body.get('id'),
                'error': {'code': -32603, 'message': str(exc)}}, status=500))

    def event_stream():
        try:
            x_proto = request.META.get('HTTP_X_FORWARDED_PROTO', '')
            scheme  = x_proto if x_proto in ('http', 'https') else (
                'https' if request.is_secure() else 'http')
            endpoint = '%s://%s/mcp/message' % (scheme, request.get_host())
            logger.info('MCP SSE GET: sending endpoint %s', endpoint)
            yield 'event: endpoint\ndata: %s\n\n' % endpoint
            while True:
                yield ': heartbeat\n\n'
                time.sleep(15)
        except GeneratorExit:
            logger.info('MCP SSE: client disconnected')
        except Exception as exc:
            logger.exception('MCP SSE generator: %s', exc)

    response = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
    response['Cache-Control']     = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    response['Connection']        = 'keep-alive'
    return _cors(response)


@csrf_exempt
def mcp_message(request):
    if request.method == 'OPTIONS':
        return _cors(HttpResponse())
    if request.method != 'POST':
        return HttpResponse(status=405)
    if not _check_auth(request):
        return _cors(JsonResponse({'error': 'Unauthorized'}, status=401))
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return _cors(JsonResponse({'error': 'Invalid JSON'}, status=400))
    method = body.get('method', '?')
    logger.info('MCP message: method=%s', method)
    try:
        result = _handle_mcp_request(body)
        return _cors(JsonResponse(result, safe=False))
    except Exception as exc:
        logger.exception('MCP message %s FAILED', method)
        return _cors(JsonResponse({'error': str(exc)}, status=500))


mcp_urlpatterns = [
    path('mcp/health',  mcp_health,  name='mcp_health'),
    path('mcp/sse',     mcp_sse,     name='mcp_sse'),
    path('mcp/message', mcp_message, name='mcp_message'),
    path('mcp/oauth/token',     oauth_token,             name='mcp_oauth_token'),
    path('mcp/oauth/authorize', oauth_authorize,          name='mcp_oauth_authorize'),
    path('mcp/oauth/register',  oauth_register,           name='mcp_oauth_register'),
    path('.well-known/oauth-protected-resource',
         oauth_protected_resource, name='mcp_oauth_protected_resource'),
]
