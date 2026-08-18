#!/usr/bin/env python3
"""
DigiCRM Sales Agent MCP Server — tool catalog (TOOLS)

Registers 78 tools for a Claude sales agent to interact with DigiCRM:
  CRM core        (31) — leads, groups, statuses, tasks, activities, meetings
  WhatsApp        (12) — send, chat, templates, inbox ops, AI context
  Automation      (18) — sequences, steps, enrollments, campaigns
  Discovery reads (5)  — dashboard, kanban, follow-ups, phone lookup, audit log
  Telephony       (3)  — call history, disposition, analytics
  Payments        (3)  — list / create / update payment records
  Real estate     (6)  — projects, units, project interests, unit leads

The authoritative count is always ``len(TOOLS)``; ``GET /mcp/health`` reports it.

-----------------------------------------------------------------------------
SUPPORTED EXECUTION PATH — HTTP only
-----------------------------------------------------------------------------
The only supported dispatcher is ``mcp/django_view.py::_dispatch_tool``, served
at ``POST /mcp/sse`` (and ``/mcp/message``). It runs inside Django and hits the
ORM directly. New tools are implemented there, and only there.

This module still owns ``TOOLS`` — ``django_view.py`` imports it verbatim for
``tools/list`` — so registering a tool schema here is step 1 of 2 for every new
tool.

.. deprecated::
   ``_dispatch`` / ``execute_tool`` / ``run_stdio`` below (the stdio transport
   that proxies to the REST API through ``mcp/client.py``) are DEPRECATED and
   unmaintained. They are kept only so existing Claude Desktop stdio configs do
   not hard-crash. They cover the original 39 tools only; every tool added after
   2026-08-19 is HTTP-only and ``_dispatch`` will raise "Unknown tool" for it.
   Do NOT add new tools to ``_dispatch``. Do not delete it without first
   migrating any remaining stdio consumers to the HTTP endpoint.

Usage (deprecated stdio transport):
  python -m mcp.server

Required env vars for the deprecated stdio path (see mcp/config.py):
  DIGICRM_BASE_URL, DIGICRM_JWT_TOKEN, DIGICRM_TENANT_ID

Optional:
  WA_VENDOR_UID, WA_API_TOKEN, WA_BASE_URL  (for WhatsApp tools)
"""

import io
import json
import logging
import sys
import os
from typing import Any

# ---------------------------------------------------------------------------
# Bootstrap: add digicrm project root to path so 'mcp.config' resolves
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp import config, client
from mcp.client import McpApiError

try:
    from mcp.sdk import MCPServer, tool  # type: ignore
except ImportError:
    # Fallback: use the official 'mcp' PyPI package (pip install mcp)
    try:
        from mcp.server import Server as MCPServer  # type: ignore
        from mcp.server.stdio import stdio_server   # type: ignore
        from mcp.types import Tool, TextContent      # type: ignore
        _USE_OFFICIAL_SDK = True
    except ImportError:
        _USE_OFFICIAL_SDK = False
        MCPServer = None

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, config.MCP_LOG_LEVEL, logging.INFO),
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    stream=sys.stderr,
)
logger = logging.getLogger('digicrm.mcp')


# ===========================================================================
# TOOL DEFINITIONS
# ===========================================================================

TOOLS: list[dict] = []

def _tool(name: str, description: str, properties: dict, required: list = None):
    """Register a tool definition."""
    TOOLS.append({
        'name': name,
        'description': description,
        'inputSchema': {
            'type': 'object',
            'properties': properties,
            'required': required or [],
        }
    })


# ---------------------------------------------------------------------------
# PHASE 1 — CRM CORE (10 tools)  + read tools
# ---------------------------------------------------------------------------

_tool('list_leads', """
Search, filter and list leads in the CRM. This is the advanced lead search.

Returns a paginated list with id, name, phone, email, status, priority,
lead_score, source, city, assigned_to, next_follow_up_at and created_at.
Combine any of the filters below; they are ANDed together.
Resolve status_id / status_ids via list_lead_statuses, lead_group_id via
list_lead_groups, and assigned_to via list_users. To find one lead from a phone
number use lookup_lead_by_phone instead.
""", {
    'search':                {'type': 'string',  'description': 'Filter by name, phone, or email (partial match)'},
    'assigned_to':           {'type': 'string',  'description': 'User UUID - only return leads assigned to this user. Resolve names via list_users.'},
    'unassigned':            {'type': 'boolean', 'description': 'If true, only return leads with no assigned user. Ignored when assigned_to is set.'},
    'status_id':             {'type': 'integer', 'description': 'Only leads in this pipeline stage. Get ids from list_lead_statuses.'},
    'status_ids':            {'type': 'array',   'items': {'type': 'integer'},
                              'description': 'Only leads in any of these pipeline stages. Overrides status_id when both are sent.'},
    'priority':              {'type': 'string',  'enum': ['LOW', 'MEDIUM', 'HIGH'],
                              'description': 'Only leads at this priority'},
    'lead_score_min':        {'type': 'integer', 'description': 'Minimum lead_score (0-100), inclusive'},
    'lead_score_max':        {'type': 'integer', 'description': 'Maximum lead_score (0-100), inclusive'},
    'created_after':         {'type': 'string',  'description': 'Only leads created at/after this ISO 8601 datetime'},
    'created_before':        {'type': 'string',  'description': 'Only leads created at/before this ISO 8601 datetime'},
    'next_follow_up_before': {'type': 'string',  'description': 'Only leads whose next_follow_up_at is at/before this ISO 8601 datetime (use for "follow-ups due")'},
    'city':                  {'type': 'string',  'description': 'Filter by city (partial match)'},
    'lead_group_id':         {'type': 'integer', 'description': 'Only leads belonging to this group. Get ids from list_lead_groups.'},
    'ordering':              {'type': 'string',
                              'enum': ['created_at', '-created_at', 'name', '-name',
                                       'lead_score', '-lead_score',
                                       'next_follow_up_at', '-next_follow_up_at',
                                       'updated_at', '-updated_at'],
                              'description': 'Sort order (prefix with - for descending). Default -created_at.'},
    'page':                  {'type': 'integer', 'description': 'Page number (default 1)'},
    'page_size':             {'type': 'integer', 'description': 'Results per page (default 20, max 100)'},
})

_tool('lookup_lead_by_phone', """
Find the single CRM lead that owns a phone number.

Matches on the last 10 significant digits, so +91XXXXXXXXXX, 0XXXXXXXXXX and a
bare 10-digit number all resolve to the same lead.
Returns {found, id, name, phone, status} — found is false when nothing matches.
Use this to turn an inbound caller or WhatsApp number into a lead_id for
get_lead, append_lead_note, create_task and the WhatsApp tools.
""", {
    'phone': {'type': 'string', 'description': 'Phone number in any format (E.164, 0-prefixed, or bare digits)'},
}, ['phone'])

_tool('get_sales_dashboard', """
Get a compact sales overview for the whole workspace.

Returns totals (lead count, high-priority count, follow-ups due today,
estimated pipeline value), lead counts broken down by status and by priority,
the 5 newest leads, the 5 next open tasks, the 5 next upcoming meetings and the
8 most recent lead activities.
Takes no arguments. Use it to orient at the start of a session before drilling
in with list_leads / list_tasks / list_meetings.
""", {})

_tool('get_lead_kanban', """
Get leads grouped by pipeline stage, in board order.

Returns one entry per active status (id, name, color_hex, order_index, is_won,
is_lost, lead_count) each with a capped list of its leads. lead_count is the
true total for the stage even when the leads list is truncated by
limit_per_status.
Pass status_id to fetch a single column. Status ids come from
list_lead_statuses.
""", {
    'status_id':        {'type': 'integer', 'description': 'Only return this one status column. Get ids from list_lead_statuses.'},
    'limit_per_status': {'type': 'integer', 'description': 'Max leads returned per status (default 20, max 100)'},
})

_tool('get_lead_follow_up', """
Get the follow-up schedule for one lead.

Returns next_follow_up_at and last_contacted_at plus the active reminder
(id, remind_at, offset_minutes, status) if one is set, or null.
Get lead_id from list_leads or lookup_lead_by_phone.
Use set_lead_follow_up to change the schedule.
""", {
    'lead_id': {'type': 'integer', 'description': 'ID of the lead'},
}, ['lead_id'])

_tool('get_lead', """
Get full details of a single lead by ID.

Returns all fields: name, phone, email, company, title, status, priority,
lead_score, source, notes, assigned_to, metadata, address, timestamps.
""", {
    'lead_id': {'type': 'integer', 'description': 'ID of the lead'},
}, ['lead_id'])

_tool('list_lead_statuses', """
List all available lead status options for this workspace.

Returns id, name, color, and order for each status.
Use status id when calling update_lead_status.
""", {})

_tool('create_lead', """
Create a new CRM lead.

Required: name, phone
Optional: email, source, lead_score, notes, assigned_to (user UUID),
          custom_fields (dict of field_key → value)

Returns the created lead object with its id.
""", {
    'name':          {'type': 'string', 'description': 'Full name of the lead'},
    'phone':         {'type': 'string', 'description': 'Phone number (10-digit Indian or full E.164)'},
    'email':         {'type': 'string', 'description': 'Email address'},
    'source':        {'type': 'string', 'description': 'Lead source (e.g. "website", "referral", "meta_ad")'},
    'lead_score':    {'type': 'integer', 'description': 'Score 0-100'},
    'notes':         {'type': 'string', 'description': 'Initial notes'},
    'assigned_to':   {'type': 'string', 'description': 'UUID of the user to assign this lead to'},
    'custom_fields': {'type': 'object', 'description': 'Dict of custom field key → value'},
}, ['name', 'phone'])

_tool('get_lead_field_schema', """
Get the lead field schema configured for this workspace.

Returns standard_fields and custom_fields, each with field_name, field_label,
field_type, is_required, is_visible, options (for dropdowns) and display_order.
Takes no arguments.
Call this before create_lead(custom_fields=…) or
bulk_import_leads(custom_fields=…) so you use real field_name keys instead of
guessing them.
""", {})

_tool('update_lead', """
Update an existing lead's fields.

All fields except lead_id are optional — only send what you want to change.
Every field you send OVERWRITES the stored value. To add to the notes without
erasing existing content, use append_lead_note instead of update_lead(notes=…).
To change the pipeline stage use update_lead_status; to change the follow-up
date use set_lead_follow_up.
""", {
    'lead_id':       {'type': 'integer', 'description': 'ID of the lead to update'},
    'name':          {'type': 'string'},
    'phone':         {'type': 'string'},
    'email':         {'type': 'string'},
    'source':        {'type': 'string'},
    'lead_score':    {'type': 'integer'},
    'notes':         {'type': 'string'},
    'assigned_to':   {'type': 'string', 'description': 'UUID of the user to reassign to'},
    'custom_fields': {'type': 'object'},
}, ['lead_id'])

_tool('append_lead_note', """
Append a timestamped block to a lead's notes without erasing what is already
there.

Returns the lead id and the full updated notes body.
This is the safe, non-destructive alternative to update_lead(notes=…), which
replaces the whole field. The append is done under a row lock, so concurrent
appends are not lost.
Get lead_id from list_leads or lookup_lead_by_phone.
""", {
    'lead_id': {'type': 'integer', 'description': 'ID of the lead'},
    'text':    {'type': 'string',  'description': 'Note text to append. A "— <timestamp>" header is added automatically.'},
}, ['lead_id', 'text'])

_tool('set_lead_follow_up', """
Set or clear the next follow-up date on a lead, with an optional reminder.

Returns the lead id, the stored next_follow_up_at and the resulting reminder
(or null if reminders are disabled).
The reminder fires reminder_offset_minutes BEFORE follow_up_at and must be in
the future. Setting reminder_enabled=false cancels any existing reminder but
keeps the follow-up date.
Get lead_id from list_leads; read the current schedule with get_lead_follow_up.
""", {
    'lead_id':                  {'type': 'integer', 'description': 'ID of the lead'},
    'follow_up_at':             {'type': 'string',  'description': 'When to follow up, as an ISO 8601 datetime (e.g. 2026-09-01T10:30:00Z)'},
    'reminder_enabled':         {'type': 'boolean', 'description': 'Create/update a reminder for this follow-up (default true)'},
    'reminder_offset_minutes':  {'type': 'integer', 'description': 'Minutes before follow_up_at to fire the reminder (default 0)'},
}, ['lead_id', 'follow_up_at'])

_tool('bulk_import_leads', """
Import multiple leads from a JSON array.

Each lead object must have at least: name, phone
Optional per lead: email, source, lead_score, notes, assigned_to, custom_fields

Returns { success_count, failure_count, errors[] }
""", {
    'leads': {
        'type': 'array',
        'description': 'Array of lead objects',
        'items': {
            'type': 'object',
            'properties': {
                'name':  {'type': 'string'},
                'phone': {'type': 'string'},
                'email': {'type': 'string'},
            },
            'required': ['name', 'phone'],
        }
    }
}, ['leads'])

_tool('add_lead_to_group', """
Add a lead to a CRM lead group (list).

lead_group_id: integer ID of the group (get from list_lead_groups if needed)
""", {
    'lead_id':       {'type': 'integer'},
    'lead_group_id': {'type': 'integer'},
}, ['lead_id', 'lead_group_id'])

_tool('add_leads_to_group', """
Add many leads to a lead group in one call.

Returns { added, already_in_group, not_found }. Leads already in the group are
skipped, not duplicated.
Get lead_group_id from list_lead_groups and lead ids from list_leads.
For a single lead you can also use add_lead_to_group.
""", {
    'lead_group_id': {'type': 'integer', 'description': 'ID of the target group. Get it from list_lead_groups.'},
    'lead_ids':      {'type': 'array', 'items': {'type': 'integer'},
                      'description': 'IDs of the leads to add'},
}, ['lead_group_id', 'lead_ids'])

_tool('remove_leads_from_group', """
Remove many leads from a lead group in one call.

Returns { removed }. Removing a lead from a group does NOT delete the lead —
only its membership.
Get lead_group_id from list_lead_groups and the member ids from
list_group_leads.
""", {
    'lead_group_id': {'type': 'integer', 'description': 'ID of the group. Get it from list_lead_groups.'},
    'lead_ids':      {'type': 'array', 'items': {'type': 'integer'},
                      'description': 'IDs of the leads to remove from the group'},
}, ['lead_group_id', 'lead_ids'])

_tool('list_users', """
List the users (team members) in this workspace.

Users come from the central auth directory (admin.celiyo.com), not the CRM.
Returns id (UUID), name, and email for each user.
Use a user's id as the assigned_to value when assigning leads or filtering leads.
""", {
    'search':    {'type': 'string',  'description': 'Filter users by name or email (optional)'},
    'page_size': {'type': 'integer', 'description': 'Max users to return (default 100)'},
})

_tool('assign_lead', """
Assign (or reassign) a single lead to a user.

Pass the user's UUID as assigned_to (resolve names via list_users first).
Pass assigned_to = null to unassign the lead.
""", {
    'lead_id':     {'type': 'integer', 'description': 'ID of the lead to assign'},
    'assigned_to': {'type': ['string', 'null'], 'description': 'User UUID to assign the lead to, or null to unassign'},
}, ['lead_id', 'assigned_to'])

_tool('bulk_assign_leads', """
Assign (or reassign) many leads to one user in a single call.

Applies the same assigned_to to every lead in lead_ids.
Pass assigned_to = null to unassign all of them.
Returns per-lead success/failure counts.
""", {
    'lead_ids':    {'type': 'array', 'items': {'type': 'integer'}, 'description': 'IDs of the leads to assign'},
    'assigned_to': {'type': ['string', 'null'], 'description': 'User UUID to assign all leads to, or null to unassign'},
}, ['lead_ids', 'assigned_to'])

_tool('list_lead_groups', """
List CRM lead groups (lists/segments) for this workspace.

Returns id, name, description, color_hex and lead_count for each group,
paginated.
Use a group's id as lead_group_id when calling add_lead_to_group,
add_leads_to_group, remove_leads_from_group, list_group_leads or
create_campaign.
""", {
    'search':    {'type': 'string',  'description': 'Filter by group name or description (partial match)'},
    'page':      {'type': 'integer', 'description': 'Page number (default 1)'},
    'page_size': {'type': 'integer', 'description': 'Results per page (default 50, max 200)'},
})

_tool('list_group_leads', """
List the leads that belong to one lead group.

Returns a paginated list of leads (id, name, phone, email, status, lead_score,
assigned_to) plus the group's name.
Get lead_group_id from list_lead_groups. Use add_leads_to_group /
remove_leads_from_group to change membership.
""", {
    'lead_group_id': {'type': 'integer', 'description': 'ID of the lead group. Get it from list_lead_groups.'},
    'search':        {'type': 'string',  'description': 'Filter the group members by name, phone, or email'},
    'page':          {'type': 'integer', 'description': 'Page number (default 1)'},
    'page_size':     {'type': 'integer', 'description': 'Results per page (default 50, max 200)'},
}, ['lead_group_id'])

_tool('create_lead_group', """
Create a new CRM lead group (list/segment).

Required: name (must be unique within the workspace)
Optional: description, color_hex (e.g. #6366F1)
Returns the created group with its id.
""", {
    'name':        {'type': 'string', 'description': 'Display name, e.g. VIP Clients'},
    'description': {'type': 'string', 'description': 'Optional description of the group purpose'},
    'color_hex':   {'type': 'string', 'description': 'Optional hex color for the group badge, e.g. #6366F1'},
}, ['name'])

_tool('create_lead_status', """
Create a new pipeline status (stage) for leads.

Required: name (must be unique within the workspace)
Optional: order_index (board position; auto-appended if omitted),
          color_hex, is_won, is_lost, is_active.
A status cannot be both is_won and is_lost.
Returns the created status with its id.
""", {
    'name':        {'type': 'string',  'description': 'Status name, e.g. Qualified or Closed Won'},
    'order_index': {'type': 'integer', 'description': 'Sort position on the board (lower = earlier). Auto-appended to the end if omitted.'},
    'color_hex':   {'type': 'string',  'description': 'Optional hex color, e.g. #22C55E'},
    'is_won':      {'type': 'boolean', 'description': 'True if this stage represents a won deal'},
    'is_lost':     {'type': 'boolean', 'description': 'True if this stage represents a lost deal'},
    'is_active':   {'type': 'boolean', 'description': 'Whether the status is active (default true)'},
}, ['name'])

_tool('list_tasks', """
List and filter tasks.

Returns a paginated list with id, title, status, priority, due_date, lead_id,
lead_name, assignee_user_id and completed_at, ordered by due date.
Get lead_id from list_leads and assignee_user_id from list_users.
Use get_task for the full body of one task and update_task to change it.
""", {
    'lead_id':          {'type': 'integer', 'description': 'Only tasks attached to this lead'},
    'status':           {'type': 'string',  'enum': ['TODO', 'IN_PROGRESS', 'DONE', 'CANCELLED'],
                         'description': 'Only tasks in this state'},
    'priority':         {'type': 'string',  'enum': ['LOW', 'MEDIUM', 'HIGH'],
                         'description': 'Only tasks at this priority'},
    'assignee_user_id': {'type': 'string',  'description': 'User UUID the task is assigned to. Resolve names via list_users.'},
    'due_after':        {'type': 'string',  'description': 'Only tasks due at/after this ISO 8601 datetime'},
    'due_before':       {'type': 'string',  'description': 'Only tasks due at/before this ISO 8601 datetime'},
    'overdue':          {'type': 'boolean', 'description': 'If true, only tasks past their due date that are not DONE or CANCELLED'},
    'search':           {'type': 'string',  'description': 'Filter by title or description (partial match)'},
    'page':             {'type': 'integer', 'description': 'Page number (default 1)'},
    'page_size':        {'type': 'integer', 'description': 'Results per page (default 50, max 200)'},
})

_tool('get_task', """
Get full details of a single task by ID.

Returns title, description, status, priority, due_date, checklist,
assignee_user_id, the linked lead (id, name, phone) and timestamps.
Get task_id from list_tasks.
""", {
    'task_id': {'type': 'integer', 'description': 'ID of the task. Get it from list_tasks.'},
}, ['task_id'])

_tool('create_task', """
Create a task in CRM, optionally linked to a lead.

Required: title
Optional: lead_id, description, due_date (YYYY-MM-DD), priority (LOW/MEDIUM/HIGH),
          assignee_user_id (UUID)
""", {
    'title':            {'type': 'string'},
    'lead_id':          {'type': 'integer', 'description': 'Link task to this lead'},
    'description':      {'type': 'string'},
    'due_date':         {'type': 'string', 'description': 'YYYY-MM-DD'},
    'priority':         {'type': 'string', 'enum': ['LOW', 'MEDIUM', 'HIGH']},
    'assignee_user_id': {'type': 'string', 'description': 'UUID of user to assign task to'},
}, ['title'])

_tool('update_task', """
Update a task. All fields except task_id are optional.

status: TODO | IN_PROGRESS | DONE | CANCELLED
""", {
    'task_id':          {'type': 'integer'},
    'title':            {'type': 'string'},
    'description':      {'type': 'string'},
    'due_date':         {'type': 'string'},
    'priority':         {'type': 'string', 'enum': ['LOW', 'MEDIUM', 'HIGH']},
    'status':           {'type': 'string', 'enum': ['TODO', 'IN_PROGRESS', 'DONE', 'CANCELLED']},
    'assignee_user_id': {'type': 'string'},
}, ['task_id'])

_tool('list_lead_activities', """
List the activity timeline (calls, emails, meetings, notes, SMS, real-estate
events) recorded against leads.

Returns a paginated list with id, lead_id, lead_name, type, content,
happened_at and meta, newest first.
Get lead_id from list_leads or lookup_lead_by_phone.
Use create_lead_activity to add an entry, or append_lead_note to add to the
lead's notes body instead.
""", {
    'lead_id':         {'type': 'integer', 'description': 'Only activities for this lead'},
    'type':            {'type': 'string',
                        'enum': ['CALL', 'EMAIL', 'MEETING', 'NOTE', 'SMS', 'REAL_ESTATE', 'OTHER'],
                        'description': 'Only activities of this type'},
    'happened_after':  {'type': 'string',  'description': 'Only activities at/after this ISO 8601 datetime'},
    'happened_before': {'type': 'string',  'description': 'Only activities at/before this ISO 8601 datetime'},
    'search':          {'type': 'string',  'description': 'Filter by activity content (partial match)'},
    'page':            {'type': 'integer', 'description': 'Page number (default 1)'},
    'page_size':       {'type': 'integer', 'description': 'Results per page (default 50, max 200)'},
})

_tool('create_lead_activity', """
Log an activity on a lead (call, note, email, SMS, meeting, etc.)

type options: CALL | NOTE | EMAIL | SMS | MEETING | WHATSAPP | OTHER
""", {
    'lead_id':     {'type': 'integer'},
    'type':        {'type': 'string', 'enum': ['CALL', 'NOTE', 'EMAIL', 'SMS', 'MEETING', 'WHATSAPP', 'OTHER']},
    'content':     {'type': 'string', 'description': 'What happened / what was said'},
    'happened_at': {'type': 'string', 'description': 'ISO 8601 datetime, defaults to now'},
}, ['lead_id', 'type', 'content'])

_tool('list_meetings', """
List and filter meetings.

Returns a paginated list with id, title, start_at, end_at, location, lead_id,
lead_name and notes, ordered by start time.
Get lead_id from list_leads. Use get_meetings_calendar for a date-grouped view
and update_meeting to reschedule.
""", {
    'lead_id':      {'type': 'integer', 'description': 'Only meetings for this lead'},
    'start_after':  {'type': 'string',  'description': 'Only meetings starting at/after this ISO 8601 datetime'},
    'start_before': {'type': 'string',  'description': 'Only meetings starting at/before this ISO 8601 datetime'},
    'search':       {'type': 'string',  'description': 'Filter by title, location, description or notes'},
    'page':         {'type': 'integer', 'description': 'Page number (default 1)'},
    'page_size':    {'type': 'integer', 'description': 'Results per page (default 50, max 200)'},
})

_tool('get_meetings_calendar', """
Get meetings grouped by calendar date.

Returns date_from, date_to and calendar_data — a map of YYYY-MM-DD to the
meetings starting that day (id, title, start_at, end_at, location, lead_id,
lead_name).
Defaults to the next 31 days. The window is capped at 92 days and 500 meetings;
use list_meetings when you need to page beyond that.
""", {
    'month':     {'type': 'string', 'description': 'Calendar month as YYYY-MM. Takes precedence over date_from/date_to.'},
    'date_from': {'type': 'string', 'description': 'Start of the range as YYYY-MM-DD (default today)'},
    'date_to':   {'type': 'string', 'description': 'End of the range as YYYY-MM-DD, inclusive (default 31 days after date_from)'},
})

_tool('create_meeting', """
Schedule a meeting, optionally linked to a lead and with attendees.

Returns the created meeting's id, uid, title, start_at, end_at, status and the
attendee rows created.
The MCP service account is always added as the ORGANIZER attendee.
Pass lead_id for a customer-facing meeting (get it from list_leads or
lookup_lead_by_phone); omit it only for an INTERNAL meeting.
For a repeating meeting pass rrule; the series end is derived from its UNTIL or
COUNT automatically.
Read meetings back with list_meetings or get_meetings_calendar, and change one
with update_meeting.
""", {
    'title':          {'type': 'string',  'description': 'Meeting title'},
    'start_time':     {'type': 'string',  'description': 'Start as an ISO 8601 datetime (e.g. 2026-09-01T10:30:00Z)'},
    'end_time':       {'type': 'string',  'description': 'End as an ISO 8601 datetime. Must not be before start_time.'},
    'lead_id':        {'type': 'integer', 'description': 'Lead this meeting is with. Omit for an internal meeting.'},
    'meeting_type':   {'type': 'string',
                       'enum': ['MEETING', 'CALL', 'DEMO', 'SITE_VISIT',
                                'FOLLOW_UP', 'INTERNAL', 'OTHER'],
                       'description': 'What kind of meeting this is (default MEETING). Drives the calendar colour.'},
    'all_day':        {'type': 'boolean', 'description': 'True for an all-day event (default false)'},
    'timezone':       {'type': 'string',  'description': 'IANA timezone the meeting is authored in, e.g. "Asia/Kolkata" (default UTC). start_time/end_time stay absolute instants; this is what the UI renders in and what rrule is expanded against so DST stays correct.'},
    'location':       {'type': 'string',  'description': 'Physical location or room'},
    'description':    {'type': 'string',  'description': 'Agenda / body shown on the calendar entry'},
    'notes':          {'type': 'string',  'description': 'Internal notes, not part of the calendar entry'},
    'conference_url': {'type': 'string',  'description': 'Video call link (Meet/Zoom/Teams)'},
    'status':         {'type': 'string',
                       'enum': ['SCHEDULED', 'CONFIRMED', 'TENTATIVE',
                                'CANCELLED', 'COMPLETED', 'NO_SHOW'],
                       'description': 'Lifecycle state (default SCHEDULED)'},
    'visibility':     {'type': 'string', 'enum': ['DEFAULT', 'PUBLIC', 'PRIVATE'],
                       'description': 'PRIVATE hides the details from anyone who is not the owner or an attendee (default DEFAULT)'},
    'rrule':          {'type': 'string',  'description': 'RFC 5545 recurrence rule without the "RRULE:" prefix, e.g. "FREQ=WEEKLY;BYDAY=MO,WE;UNTIL=20261231T183000Z". Omit for a one-off meeting.'},
    'attendees':      {'type': 'array',
                       'description': 'People to invite. Each entry needs at least one of user_id, lead_id or email.',
                       'items': {
                           'type': 'object',
                           'properties': {
                               'user_id':      {'type': 'string',  'description': 'Internal user UUID. Resolve names via list_users.'},
                               'lead_id':      {'type': 'integer', 'description': 'CRM lead to invite. Get it from list_leads.'},
                               'email':        {'type': 'string',  'description': 'Raw email address for an external guest'},
                               'display_name': {'type': 'string',  'description': 'Name to show for this attendee'},
                               'role':         {'type': 'string', 'enum': ['REQUIRED', 'OPTIONAL'],
                                                'description': 'Default REQUIRED. The organizer is set automatically and cannot be assigned here.'},
                               'notify':       {'type': 'boolean', 'description': 'Send this attendee reminders (default true)'},
                           },
                       }},
}, ['title', 'start_time', 'end_time'])

_tool('update_meeting', """
Update a scheduled meeting.

All fields except meeting_id are optional -- only send what you want to change.
Returns the meeting id and the list of fields that were changed.
Setting status to CANCELLED records who cancelled it and when (and stores
cancellation_reason if given); setting it to COMPLETED stamps completed_at.
Cancelling is the correct way to call a meeting off -- there is no delete tool.
Get meeting_id from list_meetings or get_meetings_calendar. Attendees cannot be
changed here; recreate the meeting if the invite list is wrong.
""", {
    'meeting_id':          {'type': 'integer', 'description': 'ID of the meeting. Get it from list_meetings.'},
    'title':               {'type': 'string',  'description': 'New title'},
    'start_time':          {'type': 'string',  'description': 'New start as an ISO 8601 datetime'},
    'end_time':            {'type': 'string',  'description': 'New end as an ISO 8601 datetime'},
    'lead_id':             {'type': 'integer', 'description': 'Re-link the meeting to this lead'},
    'meeting_type':        {'type': 'string',
                            'enum': ['MEETING', 'CALL', 'DEMO', 'SITE_VISIT',
                                     'FOLLOW_UP', 'INTERNAL', 'OTHER'],
                            'description': 'New meeting type'},
    'all_day':             {'type': 'boolean', 'description': 'Switch to/from an all-day event'},
    'timezone':            {'type': 'string',  'description': 'New IANA authoring timezone, e.g. "Asia/Kolkata"'},
    'location':            {'type': 'string',  'description': 'New location'},
    'description':         {'type': 'string',  'description': 'New agenda / body'},
    'notes':               {'type': 'string',  'description': 'New internal notes'},
    'conference_url':      {'type': 'string',  'description': 'New video call link'},
    'status':              {'type': 'string',
                            'enum': ['SCHEDULED', 'CONFIRMED', 'TENTATIVE',
                                     'CANCELLED', 'COMPLETED', 'NO_SHOW'],
                            'description': 'New lifecycle state'},
    'cancellation_reason': {'type': 'string',  'description': 'Why it was cancelled. Only meaningful with status = CANCELLED.'},
    'visibility':          {'type': 'string', 'enum': ['DEFAULT', 'PUBLIC', 'PRIVATE'],
                            'description': 'New visibility'},
    'rrule':               {'type': 'string',  'description': 'New RFC 5545 recurrence rule without the "RRULE:" prefix. Send an empty string to make the meeting one-off again.'},
}, ['meeting_id'])

_tool('update_lead_status', """
Move a lead to a different pipeline stage (lead status).

status_id: integer ID of the target pipeline stage.
Use the CRM UI or ask the user for the status ID if unknown.
""", {
    'lead_id':   {'type': 'integer'},
    'status_id': {'type': 'integer', 'description': 'ID of the target pipeline stage'},
    'note':      {'type': 'string', 'description': 'Optional reason for the status change'},
}, ['lead_id', 'status_id'])


# ---------------------------------------------------------------------------
# PHASE 2 — WHATSAPP MESSAGING (10 tools)
# ---------------------------------------------------------------------------

_tool('bulk_update_lead_status', """
Move many leads to the same pipeline stage in one call.

Returns { updated_count }. Only leads in this workspace are touched.
Get status_id from list_lead_statuses and lead ids from list_leads. Pass
status_id = null to clear the stage.
For a single lead use update_lead_status.
""", {
    'lead_ids':  {'type': 'array', 'items': {'type': 'integer'},
                  'description': 'IDs of the leads to move'},
    'status_id': {'type': ['integer', 'null'],
                  'description': 'Target pipeline stage id from list_lead_statuses, or null to clear the stage'},
}, ['lead_ids', 'status_id'])

_tool('send_whatsapp_template', """
Send a WhatsApp template message to a lead.

template_uid: the Laravel template _uid (get from get_whatsapp_templates)
template_components: array of component objects with variable substitutions
""", {
    'lead_id':            {'type': 'integer'},
    'template_uid':       {'type': 'string'},
    'template_components': {'type': 'array', 'description': 'Template variable components', 'items': {'type': 'object'}},
    'note':               {'type': 'string', 'description': 'Activity note to log on the lead'},
}, ['lead_id', 'template_uid'])

_tool('send_whatsapp_text', """
Send a plain text WhatsApp message to a lead.

NOTE: The 24-hour messaging window must be open (lead replied within 24h,
or a template was sent first). Use send_whatsapp_template to open the window.
""", {
    'lead_id': {'type': 'integer'},
    'text':    {'type': 'string', 'description': 'Message text to send'},
}, ['lead_id', 'text'])

_tool('get_lead_chat', """
Fetch WhatsApp chat history for a lead.

Returns paginated messages with direction (inbound/outbound), status, and timestamp.
""", {
    'lead_id':  {'type': 'integer'},
    'page':     {'type': 'integer', 'default': 1},
    'per_page': {'type': 'integer', 'default': 50, 'description': 'Max 100'},
}, ['lead_id'])

_tool('get_whatsapp_templates', """
List available WhatsApp templates for this tenant.

Returns template_uid, name, category, language, and component structure.
Use template_uid when calling send_whatsapp_template.
""", {
    'search': {'type': 'string', 'description': 'Optional search term to filter templates'},
}, [])

_tool('list_whatsapp_templates_detailed', """
List approved WhatsApp templates with their body text.

Returns uid, name, category, language, status and body for each template.
The body shows the {{1}}, {{2}} … placeholders, so use this (not
get_whatsapp_templates) when you need to know how many template_components
values a send requires.
Use a template's uid as template_uid for send_whatsapp_template,
add_sequence_step, create_campaign and create_and_launch_campaign.
""", {
    'search':   {'type': 'string', 'description': 'Filter by template name (partial, case-insensitive)'},
    'category': {'type': 'string', 'enum': ['MARKETING', 'UTILITY', 'AUTHENTICATION'],
                 'description': 'Only templates in this WhatsApp category'},
})

_tool('get_lead_enrollments', """
List sequence enrollments for a lead.

Returns enrollment id, sequence name, status (ACTIVE/PAUSED/COMPLETED/OPTED_OUT/REPLIED),
and next_step_at.
""", {
    'lead_id': {'type': 'integer'},
}, ['lead_id'])

_tool('assign_lead_chat_user', """
Assign a team member to handle this lead's WhatsApp inbox chat.

user_uid: the Laravel user _uid of the team member to assign.
""", {
    'lead_id':  {'type': 'integer'},
    'user_uid': {'type': 'string', 'description': 'Laravel _uid of the team member'},
}, ['lead_id', 'user_uid'])

_tool('mark_chat_read', """
Mark all WhatsApp messages for a lead as read in the inbox.
""", {
    'lead_id': {'type': 'integer'},
}, ['lead_id'])

_tool('block_whatsapp_contact', """
Block or unblock a lead's WhatsApp contact.

Set block=false to unblock a previously blocked contact.
""", {
    'lead_id': {'type': 'integer'},
    'block':   {'type': 'boolean', 'default': True, 'description': 'true to block, false to unblock'},
}, ['lead_id'])

_tool('agent_send_whatsapp', """
Agent-audited WhatsApp template send. Same as send_whatsapp_template but
writes to the AgentActionLog for full audit trail.

Prefer this over send_whatsapp_template when acting autonomously.
""", {
    'lead_id':             {'type': 'integer'},
    'template_uid':        {'type': 'string'},
    'template_components': {'type': 'array', 'items': {'type': 'object'}},
    'note':                {'type': 'string'},
}, ['lead_id', 'template_uid'])

_tool('get_ai_context', """
Get every id an agent needs in one call: WhatsApp templates, sequences, lead
statuses and lead groups for this workspace.

Returns whatsapp_templates (uid, name, category, language, status), sequences
(id, name, step_count), lead_statuses (id, name, color_hex, order_index) and
lead_groups (id, name, lead_count).
Takes no arguments. Prefer this over calling list_lead_statuses +
list_lead_groups + list_sequences + get_whatsapp_templates separately.
whatsapp_templates comes back empty if the WhatsApp gateway is unreachable; the
rest of the payload still returns.
""", {})

_tool('list_agent_action_logs', """
Read the audit log of actions this AI agent has already performed for the
workspace.

Returns id, action_type, status, triggered_by, payload_in, payload_out,
error_message and created_at, newest first.
Use it to avoid repeating an action or to explain what was done earlier.
log_agent_activity writes to this same log.
""", {
    'action_type': {'type': 'string',
                    'enum': ['SEND_WHATSAPP', 'ENROLL_SEQUENCE', 'CREATE_CAMPAIGN',
                             'UPDATE_LEAD_STATUS', 'LOG_ACTIVITY'],
                    'description': 'Only log entries of this action type'},
    'limit':       {'type': 'integer', 'description': 'Max results (default 50, max 200)'},
})

_tool('log_agent_activity', """
Log a custom agent activity to the DigiCRM AgentActionLog.

Use to record decisions, reasoning steps, or external actions taken.
""", {
    'lead_id':     {'type': 'integer', 'description': 'Optional lead context'},
    'action_type': {'type': 'string', 'description': 'Short label for what was done'},
    'summary':     {'type': 'string', 'description': 'Human-readable summary of what happened'},
    'payload':     {'type': 'object', 'description': 'Any structured data to attach'},
}, ['action_type', 'summary'])


# ---------------------------------------------------------------------------
# PHASE 3 — SEQUENCES & CAMPAIGNS (11 tools)
# ---------------------------------------------------------------------------

_tool('list_sequences', """
List WhatsApp follow-up sequences (drip campaigns) for this workspace.

Returns a paginated list with id, name, description, is_active, stop_on_reply,
step_count and active_enrollment_count.
Use a sequence's id as sequence_id for get_sequence_steps, add_sequence_step,
enroll_lead_in_sequence and bulk_enroll_leads_in_sequence.
""", {
    'is_active': {'type': 'boolean', 'description': 'Only active (true) or only inactive (false) sequences'},
    'search':    {'type': 'string',  'description': 'Filter by sequence name or description'},
    'page':      {'type': 'integer', 'description': 'Page number (default 1)'},
    'page_size': {'type': 'integer', 'description': 'Results per page (default 50, max 200)'},
})

_tool('get_sequence_steps', """
List the ordered steps of one WhatsApp sequence.

Returns the sequence name plus each step's id, step_number, delay_days,
template_uid, template_name and template_variable_mapping.
Get sequence_id from list_sequences. Use a returned step id as step_id for
update_sequence_step / delete_sequence_step.
""", {
    'sequence_id': {'type': 'integer', 'description': 'ID of the sequence. Get it from list_sequences.'},
}, ['sequence_id'])

_tool('list_active_sequences_with_steps', """
List every ACTIVE WhatsApp sequence together with its full ordered step list.

Returns id, name, description, step_count and steps (step_number, delay_days,
template_uid, template_name, template_variable_mapping) for each sequence.
Takes no arguments.
Use this to pick a sequence for enroll_lead_in_sequence /
bulk_enroll_leads_in_sequence without a second call to get_sequence_steps.
Use list_sequences when you also need inactive sequences or pagination.
""", {})

_tool('create_sequence', """
Create a new WhatsApp follow-up sequence.

After creating, add steps with add_sequence_step, then enroll leads.
""", {
    'name':          {'type': 'string'},
    'description':   {'type': 'string'},
    'stop_on_reply': {'type': 'boolean', 'default': True, 'description': 'Auto-stop when lead replies'},
}, ['name'])

_tool('add_sequence_step', """
Add a step to an existing sequence.

step_number: order position (1, 2, 3, ...)
delay_days: days to wait after previous step (0 = same day as enrollment for step 1)
template_uid: Lars template _uid to send at this step
""", {
    'sequence_id':              {'type': 'integer'},
    'step_number':              {'type': 'integer'},
    'delay_days':               {'type': 'integer', 'default': 0},
    'template_uid':             {'type': 'string'},
    'template_name':            {'type': 'string', 'description': 'Human-readable label'},
    'template_variable_mapping': {
        'type': 'object',
        'description': 'Maps template variable positions to lead fields. e.g. {"1": "name", "2": "phone"}'
    },
}, ['sequence_id', 'step_number', 'template_uid'])

_tool('update_sequence_step', """
Update an existing sequence step.
""", {
    'sequence_id':              {'type': 'integer'},
    'step_id':                  {'type': 'integer'},
    'delay_days':               {'type': 'integer'},
    'template_uid':             {'type': 'string'},
    'template_name':            {'type': 'string'},
    'template_variable_mapping': {'type': 'object'},
}, ['sequence_id', 'step_id'])

_tool('delete_sequence_step', """
Delete a step from a sequence. Only do this if the sequence has no active enrollments.
""", {
    'sequence_id': {'type': 'integer'},
    'step_id':     {'type': 'integer'},
}, ['sequence_id', 'step_id'])

_tool('enroll_lead_in_sequence', """
Enroll a lead into a WhatsApp follow-up sequence.

The sequence will automatically send the step messages at the configured intervals.
""", {
    'lead_id':     {'type': 'integer'},
    'sequence_id': {'type': 'integer'},
}, ['lead_id', 'sequence_id'])

_tool('bulk_enroll_leads_in_sequence', """
Enroll many leads into one WhatsApp follow-up sequence in a single call.

Returns { sequence, enrolled[], skipped[] } — skipped explains why each lead
was left out (not found, or already actively enrolled).
The sequence must be active. Get sequence_id from list_sequences or
list_active_sequences_with_steps, and lead ids from list_leads.
This action is written to the agent audit log (see list_agent_action_logs).
""", {
    'lead_ids':    {'type': 'array', 'items': {'type': 'integer'},
                    'description': 'IDs of the leads to enroll'},
    'sequence_id': {'type': 'integer', 'description': 'ID of the active sequence. Get it from list_sequences.'},
}, ['lead_ids', 'sequence_id'])

_tool('pause_enrollment', """
Pause an active sequence enrollment.

enrollment_id: get from get_lead_enrollments
""", {
    'enrollment_id': {'type': 'integer'},
}, ['enrollment_id'])

_tool('resume_enrollment', """
Resume a paused sequence enrollment.
""", {
    'enrollment_id': {'type': 'integer'},
}, ['enrollment_id'])

_tool('unenroll_lead', """
Remove a lead from a sequence (sets status to OPTED_OUT).

If sequence_id is omitted, removes the lead from ALL sequences.
""", {
    'lead_id':     {'type': 'integer'},
    'sequence_id': {'type': 'integer', 'description': 'Optional: unenroll from specific sequence only'},
}, ['lead_id'])

_tool('list_campaigns', """
List WhatsApp broadcast campaigns for this workspace.

Returns a paginated list with id, name, status, template_uid, template_name,
lead_group_id, lead_group_name, total_contacts, scheduled_at, launched_at and
laravel_campaign_uid.
Use a campaign's id as campaign_id for launch_campaign, get_campaign_analytics
and get_campaign_replies.
""", {
    'status':        {'type': 'string',
                      'enum': ['DRAFT', 'SCHEDULED', 'RUNNING', 'COMPLETED', 'FAILED'],
                      'description': 'Only campaigns in this state'},
    'lead_group_id': {'type': 'integer', 'description': 'Only campaigns targeting this lead group. Get ids from list_lead_groups.'},
    'search':        {'type': 'string',  'description': 'Filter by campaign name (partial match)'},
    'page':          {'type': 'integer', 'description': 'Page number (default 1)'},
    'page_size':     {'type': 'integer', 'description': 'Results per page (default 50, max 200)'},
})

_tool('get_campaign_replies', """
List the contacts who replied to a launched WhatsApp campaign.

Returns the reply list from the WhatsApp gateway — use it to segment warm
respondents for follow-up.
Only works once the campaign has been launched; call launch_campaign first.
Get campaign_id from list_campaigns.
""", {
    'campaign_id': {'type': 'integer', 'description': 'ID of the campaign. Get it from list_campaigns.'},
    'page':        {'type': 'integer', 'description': 'Page number (default 1)'},
    'per_page':    {'type': 'integer', 'description': 'Replies per page (default 50, max 200)'},
}, ['campaign_id'])

_tool('create_campaign', """
Create a WhatsApp campaign targeting a lead group.

lead_group_id: DigiCRM lead group ID to target
template_uid: template to send
scheduled_at: ISO 8601 datetime (optional, defaults to immediate)

Returns the campaign object in DRAFT status. Call launch_campaign to send.
""", {
    'name':                {'type': 'string'},
    'lead_group_id':       {'type': 'integer'},
    'template_uid':        {'type': 'string'},
    'template_components': {'type': 'array', 'items': {'type': 'object'}},
    'scheduled_at':        {'type': 'string', 'description': 'ISO 8601 datetime or null for immediate'},
    'notes':               {'type': 'string'},
}, ['name', 'lead_group_id', 'template_uid'])

_tool('create_and_launch_campaign', """
Create a WhatsApp broadcast campaign from an explicit list of leads and launch
it immediately — no pre-created campaign or lead group needed.

Returns campaign_id, status, contacts_count and laravel_campaign_uid.
This SENDS MESSAGES to every lead with a phone number. It cannot be undone.
Get template_uid from list_whatsapp_templates_detailed (check its body for
{{n}} placeholders and supply matching template_components), and lead ids from
list_leads or list_group_leads.
Use create_campaign + launch_campaign instead when the audience is a saved lead
group.
""", {
    'name':                {'type': 'string',  'description': 'Campaign display name'},
    'lead_ids':            {'type': 'array', 'items': {'type': 'integer'},
                            'description': 'IDs of the leads to message'},
    'template_uid':        {'type': 'string',  'description': 'Template uid from list_whatsapp_templates_detailed'},
    'template_components': {'type': 'array', 'items': {'type': 'object'},
                            'description': 'WhatsApp template component values, e.g. [{"type":"BODY","parameters":[{"type":"text","text":"Ravi"}]}]. Required when the template body has {{n}} placeholders.'},
    'scheduled_at':        {'type': 'string',  'description': 'ISO 8601 datetime to send at. Omit to send now.'},
}, ['name', 'lead_ids', 'template_uid'])

_tool('launch_campaign', """
Launch a DRAFT campaign — submits it to the WhatsApp adapter and sets status to RUNNING.

Campaign must be in DRAFT status. Lead group must have at least one lead with a phone number.
""", {
    'campaign_id': {'type': 'integer'},
}, ['campaign_id'])

_tool('get_campaign_analytics', """
Get delivery analytics for a campaign.

Returns: total, sent, delivered, read, failed, pending counts.
""", {
    'campaign_id': {'type': 'integer'},
}, ['campaign_id'])


# ---------------------------------------------------------------------------
# TELEPHONY
# ---------------------------------------------------------------------------

_tool('list_call_logs', """
List call history (TeleCMI CDR records) for this workspace.

Returns a paginated list with id, direction, call_type, from_number, to_number,
duration, call_time, lead_id, agent_user_id, call_outcome and
call_outcome_note, newest first.
Use it to answer "has anyone rung this lead?" before calling or messaging them.
Get lead_id from list_leads or lookup_lead_by_phone, and agent_user_id from
list_users. Use set_call_outcome to record a disposition on a call.
""", {
    'lead_id':       {'type': 'integer', 'description': 'Only calls linked to this lead'},
    'direction':     {'type': 'string',  'enum': ['inbound', 'outbound'], 'description': 'Only calls in this direction'},
    'call_type':     {'type': 'string',  'enum': ['answered', 'missed'], 'description': 'Only answered or only missed calls'},
    'agent_user_id': {'type': 'string',  'description': 'User UUID who handled the call. Resolve names via list_users.'},
    'date_from':     {'type': 'string',  'description': 'Only calls at/after this ISO 8601 datetime'},
    'date_to':       {'type': 'string',  'description': 'Only calls at/before this ISO 8601 datetime'},
    'page':          {'type': 'integer', 'description': 'Page number (default 1)'},
    'page_size':     {'type': 'integer', 'description': 'Results per page (default 50, max 200)'},
})


_tool('set_call_outcome', """
Record the agent disposition (outcome) and an optional note on a completed
call.

Returns the call id, the stored outcome, note and the time it was set.
Get call_id from list_call_logs. Setting an outcome again overwrites the
previous one.
""", {
    'call_id': {'type': 'integer', 'description': 'ID of the call log. Get it from list_call_logs.'},
    'outcome': {'type': 'string',
                'enum': ['interested', 'not_interested', 'follow_up',
                         'callback', 'converted', 'dnd'],
                'description': 'Disposition for this call'},
    'note':    {'type': 'string', 'description': 'Optional free-text note about the outcome (max 512 chars)'},
}, ['call_id', 'outcome'])

_tool('get_telephony_analytics', """
Get team and per-agent call analytics for a recent date window.

Returns date_from/date_to, team_summary (totals, answered/missed, talk time),
agent_summary (the same broken down per agent_user_id), outcome_breakdown
(counts per disposition) and missed_unattended (missed calls with no follow-up
yet).
Resolve agent_user_id values to names via list_users. Use list_call_logs for
the individual calls behind these numbers.
""", {
    'days': {'type': 'integer', 'description': 'Size of the window in days, ending today (default 30, max 365)'},
})


# ---------------------------------------------------------------------------
# PAYMENTS
# ---------------------------------------------------------------------------

_tool('list_payments', """
List payment records (invoices, advances, refunds) logged against leads.

Returns a paginated list with id, lead_id, lead_name, type, status, amount,
currency, method, reference_no, date and notes, newest first.
Get lead_id from list_leads. Use create_payment to add a record and
update_payment to correct one.
""", {
    'lead_id':   {'type': 'integer', 'description': 'Only payments for this lead'},
    'type':      {'type': 'string', 'enum': ['INVOICE', 'REFUND', 'ADVANCE', 'OTHER'],
                  'description': 'Only payments of this type'},
    'status':    {'type': 'string', 'enum': ['PENDING', 'CLEARED', 'FAILED', 'CANCELLED'],
                  'description': 'Only payments in this state'},
    'date_from': {'type': 'string',  'description': 'Only payments dated at/after this ISO 8601 datetime'},
    'date_to':   {'type': 'string',  'description': 'Only payments dated at/before this ISO 8601 datetime'},
    'search':    {'type': 'string',  'description': 'Filter by reference_no or notes (partial match)'},
    'page':      {'type': 'integer', 'description': 'Page number (default 1)'},
    'page_size': {'type': 'integer', 'description': 'Results per page (default 50, max 200)'},
})

_tool('create_payment', """
Record a payment against a lead.

Returns the created payment's id, lead_id, type, status and amount.
This writes a financial record. Confirm the amount and lead with the user
first; there is no delete tool to undo it — correct mistakes with
update_payment (e.g. status = CANCELLED).
Get lead_id from list_leads or lookup_lead_by_phone.
""", {
    'lead_id':      {'type': 'integer', 'description': 'ID of the lead this payment belongs to'},
    'amount':       {'type': 'number',  'description': 'Payment amount, e.g. 25000.00'},
    'type':         {'type': 'string', 'enum': ['INVOICE', 'REFUND', 'ADVANCE', 'OTHER'],
                     'description': 'What kind of record this is'},
    'status':       {'type': 'string', 'enum': ['PENDING', 'CLEARED', 'FAILED', 'CANCELLED'],
                     'description': 'Payment state (default CLEARED)'},
    'date':         {'type': 'string',  'description': 'When the payment happened, as an ISO 8601 datetime (default now)'},
    'currency':     {'type': 'string',  'description': 'ISO currency code (default INR)'},
    'method':       {'type': 'string',  'description': 'How it was paid, e.g. "UPI", "NEFT", "cash"'},
    'reference_no': {'type': 'string',  'description': 'Transaction / cheque / invoice reference number'},
    'notes':        {'type': 'string',  'description': 'Free-text notes'},
}, ['lead_id', 'amount', 'type'])

_tool('update_payment', """
Update an existing payment record.

All fields except payment_id are optional — only send what you want to change.
Returns the payment id and the fields that were changed.
Get payment_id from list_payments. Use status = CANCELLED to void a payment
rather than trying to delete it.
""", {
    'payment_id':   {'type': 'integer', 'description': 'ID of the payment. Get it from list_payments.'},
    'amount':       {'type': 'number',  'description': 'New amount'},
    'type':         {'type': 'string', 'enum': ['INVOICE', 'REFUND', 'ADVANCE', 'OTHER'],
                     'description': 'New record type'},
    'status':       {'type': 'string', 'enum': ['PENDING', 'CLEARED', 'FAILED', 'CANCELLED'],
                     'description': 'New payment state'},
    'date':         {'type': 'string',  'description': 'New payment date as an ISO 8601 datetime'},
    'currency':     {'type': 'string',  'description': 'New ISO currency code'},
    'method':       {'type': 'string',  'description': 'New payment method'},
    'reference_no': {'type': 'string',  'description': 'New reference number'},
    'notes':        {'type': 'string',  'description': 'New notes (replaces the existing notes)'},
}, ['payment_id'])


# ---------------------------------------------------------------------------
# REAL ESTATE
# ---------------------------------------------------------------------------

_tool('list_projects', """
List real estate projects (developments) in this workspace.

Returns a paginated list with id, name, project_type, status, city, state,
rera_number, possession_date and unit_count.
Use a project's id as project_id for get_project_summary, list_units and
create_project_interest.
""", {
    'status':       {'type': 'string',
                     'enum': ['planning', 'under_construction', 'ready_to_move',
                              'completed', 'on_hold'],
                     'description': 'Only projects in this build state'},
    'project_type': {'type': 'string', 'enum': ['residential', 'commercial', 'mixed', 'plotted'],
                     'description': 'Only projects of this type'},
    'search':       {'type': 'string',  'description': 'Filter by project name, city or RERA number'},
    'page':         {'type': 'integer', 'description': 'Page number (default 1)'},
    'page_size':    {'type': 'integer', 'description': 'Results per page (default 50, max 200)'},
})

_tool('get_project_summary', """
Get inventory availability for one real estate project.

Returns the project's basics plus unit_counts_by_status (available / held /
booked / sold / blocked), unit_counts_by_type and unit_counts_by_floor.
Get project_id from list_projects. Use list_units to see the individual units
behind these counts.
""", {
    'project_id': {'type': 'integer', 'description': 'ID of the project. Get it from list_projects.'},
}, ['project_id'])

_tool('list_units', """
List individual sellable units (flats, villas, plots, shops, offices).

Returns a paginated list with id, unit_number, project_id, project_name,
block_name, unit_type, configuration, floor_number, facing, carpet/built-up
area, total_price and status.
Get project_id from list_projects. Use a unit's id as unit_id for
create_unit_lead and update_unit_status.
""", {
    'project_id':   {'type': 'integer', 'description': 'Only units in this project. Get ids from list_projects.'},
    'block_id':     {'type': 'integer', 'description': 'Only units in this block/tower/wing'},
    'status':       {'type': 'string',
                     'enum': ['available', 'held', 'booked', 'sold', 'blocked'],
                     'description': 'Only units in this sales state'},
    'unit_type':    {'type': 'string',
                     'enum': ['flat', 'villa', 'row_house', 'plot',
                              'commercial_shop', 'commercial_office', 'other'],
                     'description': 'Only units of this type'},
    'floor_number': {'type': 'integer', 'description': 'Only units on this floor (may be negative for basements)'},
    'search':       {'type': 'string',  'description': 'Filter by unit_number or configuration, e.g. "A-1203" or "2BHK"'},
    'page':         {'type': 'integer', 'description': 'Page number (default 1)'},
    'page_size':    {'type': 'integer', 'description': 'Results per page (default 50, max 200)'},
})

_tool('create_project_interest', """
Record that a lead is interested in a real estate project, with an optional
budget range and preferred unit type.

Returns the interest id and whether it was newly created (a lead can only have
one interest row per project — a repeat call returns the existing one).
Creating one also writes a REAL_ESTATE entry to the lead's activity timeline.
Get lead_id from list_leads and project_id from list_projects. Once the lead
picks a specific unit, use create_unit_lead.
""", {
    'lead_id':             {'type': 'integer', 'description': 'ID of the interested lead'},
    'project_id':          {'type': 'integer', 'description': 'ID of the project. Get it from list_projects.'},
    'preferred_unit_type': {'type': 'string',
                            'enum': ['flat', 'villa', 'row_house', 'plot',
                                     'commercial_shop', 'commercial_office', 'other'],
                            'description': 'What kind of unit the lead wants'},
    'budget_min':          {'type': 'number', 'description': 'Lower end of the lead\'s budget'},
    'budget_max':          {'type': 'number', 'description': 'Upper end of the lead\'s budget'},
    'notes':               {'type': 'string', 'description': 'Free-text notes about the requirement'},
}, ['lead_id', 'project_id'])

_tool('create_unit_lead', """
Link a lead to a specific unit and record where they are in the buying journey
(interested, site visit scheduled/done, negotiating, booked, sold, cancelled).

Returns the link id and whether it was newly created (one link per lead+unit —
a repeat call returns the existing one).
Creating one also writes a REAL_ESTATE entry to the lead's activity timeline.
This is the "book a site visit" primitive: pass
relation_type = "site_visit_scheduled".
Get lead_id from list_leads and unit_id from list_units.
""", {
    'lead_id':       {'type': 'integer', 'description': 'ID of the lead'},
    'unit_id':       {'type': 'integer', 'description': 'ID of the unit. Get it from list_units.'},
    'relation_type': {'type': 'string',
                      'enum': ['interested', 'site_visit_scheduled', 'site_visit_done',
                               'negotiating', 'booked', 'sold', 'cancelled'],
                      'description': 'Where the lead is in the journey for this unit'},
    'notes':         {'type': 'string', 'description': 'Free-text notes'},
}, ['lead_id', 'unit_id', 'relation_type'])

_tool('update_unit_status', """
Change the sales status of a single unit (availability board update).

Returns the unit id, its unit_number and the new status.
Marking a unit held/booked/sold takes it out of the available inventory shown by
list_units and get_project_summary — confirm with the user before changing it.
Get unit_id from list_units.
""", {
    'unit_id': {'type': 'integer', 'description': 'ID of the unit. Get it from list_units.'},
    'status':  {'type': 'string',
                'enum': ['available', 'held', 'booked', 'sold', 'blocked'],
                'description': 'New sales status for the unit'},
}, ['unit_id', 'status'])


# ===========================================================================
# TOOL EXECUTOR
# ===========================================================================

def execute_tool(name: str, args: dict) -> str:
    """
    Dispatch a tool call to the appropriate digicrm API.
    Returns a JSON string result or a plain-text error.
    """
    try:
        result = _dispatch(name, args)
        return json.dumps(result, indent=2, default=str)
    except McpApiError as e:
        return json.dumps({'error': str(e), 'status_code': e.status_code})
    except Exception as e:
        logger.exception(f"Unexpected error in tool {name}")
        return json.dumps({'error': f'Internal MCP error: {e}'})


def _dispatch(name: str, args: dict) -> Any:  # noqa: C901
    """DEPRECATED — stdio/REST dispatcher. Route tool name → digicrm REST call.

    Superseded by ``mcp/django_view.py::_dispatch_tool`` (the HTTP path served
    at ``POST /mcp/sse``), which is the single supported dispatcher. This
    function covers only the original 39 tools; anything registered in TOOLS
    after 2026-08-19 falls through to "Unknown tool". Do not add new tools
    here — see the module docstring.
    """

    # ---- PHASE 1 ----

    if name == 'list_leads':
        params = {}
        for k in ('search', 'page', 'page_size'):
            if args.get(k) is not None:
                params[k] = args[k]
        if args.get('assigned_to'):
            params['assigned_to'] = args['assigned_to']
        elif args.get('unassigned'):
            params['assigned_to__isnull'] = 'true'
        return client.get('/api/crm/leads/', params=params)

    elif name == 'get_lead':
        return client.get(f"/api/crm/leads/{args['lead_id']}/")

    elif name == 'list_lead_statuses':
        return client.get('/api/crm/statuses/')

    elif name == 'list_users':
        params = {'page_size': args.get('page_size', 100)}
        if args.get('search'):
            params['search'] = args['search']
        return client.get('/api/crm/users/', params=params)

    elif name == 'create_lead':
        body = {k: v for k, v in args.items() if v is not None}
        return client.post('/api/crm/leads/', body)

    elif name == 'assign_lead':
        lead_id = args['lead_id']
        return client.patch(f'/api/crm/leads/{lead_id}/', {'assigned_to': args.get('assigned_to')})

    elif name == 'bulk_assign_leads':
        assigned_to = args.get('assigned_to')
        success, failure, errors = 0, 0, []
        for lead_id in args['lead_ids']:
            try:
                client.patch(f'/api/crm/leads/{lead_id}/', {'assigned_to': assigned_to})
                success += 1
            except Exception as exc:  # noqa: BLE001
                failure += 1
                errors.append({'lead_id': lead_id, 'error': str(exc)})
        return {'success_count': success, 'failure_count': failure, 'errors': errors}

    elif name == 'create_lead_group':
        body = {k: v for k, v in args.items() if v is not None}
        return client.post('/api/crm/lead-groups/', body)

    elif name == 'create_lead_status':
        body = {k: v for k, v in args.items() if v is not None}
        return client.post('/api/crm/statuses/', body)

    elif name == 'update_lead':
        lead_id = args.pop('lead_id')
        body = {k: v for k, v in args.items() if v is not None}
        return client.patch(f'/api/crm/leads/{lead_id}/', body)

    elif name == 'bulk_import_leads':
        leads = args['leads']
        # Build JSON body (import_leads supports JSON payload)
        return client.post('/api/crm/leads/import_leads/', {'leads': leads})

    elif name == 'add_lead_to_group':
        group_id = args['lead_group_id']
        lead_id  = args['lead_id']
        return client.post(f'/api/crm/lead-groups/{group_id}/add-leads/', {'lead_ids': [lead_id]})

    elif name == 'create_task':
        body = {k: v for k, v in args.items() if v is not None}
        return client.post('/api/tasks/', body)

    elif name == 'update_task':
        task_id = args.pop('task_id')
        body = {k: v for k, v in args.items() if v is not None}
        return client.patch(f'/api/tasks/{task_id}/', body)

    elif name == 'create_lead_activity':
        body = {k: v for k, v in args.items() if v is not None}
        return client.post('/api/crm/lead-activities/', body)

    elif name == 'create_meeting':
        body = {k: v for k, v in args.items() if v is not None}
        return client.post('/api/meetings/', body)

    elif name == 'update_meeting':
        meeting_id = args.pop('meeting_id')
        body = {k: v for k, v in args.items() if v is not None}
        return client.patch(f'/api/meetings/{meeting_id}/', body)

    elif name == 'update_lead_status':
        lead_id   = args['lead_id']
        status_id = args['status_id']
        note      = args.get('note')
        # Update the lead's status field directly via PATCH
        body = {'status': status_id}
        if note:
            body['notes'] = note
        return client.patch(f'/api/crm/leads/{lead_id}/', body)

    # ---- PHASE 2 ----

    elif name == 'send_whatsapp_template':
        lead_id = args['lead_id']
        body = {
            'template_uid': args['template_uid'],
            'template_components': args.get('template_components', []),
        }
        if args.get('note'):
            body['note'] = args['note']
        return client.post(f'/api/whatsapp/leads/{lead_id}/send/', body)

    elif name == 'send_whatsapp_text':
        lead_id = args['lead_id']
        return client.post(f'/api/whatsapp/leads/{lead_id}/send_text/', {'text': args['text']})

    elif name == 'get_lead_chat':
        lead_id  = args['lead_id']
        page     = args.get('page', 1)
        per_page = args.get('per_page', 50)
        return client.get(f'/api/whatsapp/leads/{lead_id}/chat/', {'page': page, 'per_page': per_page})

    elif name == 'get_whatsapp_templates':
        params = {}
        if args.get('search'):
            params['search'] = args['search']
        return client.get('/api/whatsapp/templates/', params or None)

    elif name == 'get_lead_enrollments':
        lead_id = args['lead_id']
        return client.get(f'/api/whatsapp/leads/{lead_id}/enrollments/')

    elif name == 'assign_lead_chat_user':
        lead_id  = args['lead_id']
        user_uid = args['user_uid']
        return client.post(f'/api/whatsapp/leads/{lead_id}/assign-chat-user/', {'user_uid': user_uid})

    elif name == 'mark_chat_read':
        lead_id = args['lead_id']
        return client.post(f'/api/whatsapp/leads/{lead_id}/mark-read/', {})

    elif name == 'block_whatsapp_contact':
        lead_id  = args['lead_id']
        do_block = args.get('block', True)
        return client.post(f'/api/whatsapp/leads/{lead_id}/block/', {'block': do_block})

    elif name == 'agent_send_whatsapp':
        body = {
            'lead_id':             args['lead_id'],
            'template_uid':        args['template_uid'],
            'template_components': args.get('template_components', []),
        }
        if args.get('note'):
            body['note'] = args['note']
        return client.post('/api/whatsapp/agent/send/', body)

    elif name == 'log_agent_activity':
        return client.post('/api/whatsapp/agent/log-activity/', {
            'action_type':  args['action_type'],
            'summary':      args['summary'],
            'lead_id':      args.get('lead_id'),
            'payload':      args.get('payload', {}),
        })

    # ---- PHASE 3 ----

    elif name == 'create_sequence':
        body = {
            'name':          args['name'],
            'description':   args.get('description', ''),
            'stop_on_reply': args.get('stop_on_reply', True),
        }
        return client.post('/api/whatsapp/sequences/', body)

    elif name == 'add_sequence_step':
        seq_id = args['sequence_id']
        body = {
            'step_number':               args['step_number'],
            'delay_days':                args.get('delay_days', 0),
            'template_uid':              args['template_uid'],
            'template_name':             args.get('template_name', ''),
            'template_variable_mapping': args.get('template_variable_mapping', {}),
        }
        return client.post(f'/api/whatsapp/sequences/{seq_id}/steps/add/', body)

    elif name == 'update_sequence_step':
        seq_id  = args['sequence_id']
        step_id = args['step_id']
        body = {k: v for k, v in {
            'delay_days':                args.get('delay_days'),
            'template_uid':              args.get('template_uid'),
            'template_name':             args.get('template_name'),
            'template_variable_mapping': args.get('template_variable_mapping'),
        }.items() if v is not None}
        return client.patch(f'/api/whatsapp/sequences/{seq_id}/steps/{step_id}/', body)

    elif name == 'delete_sequence_step':
        seq_id  = args['sequence_id']
        step_id = args['step_id']
        return client.delete(f'/api/whatsapp/sequences/{seq_id}/steps/{step_id}/delete/')

    elif name == 'enroll_lead_in_sequence':
        lead_id     = args['lead_id']
        sequence_id = args['sequence_id']
        return client.post(f'/api/whatsapp/leads/{lead_id}/enroll/', {'sequence_id': sequence_id})

    elif name == 'pause_enrollment':
        enrollment_id = args['enrollment_id']
        return client.patch(f'/api/whatsapp/enrollments/{enrollment_id}/', {'action': 'pause'})

    elif name == 'resume_enrollment':
        enrollment_id = args['enrollment_id']
        return client.patch(f'/api/whatsapp/enrollments/{enrollment_id}/', {'action': 'resume'})

    elif name == 'unenroll_lead':
        lead_id     = args['lead_id']
        sequence_id = args.get('sequence_id')
        body = {}
        if sequence_id:
            body['sequence_id'] = sequence_id
        return client.delete_with_body(f'/api/whatsapp/leads/{lead_id}/unenroll/', body)

    elif name == 'create_campaign':
        body = {k: v for k, v in args.items() if v is not None}
        return client.post('/api/whatsapp/agent/campaign/', body)

    elif name == 'launch_campaign':
        campaign_id = args['campaign_id']
        return client.post(f'/api/whatsapp/campaigns/{campaign_id}/launch/', {})

    elif name == 'get_campaign_analytics':
        campaign_id = args['campaign_id']
        return client.get(f'/api/whatsapp/campaigns/{campaign_id}/analytics/')

    else:
        raise McpApiError(f"Unknown tool: {name}", 400)


# ===========================================================================
# STDIO MCP PROTOCOL (JSON-RPC 2.0 over stdin/stdout)
# ===========================================================================

def _json_rpc_response(req_id, result):
    return {'jsonrpc': '2.0', 'id': req_id, 'result': result}

def _json_rpc_error(req_id, code, message):
    return {'jsonrpc': '2.0', 'id': req_id, 'error': {'code': code, 'message': message}}

def _handle_request(req: dict) -> dict:
    method = req.get('method', '')
    req_id = req.get('id')
    params = req.get('params', {})

    if method == 'initialize':
        return _json_rpc_response(req_id, {
            'protocolVersion': '2024-11-05',
            'capabilities': {'tools': {}},
            'serverInfo': {'name': 'digicrm-mcp', 'version': '1.0.0'},
        })

    elif method == 'tools/list':
        return _json_rpc_response(req_id, {'tools': TOOLS})

    elif method == 'tools/call':
        tool_name = params.get('name', '')
        tool_args = params.get('arguments', {})
        result_text = execute_tool(tool_name, tool_args)
        return _json_rpc_response(req_id, {
            'content': [{'type': 'text', 'text': result_text}],
            'isError': result_text.startswith('{"error"'),
        })

    elif method == 'notifications/initialized':
        return None  # No response for notifications

    else:
        return _json_rpc_error(req_id, -32601, f'Method not found: {method}')


def run_stdio():
    """Run the MCP server in stdio mode (JSON-RPC 2.0 over stdin/stdout)."""
    config.validate()
    logger.info(f"DigiCRM MCP server starting. Base URL: {config.DIGICRM_BASE_URL}, Tenant: {config.DIGICRM_TENANT_ID}")

    stdin  = io.TextIOWrapper(sys.stdin.buffer, encoding='utf-8')
    stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)

    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            resp = _json_rpc_error(None, -32700, f'Parse error: {e}')
            stdout.write(json.dumps(resp) + '\n')
            stdout.flush()
            continue

        response = _handle_request(req)
        if response is not None:
            stdout.write(json.dumps(response) + '\n')
            stdout.flush()


if __name__ == '__main__':
    run_stdio()
