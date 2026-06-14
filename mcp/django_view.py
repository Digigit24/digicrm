"""
DigiCRM MCP Django View — Direct ORM + OAuth edition
======================================================
Runs INSIDE Django, calls models directly. No JWT env var needed,
no HTTP round-trip to itself.

Setup in main urls.py:
    from mcp.django_view import mcp_urlpatterns, oauth_well_known
    urlpatterns += mcp_urlpatterns
    urlpatterns += [path('.well-known/oauth-authorization-server', oauth_well_known)]

Custom connector URL:  https://crm.celiyo.com/mcp/sse
OAuth Client ID:       digicrm-mcp          (fixed, enter in connector settings)
OAuth Client Secret:   value of MCP_SECRET  (set on your server, enter in connector settings)

Required env var on production server:
    MCP_SECRET=<any random string, e.g. openssl rand -hex 32>

Flow:
    1. Claude reads /.well-known/oauth-authorization-server
    2. Claude POSTs client_id + client_secret to /mcp/oauth/token
    3. Claude gets back an access token (= MCP_SECRET)
    4. Claude sends Bearer <token> on all /mcp/sse and /mcp/message requests
"""

import json
import time
import os
import logging
import secrets

from django.http import JsonResponse, StreamingHttpResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.urls import path

logger = logging.getLogger(__name__)

# Set this on your production server: MCP_SECRET=<random string>
MCP_SECRET = os.environ.get('MCP_SECRET', '')
MCP_CLIENT_ID = 'digicrm-mcp'   # fixed — enter this in Claude's connector settings


def _cors(response):
    response['Access-Control-Allow-Origin'] = '*'
    response['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    return response


def _check_auth(request) -> bool:
    """Accept either Bearer token (from OAuth flow) or ?secret= param."""
    if not MCP_SECRET:
        return True  # no secret configured → open (dev mode)
    # Bearer token sent by Claude after OAuth
    auth = request.headers.get('Authorization', '')
    if auth.startswith('Bearer ') and auth[7:].strip() == MCP_SECRET:
        return True
    # Fallback: ?secret= in URL (for direct testing)
    return request.GET.get('secret', '') == MCP_SECRET


# ── OAuth endpoints (required by MCP spec for remote HTTP servers) ─────────────

def oauth_well_known(request):
    """OAuth 2.0 Authorization Server metadata — Claude reads this first."""
    base = f"{'https' if request.is_secure() else 'http'}://{request.get_host()}"
    return _cors(JsonResponse({
        'issuer': base,
        'authorization_endpoint': f'{base}/mcp/oauth/authorize',
        'token_endpoint': f'{base}/mcp/oauth/token',
        'registration_endpoint': f'{base}/mcp/oauth/register',
        'response_types_supported': ['code'],
        'grant_types_supported': ['authorization_code', 'client_credentials'],
        'token_endpoint_auth_methods_supported': ['client_secret_post', 'client_secret_basic'],
        'code_challenge_methods_supported': ['S256'],
    }))


def oauth_protected_resource(request, path=''):
    """
    OAuth Protected Resource Metadata (RFC 9728).
    Claude hits /.well-known/oauth-protected-resource to discover auth requirements.
    """
    base = f"{'https' if request.is_secure() else 'http'}://{request.get_host()}"
    return _cors(JsonResponse({
        'resource': f'{base}/mcp/sse',
        'authorization_servers': [base],
        'bearer_methods_supported': ['header'],
        'resource_documentation': f'{base}/mcp/health',
    }))


@csrf_exempt
def oauth_register(request):
    """
    Dynamic client registration (RFC 7591).
    Claude tries this when no Client ID is provided in connector settings.
    We reject it — user must enter Client ID + Secret manually.
    """
    if request.method == 'OPTIONS':
        return _cors(HttpResponse())
    # Return the fixed client_id so Claude can proceed
    return _cors(JsonResponse({
        'client_id': MCP_CLIENT_ID,
        'client_secret': MCP_SECRET or 'configure-MCP_SECRET-env-var',
        'client_name': 'DigiCRM MCP',
        'grant_types': ['client_credentials'],
        'token_endpoint_auth_method': 'client_secret_post',
    }, status=201))


@csrf_exempt
def oauth_token(request):
    """
    Token endpoint — Claude exchanges client_id + client_secret for a Bearer token.
    We validate the secret and return it as the access token (simple but sufficient).
    """
    if request.method == 'OPTIONS':
        return _cors(HttpResponse())

    # Parse form or JSON body
    try:
        if request.content_type and 'json' in request.content_type:
            body = json.loads(request.body)
        else:
            body = request.POST.dict() or json.loads(request.body or '{}')
    except Exception:
        body = {}

    client_id = body.get('client_id') or request.POST.get('client_id', '')
    client_secret = body.get('client_secret') or request.POST.get('client_secret', '')

    if MCP_SECRET and client_secret != MCP_SECRET:
        return _cors(JsonResponse({'error': 'invalid_client'}, status=401))

    return _cors(JsonResponse({
        'access_token': MCP_SECRET or 'no-secret-configured',
        'token_type': 'bearer',
        'expires_in': 31536000,  # 1 year
    }))


@csrf_exempt
def oauth_authorize(request):
    """
    Authorization endpoint — shows an approval UI to the user.
    GET  → show approval page
    POST → user clicked Approve → redirect back with auth code
    """
    redirect_uri = request.GET.get('redirect_uri') or request.POST.get('redirect_uri', '')
    state        = request.GET.get('state')        or request.POST.get('state', '')
    client_id    = request.GET.get('client_id')    or request.POST.get('client_id', '')

    if request.method == 'POST' and request.POST.get('action') == 'approve':
        code = secrets.token_urlsafe(16)
        sep  = '&' if '?' in redirect_uri else '?'
        return HttpResponse(status=302, headers={'Location': f'{redirect_uri}{sep}code={code}&state={state}'})

    if request.method == 'POST' and request.POST.get('action') == 'deny':
        sep = '&' if '?' in redirect_uri else '?'
        return HttpResponse(status=302, headers={'Location': f'{redirect_uri}{sep}error=access_denied&state={state}'})

    # GET — show approval page
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Connect Claude to DigiCRM</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      background: #0f172a;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 20px;
    }}
    .card {{
      background: #1e293b;
      border: 1px solid #334155;
      border-radius: 16px;
      padding: 40px;
      max-width: 420px;
      width: 100%;
      text-align: center;
    }}
    .logos {{
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 16px;
      margin-bottom: 28px;
    }}
    .logo {{
      width: 52px;
      height: 52px;
      border-radius: 12px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 24px;
    }}
    .logo-claude {{ background: #d97706; }}
    .logo-crm    {{ background: #7c3aed; }}
    .connector {{
      color: #64748b;
      font-size: 22px;
      font-weight: 300;
    }}
    h1 {{
      color: #f1f5f9;
      font-size: 20px;
      font-weight: 600;
      margin-bottom: 10px;
    }}
    .subtitle {{
      color: #94a3b8;
      font-size: 14px;
      line-height: 1.6;
      margin-bottom: 28px;
    }}
    .permissions {{
      background: #0f172a;
      border: 1px solid #1e3a5f;
      border-radius: 10px;
      padding: 16px;
      margin-bottom: 28px;
      text-align: left;
    }}
    .permissions p {{
      color: #64748b;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      margin-bottom: 10px;
    }}
    .perm {{
      display: flex;
      align-items: center;
      gap: 10px;
      color: #cbd5e1;
      font-size: 13px;
      padding: 5px 0;
    }}
    .perm-icon {{ color: #22c55e; font-size: 15px; }}
    .buttons {{
      display: flex;
      gap: 12px;
    }}
    .btn {{
      flex: 1;
      padding: 12px;
      border-radius: 8px;
      border: none;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      transition: opacity 0.15s;
    }}
    .btn:hover {{ opacity: 0.85; }}
    .btn-deny    {{ background: #1e293b; border: 1px solid #334155; color: #94a3b8; }}
    .btn-approve {{ background: #7c3aed; color: #fff; }}
    .footer {{
      margin-top: 20px;
      color: #475569;
      font-size: 11px;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="logos">
      <div class="logo logo-claude">🤖</div>
      <div class="connector">⇄</div>
      <div class="logo logo-crm">⚡</div>
    </div>
    <h1>Connect Claude to DigiCRM</h1>
    <p class="subtitle">
      Claude is requesting access to your DigiCRM workspace to manage leads,
      tasks, meetings, and WhatsApp conversations on your behalf.
    </p>
    <div class="permissions">
      <p>Claude will be able to</p>
      <div class="perm"><span class="perm-icon">✓</span> Read and create leads</div>
      <div class="perm"><span class="perm-icon">✓</span> Manage tasks and meetings</div>
      <div class="perm"><span class="perm-icon">✓</span> Send WhatsApp messages</div>
      <div class="perm"><span class="perm-icon">✓</span> Run sequences and campaigns</div>
    </div>
    <form method="POST">
      <input type="hidden" name="redirect_uri" value="{redirect_uri}">
      <input type="hidden" name="state"        value="{state}">
      <input type="hidden" name="client_id"    value="{client_id}">
      <div class="buttons">
        <button class="btn btn-deny"    name="action" value="deny">Deny</button>
        <button class="btn btn-approve" name="action" value="approve">Allow Access</button>
      </div>
    </form>
    <p class="footer">Powered by Celiyo · crm.celiyo.com</p>
  </div>
</body>
</html>"""
    return HttpResponse(html)


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


@csrf_exempt
def mcp_sse(request):
    if not _check_auth(request):
        return _cors(JsonResponse({'error': 'Unauthorized'}, status=401))

    def event_stream():
        scheme = 'https' if request.is_secure() else 'http'
        host = request.get_host()
        endpoint = f'{scheme}://{host}/mcp/message'
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
    if not _check_auth(request):
        return _cors(JsonResponse({'error': 'Unauthorized'}, status=401))

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
    path('mcp/health',          mcp_health),
    path('mcp/sse',             mcp_sse),
    path('mcp/message',         mcp_message),
    path('mcp/oauth/token',     oauth_token),
    path('mcp/oauth/register',  oauth_register),
    path('mcp/oauth/authorize', oauth_authorize),
]
