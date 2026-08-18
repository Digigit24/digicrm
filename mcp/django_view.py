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

# Browser origins allowed to call the MCP endpoint. Previously this was '*',
# which let any page on the internet script a cross-origin request against the
# endpoint. Override with a comma-separated MCP_ALLOWED_ORIGINS env var.
#
# Note: server-to-server MCP clients (curl, the Claude remote-MCP connector,
# the stdio bridge) send no Origin header at all. CORS does not apply to them,
# so they are unaffected by this list — it only constrains browsers.
_DEFAULT_MCP_ORIGINS = (
    'https://claude.ai',
    'https://www.claude.ai',
    'https://crm.celiyo.com',
    'https://admin.celiyo.com',
)
MCP_ALLOWED_ORIGINS = tuple(
    o.strip().rstrip('/')
    for o in _cfg('MCP_ALLOWED_ORIGINS', '').split(',')
    if o.strip()
) or _DEFAULT_MCP_ORIGINS


def _cors(response, request=None):
    """Attach CORS headers, restricted to MCP_ALLOWED_ORIGINS.

    An unrecognised Origin gets NO Access-Control-Allow-Origin header, so the
    browser blocks the response. A request with no Origin (non-browser client)
    also gets no header, because CORS is not involved.
    """
    origin = ''
    if request is not None:
        origin = (request.headers.get('Origin') or '').strip().rstrip('/')
    if origin and origin in MCP_ALLOWED_ORIGINS:
        response['Access-Control-Allow-Origin'] = origin
    elif origin:
        logger.warning('MCP CORS: rejected origin %r', origin)
    response['Vary']                         = 'Origin'
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
    if 'secret' in request.GET:
        # Removed on purpose: a secret in the query string is written to access
        # logs, proxy logs, browser history and Referer headers.
        logger.warning(
            'MCP auth: ?secret= query parameter is no longer accepted; '
            'send Authorization: Bearer <MCP_SECRET> instead.'
        )
    auth = request.headers.get('Authorization', '')
    if not auth.startswith('Bearer '):
        return False
    # Compare as bytes: secrets.compare_digest() raises TypeError on str
    # operands that are not ASCII-only.
    return secrets.compare_digest(
        auth[7:].strip().encode('utf-8'), MCP_SECRET.encode('utf-8'))


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
    }), request)


def oauth_protected_resource(request, path=''):
    base = f"{'https' if request.is_secure() else 'http'}://{request.get_host()}"
    return _cors(JsonResponse({
        'resource':                f'{base}/mcp/sse',
        'authorization_servers':   [base],
        'bearer_methods_supported':['header'],
    }), request)


@csrf_exempt
def oauth_register(request):
    if request.method == 'OPTIONS':
        return _cors(HttpResponse(), request)
    return _cors(JsonResponse({
        'client_id':                     MCP_CLIENT_ID,
        'client_secret':                 MCP_SECRET or 'configure-MCP_SECRET-env-var',
        'client_name':                   'DigiCRM MCP',
        'grant_types':                   ['client_credentials'],
        'token_endpoint_auth_method':    'client_secret_post',
    }, status=201), request)


@csrf_exempt
def oauth_token(request):
    if request.method == 'OPTIONS':
        return _cors(HttpResponse(), request)
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
                                  status=503), request)
    if client_secret != MCP_SECRET:
        return _cors(JsonResponse({'error': 'invalid_client'}, status=401), request)
    return _cors(JsonResponse({
        'access_token': MCP_SECRET,
        'token_type':   'bearer',
        'expires_in':   31536000,
    }), request)


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


def _require(model, obj_id, label, **scope):
    """Resolve `obj_id` inside `scope` or raise. NEVER fetch by bare primary key.

    Every id in `args` is model-supplied and therefore untrusted. The MCP HTTP
    path talks to the ORM directly, so DRF's tenant mixins and permission
    classes never run: an `.objects.get(pk=...)` with no tenant filter is a
    cross-tenant read or write (audit finding A5).

    `scope` is the tenant predicate. It is usually ``tenant_id=TENANT_ID``, but
    for a model with no tenant column of its own -- WhatsAppSequenceStep -- it
    joins to the owning row instead, e.g. ``sequence__tenant_id=TENANT_ID``
    (audit finding M4). Adding the column would need a migration, which this
    layer must not do.
    """
    if obj_id is None:
        raise RuntimeError('%s id is required' % label)
    obj = model.objects.filter(pk=obj_id, **scope).first()
    if obj is None:
        raise RuntimeError(
            '%s %s does not exist in this workspace.' % (label, obj_id))
    return obj


# Fields on Meeting that the calendar-backend branch added and that both meeting
# write tools accept verbatim (arg name == column name).
_MEETING_PASSTHROUGH = (
    'meeting_type', 'all_day', 'timezone', 'location', 'description', 'notes',
    'conference_url', 'status', 'visibility',
)


def _meeting_datetime(args, key, required=False):
    """Parse an ISO 8601 meeting timestamp into an aware datetime."""
    from django.utils import timezone as _tz
    from django.utils.dateparse import parse_datetime
    raw = args.get(key)
    if raw in (None, ''):
        if required:
            raise RuntimeError('%s is required' % key)
        return None
    parsed = parse_datetime(str(raw))
    if parsed is None:
        raise RuntimeError(
            '%s must be an ISO 8601 datetime, e.g. 2026-09-01T10:30:00Z' % key)
    if _tz.is_naive(parsed):
        parsed = _tz.make_aware(parsed, _tz.get_current_timezone())
    return parsed


def _meeting_recurrence(rrule, dtstart, tz_name):
    """Validate an RRULE and resolve its denormalised series end.

    Reuses meetings.recurrence -- the calendar-backend branch's canonical
    helpers -- so an MCP-created series behaves exactly like a UI-created one
    instead of a second, subtly different RRULE parser.
    """
    from meetings import recurrence
    normalized = recurrence.normalize_rule(rrule)
    if not normalized:
        return None, None
    if 'FREQ=' not in normalized.upper():
        raise RuntimeError(
            'rrule must be an RFC 5545 recurrence rule containing FREQ=, e.g. '
            '"FREQ=WEEKLY;BYDAY=MO,WE". Got %r.' % rrule)
    if not recurrence.rule_is_valid(normalized, dtstart, tz_name):
        raise RuntimeError('rrule %r is not a rule this calendar can expand.' % rrule)
    return normalized, recurrence.compute_recurrence_end(normalized, dtstart, tz_name)


def _meeting_attendee_specs(args, meeting_tenant_id):
    """Validate the `attendees` argument into ready-to-create kwargs.

    Every id is tenant-checked here, before anything is written: attendee
    lead_ids go through _require, and user_ids must at least be well-formed
    UUIDs (the user directory is a separate service, so it cannot be joined).
    """
    import uuid as _uuid
    from crm.models import Lead as _Lead
    raw = args.get('attendees') or []
    if not isinstance(raw, list):
        raise RuntimeError('attendees must be a list of objects')
    specs = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise RuntimeError(
                'attendees[%d] must be an object with at least one of '
                'user_id, lead_id or email' % index)
        user_id = (str(item.get('user_id')).strip()
                   if item.get('user_id') else None)
        if user_id:
            try:
                _uuid.UUID(user_id)
            except ValueError:
                raise RuntimeError(
                    'attendees[%d].user_id must be a user UUID — resolve names '
                    'via list_users' % index)
        lead_id = item.get('lead_id')
        if lead_id is not None:
            lead_id = _require(_Lead, lead_id, 'Attendee lead',
                               tenant_id=meeting_tenant_id).id
        email = (item.get('email') or '').strip()
        if not (user_id or lead_id or email):
            raise RuntimeError(
                'attendees[%d] needs at least one of user_id, lead_id or email'
                % index)
        role = (item.get('role') or 'REQUIRED').upper()
        if role not in ('REQUIRED', 'OPTIONAL'):
            raise RuntimeError(
                'attendees[%d].role must be REQUIRED or OPTIONAL — the '
                'organizer is assigned by the server' % index)
        specs.append({
            'user_id':      user_id,
            'lead_id':      lead_id,
            'email':        email,
            'display_name': (item.get('display_name') or '').strip(),
            'role':         role,
            'notify':       bool(item.get('notify', True)),
        })
    return specs


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
                Meeting.objects.filter(tenant_id=TENANT_ID, start_at__gte=now,
                                       is_deleted=False)
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
            # Custom fields live in Lead.metadata. This was declared in the
            # schema but never written, so every custom_fields payload was
            # silently discarded. Key names come from get_lead_field_schema.
            metadata=args.get('custom_fields') or None,
        )
        return {'id': lead.id, 'name': lead.name, 'phone': lead.phone}

    # ── get_lead_field_schema ───────────────────────────────────────────────────
    if name == 'get_lead_field_schema':
        from crm.models import LeadFieldConfiguration
        from crm.utils import ensure_default_field_configurations
        # Idempotent: seeds the tenant's standard field rows on first read,
        # exactly like GET /api/crm/field-configurations/field_schema/.
        ensure_default_field_configurations(TENANT_ID)
        cols = (
            'field_name', 'field_label', 'field_type', 'is_required',
            'is_visible', 'options', 'placeholder', 'help_text',
            'default_value', 'display_order',
        )
        fields = LeadFieldConfiguration.objects.filter(
            tenant_id=TENANT_ID, is_active=True
        ).order_by('display_order', 'field_label')
        return {
            'standard_fields': list(fields.filter(is_standard=True).values(*cols)),
            'custom_fields':   list(fields.filter(is_standard=False).values(*cols)),
        }

    # ── update_lead ─────────────────────────────────────────────────────────────
    if name == 'update_lead':
        lead = Lead.objects.get(id=args['lead_id'], tenant_id=TENANT_ID)
        for f in ['name', 'phone', 'email', 'lead_score', 'notes', 'source',
                  'city', 'state', 'country', 'company', 'title']:
            if f in args:
                setattr(lead, f, args[f])
        if 'assigned_to' in args:
            lead.assigned_to = args['assigned_to'] or None
        if args.get('custom_fields'):
            # MERGE, not replace: a partial custom_fields payload must not wipe
            # keys the agent did not mention (same reasoning as append_lead_note).
            merged = dict(lead.metadata or {})
            merged.update(args['custom_fields'])
            lead.metadata = merged
        lead.save()
        return {'id': lead.id, 'updated': True}

    # ── update_lead_status ──────────────────────────────────────────────────────
    if name == 'update_lead_status':
        lead = Lead.objects.get(id=args['lead_id'], tenant_id=TENANT_ID)
        # The status id is untrusted: without this check a lead could be pointed
        # at another tenant's pipeline stage.
        _require(LeadStatus, args['status_id'], 'Lead status', tenant_id=TENANT_ID)
        lead.status_id = args['status_id']
        lead.save(update_fields=['status'])
        if args.get('note'):
            # `note` was declared but discarded. Record it on the timeline so
            # the reason for a stage change survives.
            LeadActivity.objects.create(
                tenant_id=TENANT_ID,
                lead_id=lead.id,
                type='NOTE',
                content='Status changed: %s' % args['note'],
                happened_at=timezone.now(),
                by_user_id=OWNER_USER_ID or None,
            )
        return {'id': lead.id, 'status_id': args['status_id'],
                'note_logged': bool(args.get('note'))}

    # ── append_lead_note ────────────────────────────────────────────────────────
    if name == 'append_lead_note':
        from django.db import transaction
        text = str(args.get('text') or '').strip()
        if not text:
            raise RuntimeError('text is required and must not be blank')
        stamp  = timezone.now().strftime('%Y-%m-%d %H:%M')
        header = '\u2014 %s' % stamp + (' \u00b7 %s' % OWNER_USER_ID if OWNER_USER_ID else '')
        block  = '%s\n%s' % (header, text)
        with transaction.atomic():
            # Read-modify-write under a row lock so concurrent appends are not
            # lost — this is the non-destructive alternative to update_lead.
            lead = Lead.objects.select_for_update().get(
                id=args['lead_id'], tenant_id=TENANT_ID)
            existing = lead.notes or ''
            lead.notes = ('%s\n\n%s' % (existing, block)) if existing.strip() else block
            lead.save(update_fields=['notes', 'updated_at'])
        return {'id': lead.id, 'appended': True, 'notes': lead.notes}

    # ── set_lead_follow_up ──────────────────────────────────────────────────────
    if name == 'set_lead_follow_up':
        from django.db import transaction
        from django.utils.dateparse import parse_datetime
        from notifications.models import Reminder, ReminderStatus
        if not OWNER_USER_ID:
            raise RuntimeError('MCP_OWNER_USER_ID env var not set')
        follow_up_at = parse_datetime(str(args['follow_up_at']))
        if follow_up_at is None:
            raise RuntimeError(
                'follow_up_at must be an ISO 8601 datetime, e.g. 2026-09-01T10:30:00Z')
        if timezone.is_naive(follow_up_at):
            follow_up_at = timezone.make_aware(
                follow_up_at, timezone.get_current_timezone())
        enabled = args.get('reminder_enabled', True)
        try:
            offset = int(args.get('reminder_offset_minutes') or 0)
        except (TypeError, ValueError):
            raise RuntimeError('reminder_offset_minutes must be an integer')
        if offset < 0:
            raise RuntimeError('reminder_offset_minutes must be 0 or greater')
        now       = timezone.now()
        remind_at = follow_up_at - timezone.timedelta(minutes=offset) if enabled else None
        if enabled and remind_at <= now:
            raise RuntimeError(
                'Reminder time %s is not in the future — pick a later follow_up_at '
                'or a smaller reminder_offset_minutes.' % remind_at.isoformat())

        with transaction.atomic():
            lead = Lead.objects.select_for_update().get(
                id=args['lead_id'], tenant_id=TENANT_ID)
            lead.next_follow_up_at = follow_up_at
            lead.save(update_fields=['next_follow_up_at', 'updated_at'])

            active = Reminder.objects.select_for_update().filter(
                tenant_id=TENANT_ID,
                lead_id=lead.id,
                recipient_user_id=OWNER_USER_ID,
                status__in=[ReminderStatus.PENDING, ReminderStatus.PROCESSING],
            ).order_by('-updated_at')
            reminder = active.first()

            if enabled:
                if reminder is None:
                    reminder = Reminder.objects.create(
                        tenant_id=TENANT_ID,
                        lead_id=lead.id,
                        recipient_user_id=OWNER_USER_ID,
                        created_by_user_id=OWNER_USER_ID,
                        follow_up_at=follow_up_at,
                        remind_at=remind_at,
                        offset_minutes=offset,
                    )
                else:
                    reminder.follow_up_at   = follow_up_at
                    reminder.remind_at      = remind_at
                    reminder.offset_minutes = offset
                    reminder.status         = ReminderStatus.PENDING
                    reminder.locked_at      = None
                    reminder.cancelled_at   = None
                    reminder.last_error     = ''
                    reminder.save(update_fields=[
                        'follow_up_at', 'remind_at', 'offset_minutes', 'status',
                        'locked_at', 'cancelled_at', 'last_error', 'updated_at',
                    ])
            else:
                active.update(status=ReminderStatus.CANCELLED,
                              cancelled_at=now, locked_at=None)
                reminder = None

        return {
            'lead_id':           lead.id,
            'next_follow_up_at': lead.next_follow_up_at,
            'reminder': ({
                'id':             reminder.id,
                'remind_at':      reminder.remind_at,
                'offset_minutes': reminder.offset_minutes,
                'status':         reminder.status,
            } if reminder else None),
        }

    # ── bulk_update_lead_status ─────────────────────────────────────────────────
    if name == 'bulk_update_lead_status':
        lead_ids = args.get('lead_ids') or []
        if not lead_ids:
            raise RuntimeError('lead_ids must not be empty')
        status_id = args.get('status_id')
        if status_id is not None and not LeadStatus.objects.filter(
                id=status_id, tenant_id=TENANT_ID).exists():
            raise RuntimeError(
                'Status %s does not exist in this workspace — get valid ids from '
                'list_lead_statuses.' % status_id)
        updated = Lead.objects.filter(
            tenant_id=TENANT_ID, id__in=lead_ids
        ).update(status_id=status_id)
        return {
            'updated_count': updated,
            'requested':     len(lead_ids),
            'status_id':     status_id,
        }

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
                    metadata=row.get('custom_fields') or None,
                )
                success += 1
            except Exception as exc:
                failure += 1
                errors.append({'row': i, 'name': row.get('name'), 'error': str(exc)})
        return {'success_count': success, 'failure_count': failure, 'errors': errors}

    # ── add_lead_to_group ───────────────────────────────────────────────────────
    if name == 'add_lead_to_group':
        lead  = _require(Lead, args['lead_id'], 'Lead', tenant_id=TENANT_ID)
        group = _require(LeadGroup, args['lead_group_id'], 'Lead group',
                         tenant_id=TENANT_ID)
        _membership, created = LeadGroupMembership.objects.get_or_create(
            lead_id=lead.id,
            group_id=group.id,
        )
        return {'lead_id': lead.id, 'group_id': group.id, 'added': created}

    # ── add_leads_to_group ──────────────────────────────────────────────────────
    if name == 'add_leads_to_group':
        group = LeadGroup.objects.get(id=args['lead_group_id'], tenant_id=TENANT_ID)
        lead_ids = args.get('lead_ids') or []
        if not lead_ids:
            raise RuntimeError('lead_ids must not be empty')
        leads = list(Lead.objects.filter(id__in=lead_ids, tenant_id=TENANT_ID))
        added, already_in = 0, 0
        for lead in leads:
            _membership, created = LeadGroupMembership.objects.get_or_create(
                group=group,
                lead=lead,
                defaults={'added_by': OWNER_USER_ID or None},
            )
            if created:
                added += 1
            else:
                already_in += 1
        return {
            'lead_group_id':    group.id,
            'group_name':       group.name,
            'added':            added,
            'already_in_group': already_in,
            'not_found':        len(set(lead_ids)) - len(leads),
        }

    # ── remove_leads_from_group ─────────────────────────────────────────────────
    if name == 'remove_leads_from_group':
        group = LeadGroup.objects.get(id=args['lead_group_id'], tenant_id=TENANT_ID)
        lead_ids = args.get('lead_ids') or []
        if not lead_ids:
            raise RuntimeError('lead_ids must not be empty')
        # Scope to this tenant's leads before deleting any membership rows.
        tenant_lead_ids = list(
            Lead.objects.filter(id__in=lead_ids, tenant_id=TENANT_ID)
            .values_list('id', flat=True)
        )
        deleted, _detail = LeadGroupMembership.objects.filter(
            group=group, lead_id__in=tenant_lead_ids
        ).delete()
        return {
            'lead_group_id': group.id,
            'group_name':    group.name,
            'removed':       deleted,
        }

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
        lead = _require(Lead, args['lead_id'], 'Lead', tenant_id=TENANT_ID)
        activity = LeadActivity.objects.create(
            tenant_id=TENANT_ID,
            lead_id=lead.id,
            type=args['type'],
            content=args['content'],
            happened_at=args.get('happened_at') or timezone.now(),
            by_user_id=OWNER_USER_ID or None,
        )
        return {'id': activity.id, 'lead_id': lead.id}

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
        # Task.lead is non-nullable; an unvalidated id would attach this
        # tenant's task to another tenant's lead and leak its name back through
        # list_tasks(lead__name).
        lead = _require(Lead, args.get('lead_id'), 'Lead', tenant_id=TENANT_ID)
        task = Task.objects.create(
            tenant_id=TENANT_ID,
            owner_user_id=OWNER_USER_ID,
            title=args['title'],
            description=args.get('description') or '',
            lead_id=lead.id,
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
        if 'assignee_user_id' in args:
            task.assignee_user_id = args['assignee_user_id'] or None
        task.save()
        return {'id': task.id, 'updated': True}

    # ── list_meetings ───────────────────────────────────────────────────────────
    if name == 'list_meetings':
        qs = Meeting.objects.filter(tenant_id=TENANT_ID, is_deleted=False)
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
            'id', 'uid', 'title', 'start_at', 'end_at', 'all_day', 'timezone',
            'meeting_type', 'status', 'visibility', 'location', 'conference_url',
            'notes', 'lead_id', 'lead__name', 'recurrence_rule', 'created_at',
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
            .filter(tenant_id=TENANT_ID, is_deleted=False,
                    start_at__date__gte=start, start_at__date__lte=end)
            .order_by('start_at')
            .values('id', 'title', 'start_at', 'end_at', 'all_day', 'timezone',
                    'meeting_type', 'status', 'location', 'conference_url',
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
        from django.db import transaction
        from meetings.models import (
            MeetingAttendee, AttendeeRoleEnum, AttendeeResponseEnum,
        )
        from meetings import recurrence as _recurrence
        if not OWNER_USER_ID:
            raise RuntimeError('MCP_OWNER_USER_ID env var not set')

        start_at = _meeting_datetime(args, 'start_time', required=True)
        end_at   = _meeting_datetime(args, 'end_time',   required=True)
        if end_at < start_at:
            raise RuntimeError('end_time must not be before start_time')

        # lead_id is optional now: an INTERNAL meeting has no lead. When given
        # it still has to belong to this workspace.
        lead_id = None
        if args.get('lead_id') is not None:
            lead_id = _require(Lead, args['lead_id'], 'Lead', tenant_id=TENANT_ID).id

        tz_name = (args.get('timezone') or 'UTC').strip() or 'UTC'
        if not _recurrence.is_valid_timezone(tz_name):
            raise RuntimeError(
                'timezone %r is not a known IANA timezone, e.g. "Asia/Kolkata" '
                'or "UTC".' % tz_name)

        rrule, recurrence_end = _meeting_recurrence(
            args.get('rrule'), start_at, tz_name)

        # Validate every attendee id BEFORE writing anything.
        specs = _meeting_attendee_specs(args, TENANT_ID)

        fields = {
            'tenant_id':     TENANT_ID,
            'owner_user_id': OWNER_USER_ID,
            'lead_id':       lead_id,
            'title':         args['title'],
            'start_at':      start_at,
            'end_at':        end_at,
            'timezone':      tz_name,
        }
        for key in _MEETING_PASSTHROUGH:
            if key in ('timezone', 'notes'):
                continue
            if args.get(key) is not None:
                fields[key] = args[key]
        fields['notes'] = args.get('notes') or ''
        if rrule:
            fields['recurrence_rule']   = rrule
            fields['recurrence_end_at'] = recurrence_end

        with transaction.atomic():
            meeting = Meeting.objects.create(**fields)
            # The organizer row is what makes this meeting visible to `team`
            # scope and RSVP. A meeting created without it is invisible to
            # everyone but its owner.
            attendees = [MeetingAttendee.objects.create(
                tenant_id=TENANT_ID,
                meeting=meeting,
                user_id=OWNER_USER_ID,
                display_name='MCP agent',
                role=AttendeeRoleEnum.ORGANIZER,
                response_status=AttendeeResponseEnum.ACCEPTED,
                is_organizer=True,
            )]
            for spec in specs:
                # The organizer is already on the meeting; a duplicate user_id
                # would violate uniq_attendee_user_per_meeting.
                if spec['user_id'] and spec['user_id'] == OWNER_USER_ID:
                    continue
                attendees.append(MeetingAttendee.objects.create(
                    tenant_id=TENANT_ID,
                    meeting=meeting,
                    user_id=spec['user_id'],
                    lead_id=spec['lead_id'],
                    email=spec['email'],
                    display_name=spec['display_name'],
                    role=spec['role'],
                    notify=spec['notify'],
                ))

        return {
            'id':                meeting.id,
            'uid':               str(meeting.uid),
            'title':             meeting.title,
            'start_at':          meeting.start_at,
            'end_at':            meeting.end_at,
            'all_day':           meeting.all_day,
            'timezone':          meeting.timezone,
            'meeting_type':      meeting.meeting_type,
            'status':            meeting.status,
            'visibility':        meeting.visibility,
            'lead_id':           meeting.lead_id,
            'recurrence_rule':   meeting.recurrence_rule,
            'recurrence_end_at': meeting.recurrence_end_at,
            'attendees': [
                {'id': a.id, 'user_id': str(a.user_id) if a.user_id else None,
                 'lead_id': a.lead_id, 'email': a.email, 'role': a.role}
                for a in attendees
            ],
        }

    # ── update_meeting ──────────────────────────────────────────────────────────
    if name == 'update_meeting':
        from meetings.models import MeetingStatusEnum
        from meetings import recurrence as _recurrence
        # is_deleted=False in the scope: a soft-deleted meeting must read as
        # gone, not as editable.
        meeting = _require(Meeting, args['meeting_id'], 'Meeting',
                           tenant_id=TENANT_ID, is_deleted=False)
        changed = []

        if 'title' in args:
            meeting.title = args['title']
            changed.append('title')
        if args.get('start_time') is not None:
            meeting.start_at = _meeting_datetime(args, 'start_time')
            changed.append('start_at')
        if args.get('end_time') is not None:
            meeting.end_at = _meeting_datetime(args, 'end_time')
            changed.append('end_at')
        if meeting.end_at < meeting.start_at:
            raise RuntimeError('end_time must not be before start_time')

        if args.get('lead_id') is not None:
            meeting.lead_id = _require(Lead, args['lead_id'], 'Lead',
                                       tenant_id=TENANT_ID).id
            changed.append('lead_id')

        if args.get('timezone') is not None:
            tz_name = str(args['timezone']).strip() or 'UTC'
            if not _recurrence.is_valid_timezone(tz_name):
                raise RuntimeError(
                    'timezone %r is not a known IANA timezone, e.g. '
                    '"Asia/Kolkata" or "UTC".' % tz_name)
            meeting.timezone = tz_name
            changed.append('timezone')

        for key in _MEETING_PASSTHROUGH:
            if key in ('timezone', 'status'):
                continue
            if key in args and args[key] is not None:
                setattr(meeting, key, args[key])
                changed.append(key)

        if 'rrule' in args:
            rrule, recurrence_end = _meeting_recurrence(
                args.get('rrule'), meeting.start_at, meeting.timezone)
            meeting.recurrence_rule   = rrule
            meeting.recurrence_end_at = recurrence_end
            changed += ['recurrence_rule', 'recurrence_end_at']

        if args.get('status') is not None:
            meeting.status = args['status']
            changed.append('status')
            if meeting.status == MeetingStatusEnum.CANCELLED:
                meeting.cancelled_at          = timezone.now()
                meeting.cancelled_by_user_id  = OWNER_USER_ID or None
                meeting.cancellation_reason   = args.get('cancellation_reason') or ''
                changed += ['cancelled_at', 'cancelled_by_user_id',
                            'cancellation_reason']
            elif meeting.status == MeetingStatusEnum.COMPLETED:
                meeting.completed_at = timezone.now()
                changed.append('completed_at')

        if not changed:
            raise RuntimeError(
                'Nothing to update — send at least one field besides meeting_id')

        meeting.save()
        return {'id': meeting.id, 'updated': True, 'changed_fields': changed,
                'status': meeting.status}


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

    def _log_send_note(lead, note):
        """Write the optional `note` argument to the lead's timeline.

        Both send tools declared `note` ("Activity note to log on the lead")
        and neither wrote it anywhere. A logging failure must not make a
        already-sent message look like a failed send.
        """
        if not note:
            return
        try:
            LeadActivity.objects.create(
                tenant_id=TENANT_ID,
                lead_id=lead.id,
                type='NOTE',
                content=note,
                happened_at=timezone.now(),
                by_user_id=OWNER_USER_ID or None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error('Failed to log send note on lead %s: %s', lead.id, exc)

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

    # ── list_whatsapp_templates_detailed ────────────────────────────────────────
    if name == 'list_whatsapp_templates_detailed':
        def _template_body(tpl):
            for comp in (tpl.get('components') or []):
                if str(comp.get('type') or '').upper() == 'BODY':
                    return comp.get('text', '')
            return ''

        raw = _adapter().get_templates()
        rows = [
            {
                'uid':      t.get('_uid') or t.get('uid'),
                'name':     t.get('template_name') or t.get('name'),
                'category': t.get('category'),
                'language': t.get('language'),
                'status':   t.get('status'),
                'body':     _template_body(t),
            }
            for t in (raw if isinstance(raw, list) else [])
        ]
        search   = (args.get('search') or '').strip().lower()
        category = (args.get('category') or '').strip().upper()
        if search:
            rows = [t for t in rows if search in (t['name'] or '').lower()]
        if category:
            rows = [t for t in rows if (t['category'] or '').upper() == category]
        return {'count': len(rows), 'results': rows}

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
        result = _adapter().send_message(
            phone=lead.phone,
            name=lead.name,
            template_uid=args['template_uid'],
            template_components=args.get('template_components', []),
            digicrm_lead_id=lead.id,
        )
        _log_send_note(lead, args.get('note'))
        return result

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
        result = _adapter().send_message(
            phone=lead.phone,
            name=lead.name,
            template_uid=args['template_uid'],
            template_components=args.get('template_components', []),
            digicrm_lead_id=lead.id,
        )
        _log_send_note(lead, args.get('note'))
        return result

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
                # `payload` was declared ("Any structured data to attach") and
                # dropped on the floor.
                'payload': args.get('payload'),
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

    # ── list_active_sequences_with_steps ────────────────────────────────────────
    if name == 'list_active_sequences_with_steps':
        rows = []
        for seq in (WhatsAppSequence.objects
                    .filter(tenant_id=TENANT_ID, is_active=True)
                    .prefetch_related('steps')
                    .order_by('name')):
            steps = list(seq.steps.order_by('step_number').values(
                'id', 'step_number', 'delay_days', 'template_uid',
                'template_name', 'template_variable_mapping',
            ))
            rows.append({
                'id':            seq.id,
                'name':          seq.name,
                'description':   seq.description,
                'stop_on_reply': seq.stop_on_reply,
                'step_count':    len(steps),
                'steps':         steps,
            })
        return {'count': len(rows), 'results': rows}

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
        seq = _require(WhatsAppSequence, args['sequence_id'], 'Sequence',
                       tenant_id=TENANT_ID)
        step = WhatsAppSequenceStep.objects.create(
            sequence_id=seq.id,
            step_number=args['step_number'],
            delay_days=args.get('delay_days', 0),
            template_uid=args['template_uid'],
            template_name=args.get('template_name', ''),
            template_variable_mapping=args.get('template_variable_mapping', {}),
        )
        return {'id': step.id, 'step_number': step.step_number}

    # ── update_sequence_step ────────────────────────────────────────────────────
    if name == 'update_sequence_step':
        # WhatsAppSequenceStep has no tenant_id column (audit M4), so scope
        # through the parent sequence rather than adding a migration.
        # sequence_id is declared AND required, so honour it: the step must
        # belong to that sequence, which must belong to this tenant.
        if args.get('sequence_id') is None:
            raise RuntimeError(
                'sequence_id is required -- get it from list_sequences, and the '
                'step ids from get_sequence_steps')
        step = _require(WhatsAppSequenceStep, args['step_id'], 'Sequence step',
                        sequence_id=args['sequence_id'],
                        sequence__tenant_id=TENANT_ID)
        for f in ['delay_days', 'template_uid', 'template_name', 'template_variable_mapping']:
            if f in args:
                setattr(step, f, args[f])
        step.save()
        return {'id': step.id, 'updated': True}

    # ── delete_sequence_step ────────────────────────────────────────────────────
    if name == 'delete_sequence_step':
        # Scoped through the parent sequence -- see update_sequence_step.
        if args.get('sequence_id') is None:
            raise RuntimeError(
                'sequence_id is required -- get it from list_sequences, and the '
                'step ids from get_sequence_steps')
        step = _require(WhatsAppSequenceStep, args['step_id'], 'Sequence step',
                        sequence_id=args['sequence_id'],
                        sequence__tenant_id=TENANT_ID)
        deleted, _detail = WhatsAppSequenceStep.objects.filter(pk=step.pk).delete()
        return {'deleted': deleted > 0}

    # ── enroll_lead_in_sequence ─────────────────────────────────────────────────
    if name == 'enroll_lead_in_sequence':
        seq  = WhatsAppSequence.objects.get(id=args['sequence_id'], tenant_id=TENANT_ID)
        lead = _require(Lead, args['lead_id'], 'Lead', tenant_id=TENANT_ID)
        first_step = seq.steps.order_by('step_number').first()
        delay = first_step.delay_days if first_step else 0
        next_step_at = timezone.now() + timezone.timedelta(days=delay)
        enrollment, created = LeadSequenceEnrollment.objects.update_or_create(
            lead_id=lead.id,
            sequence_id=seq.id,
            defaults={
                'tenant_id':    TENANT_ID,
                'status':       SequenceEnrollmentStatusEnum.ACTIVE,
                'next_step_at': next_step_at,
                'enrolled_by':  OWNER_USER_ID or None,
            },
        )
        return {'id': enrollment.id, 'created': created, 'next_step_at': str(enrollment.next_step_at)}

    # ── bulk_enroll_leads_in_sequence ───────────────────────────────────────────
    if name == 'bulk_enroll_leads_in_sequence':
        from whatsapp_integration.models import AgentActionTypeEnum
        lead_ids = args.get('lead_ids') or []
        if not lead_ids:
            raise RuntimeError('lead_ids must not be empty')
        seq = WhatsAppSequence.objects.filter(
            id=args['sequence_id'], tenant_id=TENANT_ID, is_active=True).first()
        if seq is None:
            raise RuntimeError(
                'Sequence %s not found or is inactive — get valid ids from '
                'list_sequences.' % args['sequence_id'])
        first_step   = seq.steps.order_by('step_number').first()
        next_step_at = timezone.now() + timezone.timedelta(
            days=first_step.delay_days if first_step else 0)

        enrolled, skipped = [], []
        for lead_id in lead_ids:
            lead = Lead.objects.filter(id=lead_id, tenant_id=TENANT_ID).first()
            if lead is None:
                skipped.append({'lead_id': lead_id, 'reason': 'not found'})
                continue
            enrollment, created = LeadSequenceEnrollment.objects.get_or_create(
                lead=lead,
                sequence=seq,
                defaults={
                    'tenant_id':    TENANT_ID,
                    'status':       SequenceEnrollmentStatusEnum.ACTIVE,
                    'next_step_at': next_step_at,
                    'enrolled_by':  OWNER_USER_ID or None,
                },
            )
            if not created and enrollment.status == SequenceEnrollmentStatusEnum.ACTIVE:
                skipped.append({'lead_id': lead_id, 'reason': 'already enrolled'})
                continue
            if not created:
                enrollment.status         = SequenceEnrollmentStatusEnum.ACTIVE
                enrollment.next_step_at   = next_step_at
                enrollment.current_step   = None
                enrollment.completed_at   = None
                enrollment.stopped_reason = None
                enrollment.save()
            enrolled.append(lead_id)

        result = {
            'sequence_id': seq.id,
            'sequence':    seq.name,
            'enrolled':    enrolled,
            'skipped':     skipped,
        }
        try:
            AgentActionLog.objects.create(
                tenant_id=TENANT_ID,
                action_type=AgentActionTypeEnum.ENROLL_SEQUENCE,
                payload_in={'lead_ids': lead_ids, 'sequence_id': seq.id},
                payload_out=result,
                triggered_by='claude-agent',
                status=AgentActionStatusEnum.SUCCESS,
            )
        except Exception as exc:  # noqa: BLE001 — audit write must not fail the action
            logger.error('Failed to write AgentActionLog for bulk enroll: %s', exc)
        return result

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
        # This branch used to read args['enrollment_id'] -- a key the schema
        # does not declare -- so every schema-conforming call raised KeyError.
        # It now implements the contract the schema actually advertises:
        # lead_id (required) + optional sequence_id, omitting it opts the lead
        # out of every sequence.
        lead = _require(Lead, args['lead_id'], 'Lead', tenant_id=TENANT_ID)
        qs = LeadSequenceEnrollment.objects.filter(
            lead_id=lead.id, tenant_id=TENANT_ID,
        ).exclude(status=SequenceEnrollmentStatusEnum.OPTED_OUT)
        sequence_name = None
        if args.get('sequence_id') is not None:
            seq = _require(WhatsAppSequence, args['sequence_id'], 'Sequence',
                           tenant_id=TENANT_ID)
            qs = qs.filter(sequence_id=seq.id)
            sequence_name = seq.name
        enrollment_ids = list(qs.values_list('id', flat=True))
        updated = qs.update(
            status=SequenceEnrollmentStatusEnum.OPTED_OUT,
            stopped_reason='manual unenroll via MCP',
            updated_at=timezone.now(),
        )
        return {
            'lead_id':          lead.id,
            'sequence':         sequence_name,
            'unenrolled_count': updated,
            'enrollment_ids':   enrollment_ids,
        }

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
        group = _require(LeadGroup, args['lead_group_id'], 'Lead group',
                         tenant_id=TENANT_ID)
        campaign = WhatsAppCampaign.objects.create(
            tenant_id=TENANT_ID,
            name=args['name'],
            lead_group_id=group.id,
            template_uid=args['template_uid'],
            template_name=args.get('template_name', ''),
            template_components=args.get('template_components', []),
            scheduled_at=args.get('scheduled_at'),
            created_by=OWNER_USER_ID or TENANT_ID,
        )
        return {'id': campaign.id, 'name': campaign.name, 'status': campaign.status}

    # ── create_and_launch_campaign ──────────────────────────────────────────────
    if name == 'create_and_launch_campaign':
        from django.utils.dateparse import parse_datetime
        from whatsapp_integration.models import AgentActionTypeEnum
        from whatsapp_integration.utils import normalize_msisdn

        campaign_name = str(args.get('name') or '').strip()
        template_uid  = str(args.get('template_uid') or '').strip()
        lead_ids      = args.get('lead_ids') or []
        components    = args.get('template_components') or []
        if not campaign_name:
            raise RuntimeError('name is required')
        if not template_uid:
            raise RuntimeError(
                'template_uid is required — get one from list_whatsapp_templates_detailed')
        if not lead_ids:
            raise RuntimeError('lead_ids must not be empty')

        leads = list(Lead.objects.filter(id__in=lead_ids, tenant_id=TENANT_ID))
        if not leads:
            raise RuntimeError('No leads in this workspace match the given lead_ids')
        contacts = [
            {'phone': normalize_msisdn(lead.phone),
             'name': lead.name or lead.phone,
             'digicrm_lead_id': lead.id}
            for lead in leads if lead.phone
        ]
        if not contacts:
            raise RuntimeError('None of the specified leads have a phone number')

        scheduled_at = args.get('scheduled_at')
        scheduled_dt = parse_datetime(str(scheduled_at)) if scheduled_at else None
        if scheduled_dt is not None and timezone.is_naive(scheduled_dt):
            scheduled_dt = timezone.make_aware(
                scheduled_dt, timezone.get_current_timezone())

        campaign = WhatsAppCampaign.objects.create(
            tenant_id=TENANT_ID,
            name=campaign_name,
            template_uid=template_uid,
            template_components=components,
            status=CampaignStatusEnum.DRAFT,
            scheduled_at=scheduled_dt or timezone.now(),
            total_contacts=len(contacts),
            created_by=OWNER_USER_ID or TENANT_ID,
        )
        try:
            result = _adapter().create_campaign(
                name=campaign_name,
                contacts=contacts,
                template_uid=template_uid,
                template_components=components,
                scheduled_at=scheduled_at,
                digicrm_campaign_id=campaign.id,
            )
        except Exception as exc:  # noqa: BLE001
            campaign.status = CampaignStatusEnum.FAILED
            campaign.save(update_fields=['status', 'updated_at'])
            raise RuntimeError(
                'Campaign %s was created but the WhatsApp gateway rejected the '
                'launch: %s' % (campaign.id, exc))

        campaign.laravel_campaign_uid = result.get('campaign_uid')
        campaign.laravel_group_uid    = result.get('group_uid')
        campaign.status               = CampaignStatusEnum.RUNNING
        campaign.launched_at          = timezone.now()
        campaign.save(update_fields=[
            'laravel_campaign_uid', 'laravel_group_uid', 'status',
            'launched_at', 'updated_at',
        ])

        payload = {
            'campaign_id':          campaign.id,
            'name':                 campaign.name,
            'status':               campaign.status,
            'contacts_count':       len(contacts),
            'scheduled_at':         campaign.scheduled_at,
            'laravel_campaign_uid': campaign.laravel_campaign_uid,
        }
        try:
            AgentActionLog.objects.create(
                tenant_id=TENANT_ID,
                action_type=AgentActionTypeEnum.CREATE_CAMPAIGN,
                payload_in={'name': campaign_name, 'template_uid': template_uid,
                            'lead_ids': lead_ids},
                payload_out=result,
                triggered_by='claude-agent',
                status=AgentActionStatusEnum.SUCCESS,
            )
        except Exception as exc:  # noqa: BLE001 — audit write must not fail the action
            logger.error('Failed to write AgentActionLog for campaign launch: %s', exc)
        return payload

    # ── launch_campaign ─────────────────────────────────────────────────────────
    if name == 'launch_campaign':
        campaign = WhatsAppCampaign.objects.get(id=args['campaign_id'], tenant_id=TENANT_ID)
        if campaign.status != CampaignStatusEnum.DRAFT:
            raise RuntimeError(
                'Campaign %s not in DRAFT (is %s)' % (campaign.id, campaign.status)
            )
        # Defence in depth: create_campaign now validates the group, but a row
        # written before that fix could still point at a foreign group.
        memberships = LeadGroupMembership.objects.filter(
            group_id=campaign.lead_group_id,
            group__tenant_id=TENANT_ID,
            lead__tenant_id=TENANT_ID,
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

    # ── TELEPHONY ───────────────────────────────────────────────────────────────
    # Call history is genuine CRM context ("did anyone ring this lead?").

    # ── list_call_logs ──────────────────────────────────────────────────────────
    if name == 'list_call_logs':
        from telephony.models import CallLog
        qs = CallLog.objects.filter(tenant_id=TENANT_ID)
        if args.get('lead_id'):
            qs = qs.filter(lead_id=args['lead_id'])
        if args.get('direction'):
            qs = qs.filter(direction=args['direction'])
        if args.get('call_type'):
            qs = qs.filter(call_type=args['call_type'])
        if args.get('agent_user_id'):
            qs = qs.filter(agent_user_id=args['agent_user_id'])
        if args.get('date_from'):
            qs = qs.filter(call_time__gte=args['date_from'])
        if args.get('date_to'):
            qs = qs.filter(call_time__lte=args['date_to'])
        page, page_size, offset = _paginate(args)
        total = qs.count()
        rows = list(qs.order_by('-call_time').values(
            'id', 'cmiuid', 'direction', 'call_type', 'from_number', 'to_number',
            'duration', 'billed_sec', 'call_time', 'lead_id', 'agent_user_id',
            'caller_name', 'call_outcome', 'call_outcome_note',
            'call_outcome_set_at', 'is_voicemail', 'hangup_reason',
        )[offset:offset + page_size])
        return {'count': total, 'page': page, 'page_size': page_size, 'results': rows}

    # ── set_call_outcome ────────────────────────────────────────────────────────
    if name == 'set_call_outcome':
        from telephony.models import CallLog
        from telephony.services.analytics_service import OUTCOME_CHOICES
        from telephony.services.call_log_service import set_call_outcome as _set_outcome
        outcome = str(args.get('outcome') or '').strip()
        valid   = [choice[0] for choice in OUTCOME_CHOICES]
        if outcome not in valid:
            raise RuntimeError('outcome must be one of: %s' % ', '.join(valid))
        try:
            call_log = _set_outcome(
                args['call_id'],
                TENANT_ID,
                outcome,
                str(args.get('note') or '')[:512],
                OWNER_USER_ID or None,
            )
        except CallLog.DoesNotExist:
            raise RuntimeError(
                'Call %s not found in this workspace — get valid ids from '
                'list_call_logs.' % args['call_id'])
        return {
            'id':                  call_log.id,
            'call_outcome':        call_log.call_outcome,
            'call_outcome_note':   call_log.call_outcome_note,
            'call_outcome_set_at': call_log.call_outcome_set_at,
            'lead_id':             call_log.lead_id,
        }

    # ── get_telephony_analytics ─────────────────────────────────────────────────
    if name == 'get_telephony_analytics':
        from datetime import timedelta as _timedelta
        from telephony.services.analytics_service import (
            get_team_summary, get_agent_summary,
            get_missed_unattended, get_outcome_breakdown,
        )
        try:
            days = int(args.get('days') or 30)
        except (TypeError, ValueError):
            raise RuntimeError('days must be an integer')
        days      = min(max(days, 1), 365)
        date_to   = timezone.localdate()
        date_from = date_to - _timedelta(days=days - 1)
        return {
            'days':              days,
            'date_from':         date_from,
            'date_to':           date_to,
            'team_summary':      get_team_summary(TENANT_ID, date_from, date_to),
            'agent_summary':     get_agent_summary(TENANT_ID, date_from, date_to),
            'outcome_breakdown': get_outcome_breakdown(TENANT_ID, date_from, date_to),
            'missed_unattended': get_missed_unattended(TENANT_ID),
        }

    # ── PAYMENTS ────────────────────────────────────────────────────────────────
    # Money-touching writes. The MCP HTTP path has no RBAC, so there is
    # deliberately no delete tool — void a record via update_payment instead.

    # ── list_payments ───────────────────────────────────────────────────────────
    if name == 'list_payments':
        from payments.models import Payment
        qs = Payment.objects.filter(tenant_id=TENANT_ID)
        if args.get('lead_id'):
            qs = qs.filter(lead_id=args['lead_id'])
        if args.get('type'):
            qs = qs.filter(type=args['type'])
        if args.get('status'):
            qs = qs.filter(status=args['status'])
        if args.get('date_from'):
            qs = qs.filter(date__gte=args['date_from'])
        if args.get('date_to'):
            qs = qs.filter(date__lte=args['date_to'])
        search = (args.get('search') or '').strip()
        if search:
            qs = qs.filter(Q(reference_no__icontains=search) | Q(notes__icontains=search))
        page, page_size, offset = _paginate(args)
        total = qs.count()
        rows = list(qs.order_by('-date').values(
            'id', 'lead_id', 'lead__name', 'type', 'status', 'amount', 'currency',
            'method', 'reference_no', 'date', 'notes', 'created_at',
        )[offset:offset + page_size])
        return {'count': total, 'page': page, 'page_size': page_size, 'results': rows}

    # ── create_payment ──────────────────────────────────────────────────────────
    if name == 'create_payment':
        from decimal import Decimal, InvalidOperation
        from django.utils.dateparse import parse_datetime
        from payments.models import Payment
        if not OWNER_USER_ID:
            raise RuntimeError('MCP_OWNER_USER_ID env var not set')
        # Confirm the lead is in this tenant before writing a financial record.
        lead = Lead.objects.get(id=args['lead_id'], tenant_id=TENANT_ID)
        try:
            amount = Decimal(str(args['amount']))
        except (InvalidOperation, TypeError, ValueError):
            raise RuntimeError('amount must be a number, e.g. 25000.00')
        paid_at = args.get('date')
        if paid_at:
            paid_at = parse_datetime(str(paid_at))
            if paid_at is None:
                raise RuntimeError('date must be an ISO 8601 datetime, e.g. 2026-09-01T10:30:00Z')
            if timezone.is_naive(paid_at):
                paid_at = timezone.make_aware(paid_at, timezone.get_current_timezone())
        else:
            paid_at = timezone.now()
        payment = Payment.objects.create(
            tenant_id=TENANT_ID,
            owner_user_id=OWNER_USER_ID,
            lead_id=lead.id,
            type=args['type'],
            amount=amount,
            currency=args.get('currency') or 'INR',
            method=args.get('method') or None,
            reference_no=args.get('reference_no') or None,
            notes=args.get('notes') or None,
            date=paid_at,
            status=args.get('status') or 'CLEARED',
        )
        return {
            'id':       payment.id,
            'lead_id':  payment.lead_id,
            'type':     payment.type,
            'status':   payment.status,
            'amount':   payment.amount,
            'currency': payment.currency,
            'date':     payment.date,
        }

    # ── update_payment ──────────────────────────────────────────────────────────
    if name == 'update_payment':
        from decimal import Decimal, InvalidOperation
        from django.utils.dateparse import parse_datetime
        from payments.models import Payment
        payment = Payment.objects.get(id=args['payment_id'], tenant_id=TENANT_ID)
        changed = []
        for field in ('type', 'status', 'currency', 'method', 'reference_no', 'notes'):
            if field in args:
                setattr(payment, field, args[field])
                changed.append(field)
        if 'amount' in args:
            try:
                payment.amount = Decimal(str(args['amount']))
            except (InvalidOperation, TypeError, ValueError):
                raise RuntimeError('amount must be a number, e.g. 25000.00')
            changed.append('amount')
        if 'date' in args and args['date']:
            paid_at = parse_datetime(str(args['date']))
            if paid_at is None:
                raise RuntimeError('date must be an ISO 8601 datetime, e.g. 2026-09-01T10:30:00Z')
            if timezone.is_naive(paid_at):
                paid_at = timezone.make_aware(paid_at, timezone.get_current_timezone())
            payment.date = paid_at
            changed.append('date')
        if not changed:
            raise RuntimeError('Nothing to update — send at least one field besides payment_id')
        payment.save()
        return {'id': payment.id, 'updated': True, 'changed_fields': changed}

    # ── REAL ESTATE ─────────────────────────────────────────────────────────────
    # create_project_interest / create_unit_lead replicate the REST viewsets'
    # perform_create hooks: they call real_estate.services.activity_bridge so the
    # lead's CRM timeline gets its REAL_ESTATE entry. Do not drop those calls.

    # ── list_projects ───────────────────────────────────────────────────────────
    if name == 'list_projects':
        from django.db.models import Count
        from real_estate.models import Project
        qs = Project.objects.filter(tenant_id=TENANT_ID).annotate(
            unit_count=Count('units', distinct=True))
        if args.get('status'):
            qs = qs.filter(status=args['status'])
        if args.get('project_type'):
            qs = qs.filter(project_type=args['project_type'])
        search = (args.get('search') or '').strip()
        if search:
            qs = qs.filter(
                Q(name__icontains=search) |
                Q(city__icontains=search) |
                Q(rera_number__icontains=search)
            )
        page, page_size, offset = _paginate(args)
        total = qs.count()
        rows = list(qs.order_by('-created_at').values(
            'id', 'name', 'project_type', 'status', 'city', 'state',
            'rera_number', 'possession_date', 'unit_count', 'created_at',
        )[offset:offset + page_size])
        return {'count': total, 'page': page, 'page_size': page_size, 'results': rows}

    # ── get_project_summary ─────────────────────────────────────────────────────
    if name == 'get_project_summary':
        from django.db.models import Count
        from real_estate.models import Project, Unit
        project = Project.objects.get(id=args['project_id'], tenant_id=TENANT_ID)
        units = Unit.objects.filter(project_id=project.id, tenant_id=TENANT_ID)

        def _counts(field):
            return {
                (str(row[field]) if row[field] is not None else 'null'): row['count']
                for row in units.values(field).annotate(count=Count('id'))
            }

        return {
            'project': {
                'id':              project.id,
                'name':            project.name,
                'project_type':    project.project_type,
                'status':          project.status,
                'city':            project.city,
                'possession_date': project.possession_date,
            },
            'total_units':            units.count(),
            'unit_counts_by_status':  _counts('status'),
            'unit_counts_by_type':    _counts('unit_type'),
            'unit_counts_by_floor':   _counts('floor_number'),
        }

    # ── list_units ──────────────────────────────────────────────────────────────
    if name == 'list_units':
        from real_estate.models import Unit
        qs = Unit.objects.filter(tenant_id=TENANT_ID)
        if args.get('project_id'):
            qs = qs.filter(project_id=args['project_id'])
        if args.get('block_id'):
            qs = qs.filter(block_id=args['block_id'])
        if args.get('status'):
            qs = qs.filter(status=args['status'])
        if args.get('unit_type'):
            qs = qs.filter(unit_type=args['unit_type'])
        if args.get('floor_number') is not None:
            qs = qs.filter(floor_number=args['floor_number'])
        search = (args.get('search') or '').strip()
        if search:
            qs = qs.filter(
                Q(unit_number__icontains=search) | Q(configuration__icontains=search))
        page, page_size, offset = _paginate(args)
        total = qs.count()
        rows = list(qs.order_by('project_id', 'unit_number').values(
            'id', 'unit_number', 'project_id', 'project__name', 'block_id',
            'block__name', 'unit_type', 'configuration', 'floor_number', 'facing',
            'carpet_area_sqft', 'built_up_area_sqft', 'total_price', 'status',
        )[offset:offset + page_size])
        return {'count': total, 'page': page, 'page_size': page_size, 'results': rows}

    # ── create_project_interest ─────────────────────────────────────────────────
    if name == 'create_project_interest':
        from decimal import Decimal, InvalidOperation
        from real_estate.models import Project, ProjectInterest
        from real_estate.services.activity_bridge import log_project_interest_activity
        lead    = Lead.objects.get(id=args['lead_id'], tenant_id=TENANT_ID)
        project = Project.objects.get(id=args['project_id'], tenant_id=TENANT_ID)

        def _money(key):
            if args.get(key) is None:
                return None
            try:
                return Decimal(str(args[key]))
            except (InvalidOperation, TypeError, ValueError):
                raise RuntimeError('%s must be a number' % key)

        interest, created = ProjectInterest.objects.get_or_create(
            project_id=project.id,
            lead_id=lead.id,
            defaults={
                'tenant_id':           TENANT_ID,
                'budget_min':          _money('budget_min'),
                'budget_max':          _money('budget_max'),
                'preferred_unit_type': args.get('preferred_unit_type') or None,
                'notes':               args.get('notes') or None,
            },
        )
        if created:
            # Mirrors ProjectInterestViewSet.perform_create — without this the
            # lead's timeline silently loses the REAL_ESTATE entry.
            log_project_interest_activity(interest, actor_user_id=OWNER_USER_ID or None)
        return {
            'id':         interest.id,
            'created':    created,
            'lead_id':    interest.lead_id,
            'project_id': interest.project_id,
            'project':    project.name,
        }

    # ── create_unit_lead ────────────────────────────────────────────────────────
    if name == 'create_unit_lead':
        from real_estate.models import Unit, UnitLead
        from real_estate.services.activity_bridge import log_unit_lead_activity
        lead = Lead.objects.get(id=args['lead_id'], tenant_id=TENANT_ID)
        unit = Unit.objects.select_related('project').get(
            id=args['unit_id'], tenant_id=TENANT_ID)
        unit_lead, created = UnitLead.objects.get_or_create(
            unit_id=unit.id,
            lead_id=lead.id,
            defaults={
                'tenant_id':     TENANT_ID,
                'relation_type': args['relation_type'],
                'notes':         args.get('notes') or None,
            },
        )
        if created:
            # Mirrors UnitLeadViewSet.perform_create — without this the lead's
            # timeline silently loses the REAL_ESTATE entry.
            log_unit_lead_activity(unit_lead, actor_user_id=OWNER_USER_ID or None)
        elif unit_lead.relation_type != args['relation_type']:
            previous = unit_lead.relation_type
            unit_lead.relation_type = args['relation_type']
            if args.get('notes'):
                unit_lead.notes = args['notes']
            unit_lead.save(update_fields=['relation_type', 'notes', 'updated_at'])
            # Mirrors UnitLeadViewSet.perform_update's relation-change logging.
            log_unit_lead_activity(
                unit_lead,
                actor_user_id=OWNER_USER_ID or None,
                previous_relation_type=previous,
            )
        return {
            'id':            unit_lead.id,
            'created':       created,
            'lead_id':       unit_lead.lead_id,
            'unit_id':       unit_lead.unit_id,
            'unit_number':   unit.unit_number,
            'project':       unit.project.name,
            'relation_type': unit_lead.relation_type,
        }

    # ── update_unit_status ──────────────────────────────────────────────────────
    if name == 'update_unit_status':
        from real_estate.models import Unit
        unit = Unit.objects.get(id=args['unit_id'], tenant_id=TENANT_ID)
        unit.status = args['status']
        unit.save(update_fields=['status', 'updated_at'])
        return {
            'id':          unit.id,
            'unit_number': unit.unit_number,
            'status':      unit.status,
            'project_id':  unit.project_id,
        }

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
        except KeyError as exc:
            # A missing required argument surfaces as KeyError, whose str() is
            # just the quoted key name -- useless to the model. Name it.
            logger.exception('Tool %s failed', tool_name)
            key = exc.args[0] if exc.args else exc
            return {'jsonrpc': '2.0', 'id': req_id,
                    'error': {'code': -32602,
                              'message': 'Missing required argument: %s' % key}}
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
    return _cors(JsonResponse({'status': 'ok', 'server': 'digicrm-mcp', 'tools': tool_count}), request)


@csrf_exempt
def mcp_sse(request):
    if request.method == 'OPTIONS':
        return _cors(HttpResponse(), request)
    if not _check_auth(request):
        logger.warning('MCP auth FAILED method=%s', request.method)
        return _cors(JsonResponse({'error': 'Unauthorized'}, status=401), request)

    logger.info('MCP SSE: method=%s', request.method)

    if request.method == 'POST':
        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            return _cors(JsonResponse({'error': 'Invalid JSON'}, status=400), request)
        method = body.get('method', '?')
        logger.info('MCP POST: method=%s id=%s', method, body.get('id'))
        if body.get('id') is None and method.startswith('notifications/'):
            return _cors(HttpResponse(status=202), request)
        try:
            result = _handle_mcp_request(body)
            logger.info('MCP POST: %s ok', method)
            return _cors(JsonResponse(result, safe=False), request)
        except Exception as exc:
            logger.exception('MCP POST %s FAILED', method)
            return _cors(JsonResponse({'jsonrpc': '2.0', 'id': body.get('id'),
                'error': {'code': -32603, 'message': str(exc)}}, status=500), request)

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
    return _cors(response, request)


@csrf_exempt
def mcp_message(request):
    if request.method == 'OPTIONS':
        return _cors(HttpResponse(), request)
    if request.method != 'POST':
        return HttpResponse(status=405)
    if not _check_auth(request):
        return _cors(JsonResponse({'error': 'Unauthorized'}, status=401), request)
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return _cors(JsonResponse({'error': 'Invalid JSON'}, status=400), request)
    method = body.get('method', '?')
    logger.info('MCP message: method=%s', method)
    try:
        result = _handle_mcp_request(body)
        return _cors(JsonResponse(result, safe=False), request)
    except Exception as exc:
        logger.exception('MCP message %s FAILED', method)
        return _cors(JsonResponse({'error': str(exc)}, status=500), request)


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
