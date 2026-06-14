"""
DigiCRM MCP Django View — Direct ORM edition
=============================================
Runs INSIDE Django, calls models directly. No JWT env var needed,
no HTTP round-trip to itself.

Setup in main urls.py:
    from mcp.django_view import mcp_urlpatterns
    urlpatterns += mcp_urlpatterns

Custom connector URL:
    https://crm.celiyo.com/mcp/sse   (leave OAuth fields blank)

The only env var you need is optional security:
    MCP_SECRET=somerandomstring
Then use: https://crm.celiyo.com/mcp/sse?secret=somerandomstring
"""

import json
import time
import os
import logging

from django.http import JsonResponse, StreamingHttpResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.urls import path

logger = logging.getLogger(__name__)

MCP_SECRET = os.environ.get('MCP_SECRET', '')


def _cors(response):
    response['Access-Control-Allow-Origin'] = '*'
    response['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    return response


def _check_secret(request):
    if not MCP_SECRET:
        return True
    return request.GET.get('secret', '') == MCP_SECRET


# ── Direct ORM tool dispatcher ────────────────────────────────────────────────

def _dispatch_tool(name: str, args: dict) -> dict:
    """
    Handle MCP tool calls using Django ORM directly.
    No HTTP, no JWT — same process as Django itself.
    """
    # Import models here (Django is already set up at this point)
    from crm.models import Lead, LeadStatus, LeadActivity, LeadGroup, LeadGroupMembership
    from tasks.models import Task
    from meetings.models import Meeting
    from django.utils import timezone
    import datetime

    # ── CRM tools ──────────────────────────────────────────────────────────────

    if name == 'list_leads':
        qs = Lead.objects.filter(tenant_id=args.get('tenant_id')) if args.get('tenant_id') else Lead.objects.all()
        search = args.get('search')
        if search:
            qs = qs.filter(name__icontains=search) | qs.filter(phone__icontains=search)
        page = int(args.get('page', 1))
        page_size = int(args.get('page_size', 20))
        offset = (page - 1) * page_size
        total = qs.count()
        leads = list(qs.values('id', 'name', 'phone', 'email', 'status_id', 'lead_score', 'created_at')[offset:offset+page_size])
        return {'count': total, 'page': page, 'results': leads}

    if name == 'get_lead':
        lead = Lead.objects.get(id=args['lead_id'])
        return {'id': lead.id, 'name': lead.name, 'phone': lead.phone,
                'email': lead.email, 'lead_score': lead.lead_score,
                'status_id': lead.status_id, 'notes': lead.notes,
                'created_at': str(lead.created_at)}

    if name == 'create_lead':
        lead = Lead.objects.create(
            name=args['name'],
            phone=args['phone'],
            email=args.get('email', ''),
            source=args.get('source', ''),
            lead_score=args.get('lead_score', 0),
            notes=args.get('notes', ''),
            assigned_to_id=args.get('assigned_to'),
        )
        return {'id': lead.id, 'name': lead.name, 'phone': lead.phone}

    if name == 'update_lead':
        lead = Lead.objects.get(id=args['lead_id'])
        fields = ['name', 'phone', 'email', 'lead_score', 'notes', 'source']
        for f in fields:
            if f in args:
                setattr(lead, f, args[f])
        if 'assigned_to' in args:
            lead.assigned_to_id = args['assigned_to']
        lead.save()
        return {'id': lead.id, 'updated': True}

    if name == 'update_lead_status':
        lead = Lead.objects.get(id=args['lead_id'])
        lead.status_id = args['status_id']
        lead.save(update_fields=['status'])
        return {'id': lead.id, 'status_id': args['status_id']}

    if name == 'list_lead_statuses':
        statuses = list(LeadStatus.objects.values('id', 'name', 'color', 'order'))
        return {'results': statuses}

    if name == 'add_lead_to_group':
        LeadGroupMembership.objects.get_or_create(
            lead_id=args['lead_id'],
            group_id=args['lead_group_id']
        )
        return {'lead_id': args['lead_id'], 'group_id': args['lead_group_id'], 'added': True}

    if name == 'create_lead_activity':
        activity = LeadActivity.objects.create(
            lead_id=args['lead_id'],
            activity_type=args['type'],
            content=args['content'],
            happened_at=args.get('happened_at', timezone.now()),
        )
        return {'id': activity.id, 'lead_id': args['lead_id']}

    # ── Task tools ─────────────────────────────────────────────────────────────

    if name == 'create_task':
        task = Task.objects.create(
            title=args['title'],
            description=args.get('description', ''),
            lead_id=args.get('lead_id'),
            due_date=args.get('due_date'),
            priority=args.get('priority', 'MEDIUM'),
            assignee_id=args.get('assignee_user_id'),
        )
        return {'id': task.id, 'title': task.title}

    if name == 'update_task':
        task = Task.objects.get(id=args['task_id'])
        for f in ['title', 'description', 'status', 'priority', 'due_date']:
            if f in args:
                setattr(task, f, args[f])
        task.save()
        return {'id': task.id, 'updated': True}

    # ── Meeting tools ──────────────────────────────────────────────────────────

    if name == 'create_meeting':
        meeting = Meeting.objects.create(
            lead_id=args['lead_id'],
            title=args['title'],
            start_time=args['start_time'],
            end_time=args['end_time'],
            notes=args.get('notes', ''),
        )
        return {'id': meeting.id, 'title': meeting.title}

    if name == 'update_meeting':
        meeting = Meeting.objects.get(id=args['meeting_id'])
        for f in ['title', 'status', 'start_time', 'end_time', 'notes']:
            if f in args:
                setattr(meeting, f, args[f])
        meeting.save()
        return {'id': meeting.id, 'updated': True}

    # ── WhatsApp & Sequence tools — still go via HTTP to whatsapp_integration ──
    # (because that app internally calls the Laravel adapter)

    from mcp.server import _dispatch as _server_dispatch
    return json.loads(_server_dispatch(name, args))


def _handle_mcp_request(body: dict) -> dict:
    """JSON-RPC 2.0 dispatcher."""
    method = body.get('method')
    req_id = body.get('id')

    if method == 'initialize':
        return {
            'jsonrpc': '2.0', 'id': req_id,
            'result': {
                'protocolVersion': '2024-11-05',
                'capabilities': {'tools': {}},
                'serverInfo': {'name': 'digicrm', 'version': '1.0.0'},
            }
        }

    if method == 'notifications/initialized':
        return {'jsonrpc': '2.0', 'id': req_id, 'result': {}}

    if method == 'tools/list':
        from mcp.server import TOOLS
        return {'jsonrpc': '2.0', 'id': req_id, 'result': {'tools': TOOLS}}

    if method == 'tools/call':
        params = body.get('params', {})
        tool_name = params.get('name')
        tool_args = params.get('arguments', {})
        try:
            result = _dispatch_tool(tool_name, tool_args)
            return {
                'jsonrpc': '2.0', 'id': req_id,
                'result': {'content': [{'type': 'text', 'text': json.dumps(result, default=str)}]}
            }
        except Exception as e:
            logger.exception(f'Tool {tool_name} failed')
            return {
                'jsonrpc': '2.0', 'id': req_id,
                'error': {'code': -32603, 'message': str(e)}
            }

    return {'jsonrpc': '2.0', 'id': req_id, 'error': {'code': -32601, 'message': f'Unknown method: {method}'}}


# ── Django views ──────────────────────────────────────────────────────────────

def mcp_health(request):
    return _cors(JsonResponse({'status': 'ok', 'server': 'digicrm-mcp'}))


def mcp_sse(request):
    if not _check_secret(request):
        return _cors(JsonResponse({'error': 'Forbidden'}, status=403))

    def event_stream():
        scheme = 'https' if request.is_secure() else 'http'
        host = request.get_host()
        secret_param = f'?secret={MCP_SECRET}' if MCP_SECRET else ''
        endpoint = f'{scheme}://{host}/mcp/message{secret_param}'
        yield f'event: endpoint\ndata: {endpoint}\n\n'
        while True:
            yield ': heartbeat\n\n'
            time.sleep(15)

    response = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return _cors(response)


@csrf_exempt
def mcp_message(request):
    if request.method == 'OPTIONS':
        return _cors(HttpResponse())
    if request.method != 'POST':
        return HttpResponse(status=405)
    if not _check_secret(request):
        return _cors(JsonResponse({'error': 'Forbidden'}, status=403))

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return _cors(JsonResponse({'error': 'Invalid JSON'}, status=400))

    try:
        result = _handle_mcp_request(body)
        return _cors(JsonResponse(result, safe=False))
    except Exception as e:
        logger.exception('MCP request failed')
        return _cors(JsonResponse({'error': str(e)}, status=500))


mcp_urlpatterns = [
    path('mcp/health',  mcp_health),
    path('mcp/sse',     mcp_sse),
    path('mcp/message', mcp_message),
]
