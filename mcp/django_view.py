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
    """Authenticate an MCP request against MCP_SECRET.

    FAILS CLOSED. If MCP_SECRET is unset/blank the server is misconfigured and
    every request is rejected. Returning True here (the previous behaviour)
    left an unconfigured deploy completely open: the MCP HTTP dispatcher talks
    straight to the ORM with no DRF permission classes, so this shared secret
    is the only access control on the whole surface.
    """
    if not MCP_SECRET:
        logger.error(
            'MCP_SECRET is not configured — refusing all MCP requests. '
            'Set MCP_SECRET in the environment to enable the MCP endpoint.'
        )
        return False
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
    # Fail closed: an unconfigured server must not mint a usable access token.
    if not MCP_SECRET:
        logger.error('MCP_SECRET is not configured — refusing to issue an OAuth token.')
        return _cors(JsonResponse({'error': 'server_error',
                                   'error_description': 'MCP_SECRET is not configured'},
                                  status=503))
    if client_secret != MCP_SECRET:
        return _cors(JsonResponse({'error': 'invalid_client'}, status=401))
    return _cors(JsonResponse({
        'access_token': MCP_SECRET,
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


def _paginate(args: dict, default_size: int = 50, max_size: int = 200):
    """Return (page, page_size, offset) with page_size hard-capped.

    Every list tool must paginate — an unbounded result set will blow the
    model's context window.
    """
    try:
        page = int(args.get('page') or 1)
    except (TypeError, ValueError):
        raise RuntimeError('page must be an integer')
    try:
        page_size = int(args.get('page_size') or default_size)
    except (TypeError, ValueError):
        raise RuntimeError('page_size must be an integer')
    page      = max(page, 1)
    page_size = min(max(page_size, 1), max_size)
    return page, page_size, (page - 1) * page_size


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
        search = (args.get('search') or '').strip()
        if search:
            qs = qs.filter(
                Q(name__icontains=search) |
                Q(phone__icontains=search) |
                Q(email__icontains=search)
            )
        if args.get('assigned_to'):
            qs = qs.filter(assigned_to=args['assigned_to'])
        elif args.get('unassigned'):
            qs = qs.filter(assigned_to__isnull=True)
        # Advanced filters (search_leads_advanced lives here rather than as a
        # separate tool — see _plans/02-mcp-roadmap.md Batch 1).
        status_ids = args.get('status_ids') or None
        if status_ids:
            qs = qs.filter(status_id__in=status_ids)
        elif args.get('status_id'):
            qs = qs.filter(status_id=args['status_id'])
        if args.get('priority'):
            qs = qs.filter(priority=args['priority'])
        if args.get('lead_score_min') is not None:
            qs = qs.filter(lead_score__gte=args['lead_score_min'])
        if args.get('lead_score_max') is not None:
            qs = qs.filter(lead_score__lte=args['lead_score_max'])
        if args.get('created_after'):
            qs = qs.filter(created_at__gte=args['created_after'])
        if args.get('created_before'):
            qs = qs.filter(created_at__lte=args['created_before'])
        if args.get('next_follow_up_before'):
            qs = qs.filter(
                next_follow_up_at__isnull=False,
                next_follow_up_at__lte=args['next_follow_up_before'],
            )
        if args.get('city'):
            qs = qs.filter(city__icontains=args['city'])
        if args.get('lead_group_id'):
            qs = qs.filter(groups__id=args['lead_group_id'])
        ordering = (args.get('ordering') or '-created_at').strip()
        if ordering.lstrip('-') not in {
            'created_at', 'updated_at', 'name', 'lead_score', 'next_follow_up_at',
        }:
            raise RuntimeError(
                'Unsupported ordering %r. Allowed: created_at, updated_at, name, '
                'lead_score, next_follow_up_at (optionally prefixed with -).' % ordering
            )
        page, page_size, offset = _paginate(args, default_size=20, max_size=100)
        total = qs.count()
        leads = list(qs.order_by(ordering).values(
            'id', 'name', 'phone', 'email', 'status_id', 'status__name',
            'priority', 'lead_score', 'source', 'city', 'assigned_to',
            'next_follow_up_at', 'created_at',
        )[offset:offset + page_size])
        return {'count': total, 'page': page, 'page_size': page_size, 'results': leads}

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

    # ── lookup_lead_by_phone ────────────────────────────────────────────────────
    if name == 'lookup_lead_by_phone':
        raw    = str(args.get('phone') or '')
        digits = ''.join(ch for ch in raw if ch.isdigit())
        if len(digits) < 6:
            raise RuntimeError('phone must contain at least 6 digits')
        qs = Lead.objects.filter(tenant_id=TENANT_ID).select_related('status')
        # Match on the last 10 significant digits first (mirrors the CDR
        # pipeline), then fall back to the full digit string.
        lead = (qs.filter(phone__endswith=digits[-10:]).first()
                or qs.filter(phone__endswith=digits).first())
        if not lead:
            return {'found': False, 'phone': raw}
        return {
            'found':  True,
            'id':     lead.id,
            'name':   lead.name,
            'phone':  lead.phone,
            'status': ({'id': lead.status_id, 'name': lead.status.name,
                        'color_hex': lead.status.color_hex} if lead.status else None),
        }

    # ── get_sales_dashboard ─────────────────────────────────────────────────────
    if name == 'get_sales_dashboard':
        from django.db.models import Count, Sum
        leads = Lead.objects.filter(tenant_id=TENANT_ID)
        now   = timezone.now()
        today = now.date()
        return {
            'totals': {
                'leads':           leads.count(),
                'high_priority':   leads.filter(priority='HIGH').count(),
                'followups_due':   leads.filter(next_follow_up_at__date__lte=today).count(),
                'estimated_value': leads.aggregate(total=Sum('value_amount')).get('total') or 0,
            },
            'status_breakdown': list(
                leads.values('status_id', 'status__name')
                .annotate(count=Count('id'))
                .order_by('status__order_index', 'status__name')
            ),
            'priority_breakdown': list(
                leads.values('priority').annotate(count=Count('id')).order_by('priority')
            ),
            'recent_leads': list(
                leads.order_by('-created_at').values(
                    'id', 'name', 'phone', 'status__name', 'lead_score', 'created_at',
                )[:5]
            ),
            'open_tasks': list(
                Task.objects.filter(tenant_id=TENANT_ID)
                .exclude(status__in=['DONE', 'CANCELLED'])
                .order_by('due_date', '-created_at')
                .values('id', 'title', 'status', 'priority', 'due_date',
                        'lead_id', 'lead__name')[:5]
            ),
            'upcoming_meetings': list(
                Meeting.objects.filter(tenant_id=TENANT_ID, start_at__gte=now)
                .order_by('start_at')
                .values('id', 'title', 'start_at', 'end_at', 'lead_id', 'lead__name')[:5]
            ),
            'recent_activities': list(
                LeadActivity.objects.filter(tenant_id=TENANT_ID)
                .order_by('-happened_at')
                .values('id', 'lead_id', 'lead__name', 'type', 'content', 'happened_at')[:8]
            ),
        }

    # ── get_lead_kanban ─────────────────────────────────────────────────────────
    if name == 'get_lead_kanban':
        try:
            limit = int(args.get('limit_per_status') or 20)
        except (TypeError, ValueError):
            raise RuntimeError('limit_per_status must be an integer')
        limit = min(max(limit, 1), 100)
        statuses = LeadStatus.objects.filter(
            tenant_id=TENANT_ID, is_active=True
        ).order_by('order_index')
        if args.get('status_id'):
            statuses = statuses.filter(id=args['status_id'])
        columns = []
        for st in statuses:
            lead_qs = Lead.objects.filter(tenant_id=TENANT_ID, status_id=st.id)
            columns.append({
                'id':          st.id,
                'name':        st.name,
                'color_hex':   st.color_hex,
                'order_index': st.order_index,
                'is_won':      st.is_won,
                'is_lost':     st.is_lost,
                'lead_count':  lead_qs.count(),
                'leads': list(lead_qs.order_by('-created_at').values(
                    'id', 'name', 'phone', 'email', 'priority', 'lead_score',
                    'assigned_to', 'next_follow_up_at', 'created_at',
                )[:limit]),
            })
        return {'limit_per_status': limit, 'statuses': columns}

    # ── get_lead_follow_up ──────────────────────────────────────────────────────
    if name == 'get_lead_follow_up':
        from notifications.models import Reminder, ReminderStatus
        lead = Lead.objects.get(id=args['lead_id'], tenant_id=TENANT_ID)
        reminder = (
            Reminder.objects
            .filter(tenant_id=TENANT_ID, lead_id=lead.id,
                    status__in=[ReminderStatus.PENDING, ReminderStatus.PROCESSING])
            .order_by('-updated_at')
            .values('id', 'follow_up_at', 'remind_at', 'offset_minutes', 'status')
            .first()
        )
        return {
            'lead_id':           lead.id,
            'lead_name':         lead.name,
            'next_follow_up_at': lead.next_follow_up_at,
            'last_contacted_at': lead.last_contacted_at,
            'reminder':          reminder,
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

    # -- list_users --
    if name == 'list_users':
        from crm.user_directory import fetch_tenant_users
        return fetch_tenant_users(
            search=args.get('search'),
            page_size=args.get('page_size', 100),
        )

    # -- assign_lead --
    if name == 'assign_lead':
        lead = Lead.objects.get(id=args['lead_id'], tenant_id=TENANT_ID)
        lead.assigned_to = args.get('assigned_to') or None
        lead.save(update_fields=['assigned_to', 'updated_at'])
        return {'id': lead.id, 'assigned_to': str(lead.assigned_to) if lead.assigned_to else None}

    # -- bulk_assign_leads --
    if name == 'bulk_assign_leads':
        assigned_to = args.get('assigned_to') or None
        success, failure, errors = 0, 0, []
        for lead_id in args.get('lead_ids', []):
            try:
                updated = Lead.objects.filter(
                    id=lead_id, tenant_id=TENANT_ID
                ).update(assigned_to=assigned_to)
                if updated:
                    success += 1
                else:
                    failure += 1
                    errors.append({'lead_id': lead_id, 'error': 'not found in tenant'})
            except Exception as exc:  # noqa: BLE001
                failure += 1
                errors.append({'lead_id': lead_id, 'error': str(exc)})
        return {
            'success_count': success,
            'failure_count': failure,
            'assigned_to': str(assigned_to) if assigned_to else None,
            'errors': errors,
        }

    # ── list_lead_groups ─────────────────────────────────────────
    if name == 'list_lead_groups':
        from django.db.models import Count
        qs = LeadGroup.objects.filter(tenant_id=TENANT_ID).annotate(
            lead_count=Count('memberships')
        )
        search = (args.get('search') or '').strip()
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(description__icontains=search))
        page      = max(int(args.get('page', 1)), 1)
        page_size = min(max(int(args.get('page_size', 50)), 1), 200)
        offset    = (page - 1) * page_size
        total     = qs.count()
        rows = list(qs.order_by('name').values(
            'id', 'name', 'description', 'color_hex', 'lead_count', 'created_at',
        )[offset:offset + page_size])
        return {'count': total, 'page': page, 'page_size': page_size, 'results': rows}

    # ── list_group_leads ────────────────────────────────────────────────────────
    if name == 'list_group_leads':
        group = LeadGroup.objects.get(id=args['lead_group_id'], tenant_id=TENANT_ID)
        qs = Lead.objects.filter(tenant_id=TENANT_ID, groups=group)
        search = (args.get('search') or '').strip()
        if search:
            qs = qs.filter(
                Q(name__icontains=search) |
                Q(phone__icontains=search) |
                Q(email__icontains=search)
            )
        page, page_size, offset = _paginate(args)
        total = qs.count()
        rows = list(qs.order_by('-created_at').values(
            'id', 'name', 'phone', 'email', 'status_id', 'status__name',
            'lead_score', 'assigned_to', 'created_at',
        )[offset:offset + page_size])
        return {
            'lead_group_id': group.id,
            'group_name':    group.name,
            'count':         total,
            'page':          page,
            'page_size':     page_size,
            'results':       rows,
        }

    # -- create_lead_group --
    if name == 'create_lead_group':
        group = LeadGroup.objects.create(
            tenant_id=TENANT_ID,
            name=args['name'],
            description=args.get('description') or None,
            color_hex=args.get('color_hex') or None,
            created_by=OWNER_USER_ID or TENANT_ID,
        )
        return {'id': group.id, 'name': group.name}

    # -- create_lead_status --
    if name == 'create_lead_status':
        order_index = args.get('order_index')
        if order_index is None:
            last = (
                LeadStatus.objects.filter(tenant_id=TENANT_ID)
                .order_by('-order_index')
                .values_list('order_index', flat=True)
                .first()
            )
            order_index = (last + 1) if last is not None else 0
        status_obj = LeadStatus.objects.create(
            tenant_id=TENANT_ID,
            name=args['name'],
            order_index=order_index,
            color_hex=args.get('color_hex') or None,
            is_won=args.get('is_won', False),
            is_lost=args.get('is_lost', False),
            is_active=args.get('is_active', True),
        )
        return {'id': status_obj.id, 'name': status_obj.name, 'order_index': status_obj.order_index}

    # ── list_lead_activities ────────────────────────────────────────────────────
    if name == 'list_lead_activities':
        qs = LeadActivity.objects.filter(tenant_id=TENANT_ID)
        if args.get('lead_id'):
            qs = qs.filter(lead_id=args['lead_id'])
        if args.get('type'):
            qs = qs.filter(type=args['type'])
        if args.get('happened_after'):
            qs = qs.filter(happened_at__gte=args['happened_after'])
        if args.get('happened_before'):
            qs = qs.filter(happened_at__lte=args['happened_before'])
        search = (args.get('search') or '').strip()
        if search:
            qs = qs.filter(content__icontains=search)
        page, page_size, offset = _paginate(args)
        total = qs.count()
        rows = list(qs.order_by('-happened_at').values(
            'id', 'lead_id', 'lead__name', 'type', 'content',
            'happened_at', 'meta', 'created_at',
        )[offset:offset + page_size])
        return {'count': total, 'page': page, 'page_size': page_size, 'results': rows}

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

    # ── list_tasks ──────────────────────────────────────────────────────────────
    if name == 'list_tasks':
        qs = Task.objects.filter(tenant_id=TENANT_ID)
        if args.get('lead_id'):
            qs = qs.filter(lead_id=args['lead_id'])
        if args.get('status'):
            qs = qs.filter(status=args['status'])
        if args.get('priority'):
            qs = qs.filter(priority=args['priority'])
        if args.get('assignee_user_id'):
            qs = qs.filter(assignee_user_id=args['assignee_user_id'])
        if args.get('due_after'):
            qs = qs.filter(due_date__gte=args['due_after'])
        if args.get('due_before'):
            qs = qs.filter(due_date__lte=args['due_before'])
        if args.get('overdue'):
            qs = qs.filter(due_date__lt=timezone.now()).exclude(
                status__in=['DONE', 'CANCELLED'])
        search = (args.get('search') or '').strip()
        if search:
            qs = qs.filter(Q(title__icontains=search) | Q(description__icontains=search))
        page, page_size, offset = _paginate(args)
        total = qs.count()
        rows = list(qs.order_by('due_date', '-created_at').values(
            'id', 'title', 'status', 'priority', 'due_date',
            'lead_id', 'lead__name', 'assignee_user_id', 'completed_at', 'created_at',
        )[offset:offset + page_size])
        return {'count': total, 'page': page, 'page_size': page_size, 'results': rows}

    # ── get_task ────────────────────────────────────────────────────────────────
    if name == 'get_task':
        task = Task.objects.select_related('lead').get(
            id=args['task_id'], tenant_id=TENANT_ID)
        return {
            'id':               task.id,
            'title':            task.title,
            'description':      task.description,
            'status':           task.status,
            'priority':         task.priority,
            'due_date':         task.due_date,
            'checklist':        task.checklist,
            'assignee_user_id': str(task.assignee_user_id) if task.assignee_user_id else None,
            'attachments_count': task.attachments_count,
            'lead': ({'id': task.lead_id, 'name': task.lead.name,
                      'phone': task.lead.phone} if task.lead_id else None),
            'created_at':   task.created_at,
            'updated_at':   task.updated_at,
            'completed_at': task.completed_at,
        }

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

    # ── list_meetings ───────────────────────────────────────────────────────────
    if name == 'list_meetings':
        qs = Meeting.objects.filter(tenant_id=TENANT_ID)
        if args.get('lead_id'):
            qs = qs.filter(lead_id=args['lead_id'])
        if args.get('start_after'):
            qs = qs.filter(start_at__gte=args['start_after'])
        if args.get('start_before'):
            qs = qs.filter(start_at__lte=args['start_before'])
        search = (args.get('search') or '').strip()
        if search:
            qs = qs.filter(
                Q(title__icontains=search) |
                Q(location__icontains=search) |
                Q(description__icontains=search) |
                Q(notes__icontains=search)
            )
        page, page_size, offset = _paginate(args)
        total = qs.count()
        rows = list(qs.order_by('start_at').values(
            'id', 'title', 'start_at', 'end_at', 'location', 'notes',
            'lead_id', 'lead__name', 'created_at',
        )[offset:offset + page_size])
        return {'count': total, 'page': page, 'page_size': page_size, 'results': rows}

    # ── get_meetings_calendar ───────────────────────────────────────────────────
    if name == 'get_meetings_calendar':
        from datetime import date as _date, timedelta as _timedelta
        MAX_DAYS = 92
        MAX_ROWS = 500
        month = (args.get('month') or '').strip()
        if month:
            try:
                year_s, month_s = month.split('-')
                start = _date(int(year_s), int(month_s), 1)
            except (ValueError, TypeError):
                raise RuntimeError('month must be YYYY-MM, e.g. "2026-08"')
            nxt = _date(start.year + (start.month == 12),
                        (start.month % 12) + 1, 1)
            end = nxt - _timedelta(days=1)
        else:
            try:
                start = (_date.fromisoformat(args['date_from'])
                         if args.get('date_from') else timezone.localdate())
                end   = (_date.fromisoformat(args['date_to'])
                         if args.get('date_to') else start + _timedelta(days=31))
            except (ValueError, TypeError):
                raise RuntimeError('date_from / date_to must be YYYY-MM-DD')
        if end < start:
            raise RuntimeError('date_to must not be before date_from')
        if (end - start).days > MAX_DAYS:
            end = start + _timedelta(days=MAX_DAYS)
        rows = list(
            Meeting.objects
            .filter(tenant_id=TENANT_ID,
                    start_at__date__gte=start, start_at__date__lte=end)
            .order_by('start_at')
            .values('id', 'title', 'start_at', 'end_at', 'location',
                    'lead_id', 'lead__name')[:MAX_ROWS]
        )
        calendar_data = {}
        for row in rows:
            started = row['start_at']
            if timezone.is_aware(started):
                started = timezone.localtime(started)
            calendar_data.setdefault(started.date().isoformat(), []).append(row)
        return {
            'date_from':     start.isoformat(),
            'date_to':       end.isoformat(),
            'count':         len(rows),
            'truncated':     len(rows) >= MAX_ROWS,
            'calendar_data': calendar_data,
        }

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
        # Primary system of record is DigiCRM (admin.celiyo.com user UUIDs), the
        # same fix applied to the REST view. The Laravel adapter is best-effort
        # only — its user model is separate and a miss must NOT fail the call.
        import uuid as _uuid
        lead = _get_lead(args['lead_id'])
        user_uid = str(args.get('user_uid', '')).strip()
        if not user_uid:
            raise RuntimeError('user_uid is required (resolve a name via list_users first)')
        try:
            _uuid.UUID(user_uid)
        except ValueError:
            raise RuntimeError('user_uid must be a valid UUID — resolve names via list_users')

        # Primary: write Lead.assigned_to directly in DigiCRM
        lead.assigned_to = user_uid
        lead.save(update_fields=['assigned_to'])
        LeadActivity.objects.create(
            lead=lead,
            tenant_id=TENANT_ID,
            type='NOTE',
            content='Chat assigned to user %s via MCP agent.' % user_uid,
            created_by=OWNER_USER_ID or TENANT_ID,
        )

        # Secondary: best-effort sync to the Laravel WhatsApp inbox panel
        adapter_result = {}
        if lead.phone:
            try:
                adapter_result = _adapter().assign_chat_user(phone=lead.phone, user_uid=user_uid)
            except Exception:
                adapter_result = {'wa_inbox_sync': 'skipped — no matching Laravel user for this UID'}

        return {
            'detail': 'Chat user assigned.',
            'lead_id': lead.id,
            'assigned_to': user_uid,
            **(adapter_result if isinstance(adapter_result, dict) else {}),
        }

    # ── mark_chat_read ──────────────────────────────────────────────────────────
    if name == 'mark_chat_read':
        lead = _get_lead(args['lead_id'])
        return _adapter().mark_chat_read(phone=lead.phone)

    # ── block_whatsapp_contact ──────────────────────────────────────────────────
    if name == 'block_whatsapp_contact':
        lead = _get_lead(args['lead_id'])
        return _adapter().block_contact(phone=lead.phone, block=args.get('block', True))

    # ── get_ai_context ──────────────────────────────────────────────────────────
    if name == 'get_ai_context':
        from django.db.models import Count
        # Templates live in the Laravel gateway. A gateway outage must not fail
        # the whole context call — return an empty list and keep the CRM half.
        try:
            raw_templates = _adapter().get_templates()
        except Exception as exc:  # noqa: BLE001
            logger.warning('get_ai_context: template fetch failed: %s', exc)
            raw_templates = []
        templates = [
            {
                'uid':      t.get('_uid') or t.get('uid'),
                'name':     t.get('template_name') or t.get('name'),
                'category': t.get('category'),
                'language': t.get('language'),
                'status':   t.get('status'),
            }
            for t in (raw_templates if isinstance(raw_templates, list) else [])
        ]
        sequences = list(
            WhatsAppSequence.objects.filter(tenant_id=TENANT_ID)
            .annotate(step_count=Count('steps', distinct=True))
            .order_by('name')
            .values('id', 'name', 'description', 'is_active', 'step_count')
        )
        statuses = list(
            LeadStatus.objects.filter(tenant_id=TENANT_ID)
            .order_by('order_index')
            .values('id', 'name', 'color_hex', 'order_index', 'is_won', 'is_lost')
        )
        groups = list(
            LeadGroup.objects.filter(tenant_id=TENANT_ID)
            .annotate(lead_count=Count('memberships'))
            .order_by('name')
            .values('id', 'name', 'lead_count')
        )
        return {
            'whatsapp_templates': templates,
            'sequences':          sequences,
            'lead_statuses':      statuses,
            'lead_groups':        groups,
        }

    # ── list_agent_action_logs ──────────────────────────────────────────────────
    if name == 'list_agent_action_logs':
        qs = AgentActionLog.objects.filter(tenant_id=TENANT_ID)
        if args.get('action_type'):
            qs = qs.filter(action_type=args['action_type'])
        try:
            limit = int(args.get('limit') or 50)
        except (TypeError, ValueError):
            raise RuntimeError('limit must be an integer')
        limit = min(max(limit, 1), 200)
        rows = list(qs.order_by('-created_at').values(
            'id', 'action_type', 'status', 'triggered_by',
            'payload_in', 'payload_out', 'error_message', 'created_at',
        )[:limit])
        return {'count': len(rows), 'limit': limit, 'results': rows}

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


    # ── list_sequences ──────────────────────────────────────────────────────────
    if name == 'list_sequences':
        from django.db.models import Count
        qs = WhatsAppSequence.objects.filter(tenant_id=TENANT_ID).annotate(
            step_count=Count('steps', distinct=True),
            active_enrollment_count=Count(
                'enrollments',
                filter=Q(enrollments__status=SequenceEnrollmentStatusEnum.ACTIVE),
                distinct=True,
            ),
        )
        if args.get('is_active') is not None:
            qs = qs.filter(is_active=bool(args['is_active']))
        search = (args.get('search') or '').strip()
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(description__icontains=search))
        page, page_size, offset = _paginate(args)
        total = qs.count()
        rows = list(qs.order_by('name').values(
            'id', 'name', 'description', 'is_active', 'stop_on_reply',
            'step_count', 'active_enrollment_count', 'created_at',
        )[offset:offset + page_size])
        return {'count': total, 'page': page, 'page_size': page_size, 'results': rows}

    # ── get_sequence_steps ──────────────────────────────────────────────────────
    if name == 'get_sequence_steps':
        seq = WhatsAppSequence.objects.get(
            id=args['sequence_id'], tenant_id=TENANT_ID)
        steps = list(seq.steps.order_by('step_number').values(
            'id', 'step_number', 'delay_days', 'template_uid',
            'template_name', 'template_variable_mapping',
        ))
        return {
            'sequence_id':   seq.id,
            'sequence_name': seq.name,
            'is_active':     seq.is_active,
            'count':         len(steps),
            'results':       steps,
        }

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

    # ── list_campaigns ──────────────────────────────────────────────────────────
    if name == 'list_campaigns':
        qs = WhatsAppCampaign.objects.filter(tenant_id=TENANT_ID)
        if args.get('status'):
            qs = qs.filter(status=args['status'])
        if args.get('lead_group_id'):
            qs = qs.filter(lead_group_id=args['lead_group_id'])
        search = (args.get('search') or '').strip()
        if search:
            qs = qs.filter(name__icontains=search)
        page, page_size, offset = _paginate(args)
        total = qs.count()
        rows = list(qs.order_by('-created_at').values(
            'id', 'name', 'status', 'template_uid', 'template_name',
            'lead_group_id', 'lead_group__name', 'total_contacts',
            'scheduled_at', 'launched_at', 'laravel_campaign_uid', 'created_at',
        )[offset:offset + page_size])
        return {'count': total, 'page': page, 'page_size': page_size, 'results': rows}

    # ── get_campaign_replies ────────────────────────────────────────────────────
    if name == 'get_campaign_replies':
        campaign = WhatsAppCampaign.objects.get(
            id=args['campaign_id'], tenant_id=TENANT_ID)
        if not campaign.laravel_campaign_uid:
            raise RuntimeError(
                'Campaign %s has not been launched yet — call launch_campaign first.'
                % campaign.id
            )
        page, per_page, _offset = _paginate(
            {'page': args.get('page'), 'page_size': args.get('per_page')})
        return _adapter().get_campaign_replies(
            campaign.laravel_campaign_uid, page, per_page)

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
    try:
        from mcp.server import TOOLS
        tool_count = len(TOOLS)
    except Exception:  # noqa: BLE001
        tool_count = None
    return _cors(JsonResponse({'status': 'ok', 'server': 'digicrm-mcp', 'tools': tool_count}))


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
