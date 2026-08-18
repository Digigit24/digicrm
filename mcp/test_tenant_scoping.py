#!/usr/bin/env python3
"""
DigiCRM MCP -- tenant-isolation and auth tests (offline, NO database required).

Covers audit findings A4, A5, M4 and improvement item P0-7 of
`_plans/03-whatsapp-audit-2026-08.md`.

Why this file exists as well as `test_http.py`: `test_http.py` needs a live
server and a live database, so it cannot run in CI or on a dev box with no DB.
The cross-tenant guarantees are the highest-severity thing in this layer, so
they get a test that always runs. Model managers are replaced with in-memory
fakes, which means the real dispatch branch is executed and the exact scope
kwargs it passes to `_require()` are asserted -- without touching a database.

Usage:
    python mcp/test_tenant_scoping.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Must be set before mcp.django_view is imported: it reads them at module scope.
TENANT_A = '11111111-1111-1111-1111-111111111111'
TENANT_B = '22222222-2222-2222-2222-222222222222'
os.environ['DIGICRM_TENANT_ID'] = TENANT_A
os.environ['MCP_OWNER_USER_ID'] = '33333333-3333-3333-3333-333333333333'
os.environ['MCP_SECRET'] = 'unit-test-secret'
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'digicrm.settings')

import django  # noqa: E402
django.setup()

from django.test import RequestFactory  # noqa: E402

import mcp.django_view as dv  # noqa: E402

GREEN, RED, YELLOW, BOLD, RESET = (
    '\033[92m', '\033[91m', '\033[93m', '\033[1m', '\033[0m')

_results = {'passed': 0, 'failed': 0}


def check(label, cond, detail=''):
    if cond:
        _results['passed'] += 1
        print('  %sPASS%s  %s' % (GREEN, RESET, label))
    else:
        _results['failed'] += 1
        print('  %sFAIL%s  %s  %s' % (RED, RESET, label, detail))


def expect_raises(label, fn, must_contain=None):
    """Assert fn() raises, optionally with a readable message."""
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if must_contain and must_contain.lower() not in msg.lower():
            check(label, False, 'raised but message was %r' % msg)
        else:
            check(label, True)
            print('        raised: %s' % msg)
        return
    check(label, False, 'did NOT raise -- cross-tenant write would succeed')


# ---------------------------------------------------------------------------
# In-memory fakes. A row carries the scope predicate it satisfies, so a
# `.filter(pk=..., sequence__tenant_id=...)` is matched literally -- which is
# exactly the assertion we want: the branch must pass the tenant predicate.
# ---------------------------------------------------------------------------

class FakeRow:
    def __init__(self, pk, scope, **attrs):
        self.pk = pk
        self.id = pk
        self._scope = scope
        for k, v in attrs.items():
            setattr(self, k, v)


class FakeQS:
    def __init__(self, rows):
        self._rows = list(rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def delete(self):
        n = len(self._rows)
        self._rows = []
        return n, {}


class FakeManager:
    def __init__(self, rows):
        self.rows = rows
        self.filter_calls = []

    def filter(self, pk=None, id=None, **scope):
        key = pk if pk is not None else id
        self.filter_calls.append(dict(scope, pk=key))
        return FakeQS([
            r for r in self.rows
            if (key is None or r.pk == key)
            and all(r._scope.get(k) == v for k, v in scope.items())
        ])

    def get(self, id=None, pk=None, **scope):
        row = self.filter(pk=(pk if pk is not None else id), **scope).first()
        if row is None:
            raise LookupError('not found')
        return row


class FakeModel:
    """Stands in for a Django model class in a patched module."""

    DoesNotExist = LookupError

    def __init__(self, rows):
        self.objects = FakeManager(rows)


def dispatch(tool, args):
    return dv._dispatch_tool(tool, args)


print('\n%s%sDigiCRM MCP -- tenant isolation & auth (offline)%s' % (BOLD, YELLOW, RESET))
print('%stenant under test : %s%s' % (YELLOW, TENANT_A, RESET))
print('%sforeign tenant    : %s%s\n' % (YELLOW, TENANT_B, RESET))


# ---------------------------------------------------------------------------
# 1. _require() itself
# ---------------------------------------------------------------------------
print('%s-- _require() helper%s' % (BOLD, RESET))

mine = FakeModel([FakeRow(1, {'tenant_id': TENANT_A})])
theirs = FakeModel([FakeRow(99, {'tenant_id': TENANT_B})])

check('returns the row when the tenant matches',
      dv._require(mine, 1, 'Thing', tenant_id=TENANT_A).pk == 1)
expect_raises('rejects a row owned by another tenant',
              lambda: dv._require(theirs, 99, 'Thing', tenant_id=TENANT_A),
              'does not exist in this workspace')
expect_raises('rejects an id that exists nowhere',
              lambda: dv._require(mine, 12345, 'Thing', tenant_id=TENANT_A),
              'does not exist in this workspace')
expect_raises('rejects a missing id', lambda: dv._require(mine, None, 'Thing'),
              'is required')


# ---------------------------------------------------------------------------
# 2. A5 -- the four tools the audit named
# ---------------------------------------------------------------------------
print('\n%s-- A5: the four cross-tenant write tools%s' % (BOLD, RESET))

import crm.models as crm_models                      # noqa: E402
import whatsapp_integration.models as wa_models      # noqa: E402

_orig = {
    'Lead': crm_models.Lead,
    'LeadGroup': crm_models.LeadGroup,
    'LeadStatus': crm_models.LeadStatus,
    'WhatsAppSequence': wa_models.WhatsAppSequence,
    'WhatsAppSequenceStep': wa_models.WhatsAppSequenceStep,
}

# Tenant B's rows. Each declares the scope predicate that WOULD match it.
foreign_lead = FakeModel([FakeRow(99, {'tenant_id': TENANT_B}, name='B lead')])
foreign_group = FakeModel([FakeRow(99, {'tenant_id': TENANT_B}, name='B group')])
foreign_status = FakeModel([FakeRow(99, {'tenant_id': TENANT_B})])
# WhatsAppSequenceStep has no tenant_id of its own (audit M4) -- it can only be
# scoped by joining to its sequence.
foreign_step = FakeModel([FakeRow(99, {'sequence__tenant_id': TENANT_B})])
own_sequence = FakeModel([FakeRow(7, {'tenant_id': TENANT_A}, name='A seq')])

crm_models.Lead = foreign_lead
crm_models.LeadGroup = foreign_group
crm_models.LeadStatus = foreign_status
wa_models.WhatsAppSequenceStep = foreign_step
wa_models.WhatsAppSequence = own_sequence

expect_raises(
    "update_sequence_step refuses another tenant's step_id",
    lambda: dispatch('update_sequence_step',
                     {'sequence_id': 7, 'step_id': 99,
                      'template_uid': 'attacker-template'}),
    'sequence step')

expect_raises(
    "delete_sequence_step refuses another tenant's step_id",
    lambda: dispatch('delete_sequence_step',
                     {'sequence_id': 7, 'step_id': 99}),
    'sequence step')

expect_raises(
    "enroll_lead_in_sequence refuses another tenant's lead_id",
    lambda: dispatch('enroll_lead_in_sequence', {'lead_id': 99, 'sequence_id': 7}),
    'lead')

expect_raises(
    "create_campaign refuses another tenant's lead_group_id",
    lambda: dispatch('create_campaign', {
        'name': 'x', 'lead_group_id': 99, 'template_uid': 'u'}),
    'lead group')

# The step lookup must join through the sequence, not filter a (nonexistent)
# tenant_id column on the step table -- this is the M4 half of the fix.
step_scopes = [c for c in foreign_step.objects.filter_calls]
check('sequence step is scoped via sequence__tenant_id (audit M4)',
      any('sequence__tenant_id' in c for c in step_scopes), str(step_scopes))
check('sequence step is NOT scoped by a bare tenant_id column',
      all('tenant_id' not in c for c in step_scopes), str(step_scopes))


# ---------------------------------------------------------------------------
# 3. Same pattern, found by auditing the other 74 tools
# ---------------------------------------------------------------------------
print('\n%s-- Same pattern in tools the audit did not name%s' % (BOLD, RESET))

expect_raises(
    "add_sequence_step refuses another tenant's sequence_id",
    lambda: dispatch('add_sequence_step', {
        'sequence_id': 4242, 'step_number': 1, 'template_uid': 'u'}),
    'sequence')

expect_raises(
    "add_lead_to_group refuses another tenant's lead_id",
    lambda: dispatch('add_lead_to_group', {'lead_id': 99, 'lead_group_id': 99}),
    'lead')

expect_raises(
    "create_lead_activity refuses another tenant's lead_id",
    lambda: dispatch('create_lead_activity',
                     {'lead_id': 99, 'type': 'NOTE', 'content': 'x'}),
    'lead')

expect_raises(
    "create_task refuses another tenant's lead_id",
    lambda: dispatch('create_task', {'lead_id': 99, 'title': 'x'}),
    'lead')

# NOTE: create_meeting's lead scoping is asserted in section 3b instead. Its
# dispatch branch imports MeetingAttendee before it reaches the lead check, so
# it can only run where the calendar-backend schema is present.

# update_lead_status: the lead lookup is already tenant-scoped, so give it a
# lead it CAN find and prove the status_id is what gets rejected.
crm_models.Lead = FakeModel([FakeRow(1, {'tenant_id': TENANT_A}, status_id=None)])
expect_raises(
    "update_lead_status refuses another tenant's status_id",
    lambda: dispatch('update_lead_status', {'lead_id': 1, 'status_id': 99}),
    'lead status')

for k, v in _orig.items():
    setattr(crm_models if hasattr(crm_models, k) else wa_models, k, v)


# ---------------------------------------------------------------------------
# 3b. Calendar meeting tools (calendar-backend schema)
# ---------------------------------------------------------------------------
print('\n%s-- Meeting tools against the calendar-backend schema%s' % (BOLD, RESET))

# These tools reference Meeting columns and helpers that land on the
# Digigit24/calendar-backend branch (MeetingAttendee, is_deleted, timezone,
# recurrence_rule, meetings.recurrence). Until that branch is merged this
# worktree still has the old model, so skip rather than report a false failure.
try:
    from meetings.models import MeetingAttendee  # noqa: F401
    from meetings import recurrence  # noqa: F401
    _CALENDAR_SCHEMA = True
except ImportError as _exc:
    _CALENDAR_SCHEMA = False
    print('  %sSKIP%s  calendar-backend schema not present here (%s).' % (
        YELLOW, RESET, _exc))
    print('        These cases run once Digigit24/calendar-backend is merged.')

if _CALENDAR_SCHEMA:
    import meetings.models as mtg_models

    _orig_meeting = mtg_models.Meeting
    _orig_lead = crm_models.Lead

    START = '2026-09-01T10:00:00Z'
    END = '2026-09-01T11:00:00Z'

    def base(**over):
        out = {'title': 'probe', 'start_time': START, 'end_time': END}
        out.update(over)
        return out

    # -- tenant scoping on every id these tools accept --------------------
    crm_models.Lead = FakeModel([FakeRow(99, {'tenant_id': TENANT_B})])
    expect_raises("create_meeting refuses another tenant's lead_id",
                  lambda: dispatch('create_meeting', base(lead_id=99)), 'lead')

    expect_raises("create_meeting refuses another tenant's attendee lead_id",
                  lambda: dispatch('create_meeting',
                                   base(attendees=[{'lead_id': 99}])),
                  'attendee lead')

    # A foreign meeting_id, and a soft-deleted one, must both read as gone.
    mtg_models.Meeting = FakeModel([FakeRow(99, {'tenant_id': TENANT_B,
                                                 'is_deleted': False})])
    expect_raises("update_meeting refuses another tenant's meeting_id",
                  lambda: dispatch('update_meeting',
                                   {'meeting_id': 99, 'title': 'x'}),
                  'meeting')

    deleted = FakeModel([FakeRow(5, {'tenant_id': TENANT_A, 'is_deleted': True})])
    mtg_models.Meeting = deleted
    expect_raises('update_meeting refuses a soft-deleted meeting',
                  lambda: dispatch('update_meeting',
                                   {'meeting_id': 5, 'title': 'x'}),
                  'meeting')
    check('update_meeting scopes on is_deleted=False',
          any(c.get('is_deleted') is False for c in deleted.objects.filter_calls),
          str(deleted.objects.filter_calls))

    # -- attendee identity and argument validation ------------------------
    crm_models.Lead = FakeModel([FakeRow(1, {'tenant_id': TENANT_A})])
    expect_raises('create_meeting rejects a malformed attendee user_id',
                  lambda: dispatch('create_meeting',
                                   base(attendees=[{'user_id': 'not-a-uuid'}])),
                  'user uuid')
    expect_raises('create_meeting rejects an attendee with no identity',
                  lambda: dispatch('create_meeting',
                                   base(attendees=[{'display_name': 'Nobody'}])),
                  'at least one of')
    expect_raises('create_meeting refuses ORGANIZER as an attendee role',
                  lambda: dispatch('create_meeting',
                                   base(attendees=[{'email': 'a@b.c',
                                                    'role': 'ORGANIZER'}])),
                  'required or optional')
    expect_raises('create_meeting rejects end_time before start_time',
                  lambda: dispatch('create_meeting',
                                   base(start_time=END, end_time=START)),
                  'end_time')
    expect_raises('create_meeting rejects an unparseable start_time',
                  lambda: dispatch('create_meeting',
                                   base(start_time='next tuesday')),
                  'iso 8601')
    expect_raises('create_meeting rejects an unknown IANA timezone',
                  lambda: dispatch('create_meeting',
                                   base(timezone='Mars/Olympus_Mons')),
                  'timezone')
    expect_raises('create_meeting rejects a malformed rrule',
                  lambda: dispatch('create_meeting', base(rrule='every monday')),
                  'freq=')

    mtg_models.Meeting = _orig_meeting
    crm_models.Lead = _orig_lead

    # -- recurrence helpers are the calendar-backend ones, not a second
    #    private RRULE parser --------------------------------------------
    from datetime import datetime, timezone as _dtz
    dtstart = datetime(2026, 9, 1, 10, 0, tzinfo=_dtz.utc)
    rule, end = dv._meeting_recurrence('RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=3',
                                       dtstart, 'Asia/Kolkata')
    check('rrule "RRULE:" prefix is stripped', rule.startswith('FREQ='), repr(rule))
    check('COUNT= resolves a concrete recurrence_end_at', end is not None, repr(end))
    rule2, end2 = dv._meeting_recurrence('FREQ=DAILY', dtstart, 'UTC')
    check('an open-ended rule leaves recurrence_end_at null', end2 is None, repr(end2))
    check('no rrule means no recurrence', dv._meeting_recurrence(None, dtstart, 'UTC')
          == (None, None))


# ---------------------------------------------------------------------------
# 3c. Declared-vs-implemented contract for the meeting tools
# ---------------------------------------------------------------------------
print('\n%s-- Every declared meeting property is actually written%s' % (BOLD, RESET))

import re as _re  # noqa: E402
from mcp.server import TOOLS as _TOOLS  # noqa: E402

_src = open(dv.__file__.replace('.pyc', '.py'), encoding='utf-8').read()
_lines = _src.split('\n')
_starts = [(i, m.group(1)) for i, l in enumerate(_lines)
           for m in [_re.match(r"    if name == '([a-z_]+)':", l)] if m]
_starts.append((len(_lines), None))
_bodies = {n: '\n'.join(_lines[ln:_starts[i + 1][0]])
           for i, (ln, n) in enumerate(_starts[:-1])}
_helpers = _src[:_src.index('def _dispatch_tool')]

# Applied to ALL tools, not just the meeting ones: a property that is declared
# in inputSchema but never read by the dispatch branch is silently discarded,
# and the model has no way to find out. That is the bug class that produced
# create_meeting(location=...) going nowhere.
_all_orphans = []
for _tool_def in _TOOLS:
    _body = _bodies.get(_tool_def['name'], '')
    for _prop in _tool_def['inputSchema']['properties']:
        if ("'%s'" % _prop) in _body or ("'%s'" % _prop) in _helpers:
            continue
        _all_orphans.append('%s.%s' % (_tool_def['name'], _prop))
check('no tool declares a property its dispatch branch ignores',
      not _all_orphans, 'orphaned: %s' % _all_orphans)

_missing_req = ['%s.%s' % (t['name'], k) for t in _TOOLS
                for k in (t['inputSchema'].get('required') or [])
                if k not in t['inputSchema']['properties']]
check('every required key is a declared property', not _missing_req, str(_missing_req))

for _tool_name in ('create_meeting', 'update_meeting'):
    _tool_def = next(t for t in _TOOLS if t['name'] == _tool_name)
    _body = _bodies[_tool_name]
    _orphans = [
        prop for prop in _tool_def['inputSchema']['properties']
        if ("'%s'" % prop) not in _body and ("'%s'" % prop) not in _helpers
    ]
    check('%s writes every property it declares' % _tool_name,
          not _orphans, 'declared but never read: %s' % _orphans)

check('create_meeting no longer declares attendees as a list of strings',
      next(t for t in _TOOLS if t['name'] == 'create_meeting')
      ['inputSchema']['properties']['attendees']['items']['type'] == 'object')

for _tool_name in ('list_meetings', 'get_meetings_calendar', 'get_sales_dashboard'):
    check('%s hides soft-deleted meetings' % _tool_name,
          'is_deleted=False' in _bodies[_tool_name],
          'no is_deleted filter found')


# ---------------------------------------------------------------------------
# 4. A4 / P0-7 -- auth and CORS
# ---------------------------------------------------------------------------
print('\n%s-- A4 / P0-7: auth transport and CORS%s' % (BOLD, RESET))

rf = RequestFactory()
SECRET = 'unit-test-secret'
dv.MCP_SECRET = SECRET

check('Bearer header with the right secret is accepted',
      dv._check_auth(rf.post('/mcp/sse', HTTP_AUTHORIZATION='Bearer ' + SECRET)))
check('Bearer header with a wrong secret is rejected',
      not dv._check_auth(rf.post('/mcp/sse', HTTP_AUTHORIZATION='Bearer nope')))
check('no Authorization header is rejected',
      not dv._check_auth(rf.post('/mcp/sse')))
check('?secret= query parameter is NO LONGER accepted',
      not dv._check_auth(rf.post('/mcp/sse?secret=' + SECRET)))
check('?secret= is rejected even when it holds the correct value',
      not dv._check_auth(rf.post('/mcp/sse?secret=' + SECRET + '&x=1')))

dv.MCP_SECRET = ''
check('blank MCP_SECRET fails closed (A4)',
      not dv._check_auth(rf.post('/mcp/sse', HTTP_AUTHORIZATION='Bearer ' + SECRET)))
dv.MCP_SECRET = SECRET

from django.http import HttpResponse  # noqa: E402

allowed = dv.MCP_ALLOWED_ORIGINS[0]
r = dv._cors(HttpResponse(), rf.post('/mcp/sse', HTTP_ORIGIN=allowed))
check('allow-listed Origin is echoed back',
      r.get('Access-Control-Allow-Origin') == allowed,
      repr(r.get('Access-Control-Allow-Origin')))

r = dv._cors(HttpResponse(), rf.post('/mcp/sse', HTTP_ORIGIN='https://evil.example'))
check('unknown Origin gets NO Access-Control-Allow-Origin header',
      r.get('Access-Control-Allow-Origin') is None,
      repr(r.get('Access-Control-Allow-Origin')))

r = dv._cors(HttpResponse(), rf.post('/mcp/sse'))
check('no Origin (non-browser client) gets no ACAO header',
      r.get('Access-Control-Allow-Origin') is None,
      repr(r.get('Access-Control-Allow-Origin')))
check('Vary: Origin is always set (cache safety)', r.get('Vary') == 'Origin')

check('wildcard CORS is gone',
      '*' not in [dv._cors(HttpResponse(), rf.post('/mcp/sse')).get(
          'Access-Control-Allow-Origin')])


# ---------------------------------------------------------------------------
print('\n%s%s%s' % (BOLD, '-' * 55, RESET))
print('%sResults: %s%d passed%s  %s%d failed%s%s\n' % (
    BOLD, GREEN, _results['passed'], RESET,
    RED, _results['failed'], RESET, RESET))
sys.exit(1 if _results['failed'] else 0)
