#!/usr/bin/env python3
"""
DigiCRM Sales Agent MCP Server

Provides 31 tools for a Claude sales agent to interact with DigiCRM:
  Phase 1 (10) — CRM core: leads, tasks, activities, meetings, status
  Phase 2 (10) — WhatsApp messaging: send, chat, sequences, inbox ops
  Phase 3 (11) — Automation: sequences, enrollments, campaigns

Transport: stdio (run as subprocess by Claude Desktop / Claude Code)

Usage:
  python -m mcp.server

Required env vars (see mcp/config.py):
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
Search and list leads in the CRM.

Returns paginated list with id, name, phone, email, status, lead_score, source,
assigned_to.
Use search to filter by name, phone, or email.
Use assigned_to (a user UUID from list_users) to show only that user's leads,
or unassigned=true to show leads with no owner.
""", {
    'search':      {'type': 'string',  'description': 'Filter by name, phone, or email (partial match)'},
    'assigned_to': {'type': 'string',  'description': 'User UUID - only return leads assigned to this user. Resolve names via list_users.'},
    'unassigned':  {'type': 'boolean', 'description': 'If true, only return leads with no assigned user. Ignored when assigned_to is set.'},
    'page':        {'type': 'integer', 'description': 'Page number (default 1)'},
    'page_size':   {'type': 'integer', 'description': 'Results per page (default 20, max 100)'},
})

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

_tool('update_lead', """
Update an existing lead's fields.

All fields except lead_id are optional — only send what you want to change.
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

_tool('create_lead_activity', """
Log an activity on a lead (call, note, email, SMS, meeting, etc.)

type options: CALL | NOTE | EMAIL | SMS | MEETING | WHATSAPP | OTHER
""", {
    'lead_id':     {'type': 'integer'},
    'type':        {'type': 'string', 'enum': ['CALL', 'NOTE', 'EMAIL', 'SMS', 'MEETING', 'WHATSAPP', 'OTHER']},
    'content':     {'type': 'string', 'description': 'What happened / what was said'},
    'happened_at': {'type': 'string', 'description': 'ISO 8601 datetime, defaults to now'},
}, ['lead_id', 'type', 'content'])

_tool('create_meeting', """
Schedule a meeting linked to a lead.

Required: lead_id, title, start_time, end_time
start_time / end_time: ISO 8601 datetime strings
""", {
    'lead_id':      {'type': 'integer'},
    'title':        {'type': 'string'},
    'start_time':   {'type': 'string', 'description': 'ISO 8601 datetime'},
    'end_time':     {'type': 'string', 'description': 'ISO 8601 datetime'},
    'location':     {'type': 'string'},
    'description':  {'type': 'string'},
    'attendees':    {'type': 'array', 'items': {'type': 'string'}, 'description': 'List of email addresses'},
}, ['lead_id', 'title', 'start_time', 'end_time'])

_tool('update_meeting', """
Update a scheduled meeting. All fields except meeting_id are optional.
""", {
    'meeting_id':   {'type': 'integer'},
    'title':        {'type': 'string'},
    'start_time':   {'type': 'string'},
    'end_time':     {'type': 'string'},
    'location':     {'type': 'string'},
    'description':  {'type': 'string'},
    'status':       {'type': 'string', 'enum': ['SCHEDULED', 'COMPLETED', 'CANCELLED']},
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
    """Route tool name → digicrm API call."""

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
