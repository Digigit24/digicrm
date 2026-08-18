# DigiCRM MCP — Status, Endpoints & Fix Guide

**Last updated:** 2026-08-19 (batches 0-3 of `_plans/02-mcp-roadmap.md`)
**Test command:** `python mcp/test_http.py --url http://127.0.0.1:8000/mcp/sse --secret "$MCP_SECRET"`
**Last full live run:** 2026-06-14 — 23 passed · 6 failed · 1 skipped / 30 total, against the
then-current 39-tool catalog. The 39 tools added since have NOT been exercised against a live
database; they were verified offline (see *Verification status* below).

---

## MCP Server

- **Endpoint:** `POST /mcp/sse`  
- **Auth:** `Authorization: Bearer <MCP_SECRET>`  
- **Protocol:** MCP Streamable HTTP 2025-03-26 (JSON-RPC 2.0)  
- **Tools registered:** 78

---

## All 78 MCP Tools

Generated from `mcp/server.py::TOOLS`. `GET /mcp/health` reports the same count.
Every tool is dispatched by `mcp/django_view.py::_dispatch_tool` (1:1, no orphans)
and is hard-scoped to `DIGICRM_TENANT_ID`.

### CRM — leads & search (10)

| Tool | R/W | What it does | Arguments (**bold** = required) |
|---|---|---|---|
| `list_leads` | R | Search, filter and list leads in the CRM. This is the advanced lead search. | search?, assigned_to?, unassigned?, status_id?, status_ids?, priority?, lead_score_min?, lead_score_max?, created_after?, created_before?, next_follow_up_before?, city?, lead_group_id?, ordering?, page?, page_size? |
| `get_lead` | R | Get full details of a single lead by ID. | **lead_id** |
| `lookup_lead_by_phone` | R | Find the single CRM lead that owns a phone number. | **phone** |
| `create_lead` | W | Create a new CRM lead. | **name**, **phone**, email?, source?, lead_score?, notes?, assigned_to?, custom_fields? |
| `update_lead` | W | Update an existing lead's fields. | **lead_id**, name?, phone?, email?, source?, lead_score?, notes?, assigned_to?, custom_fields? |
| `append_lead_note` | W | Append a timestamped block to a lead's notes without erasing what is already | **lead_id**, **text** |
| `bulk_import_leads` | W | Import multiple leads from a JSON array. | **leads** |
| `assign_lead` | W | Assign (or reassign) a single lead to a user. | **lead_id**, **assigned_to** |
| `bulk_assign_leads` | W | Assign (or reassign) many leads to one user in a single call. | **lead_ids**, **assigned_to** |
| `get_lead_field_schema` | R | Get the lead field schema configured for this workspace. | _none_ |

### CRM — pipeline & dashboards (6)

| Tool | R/W | What it does | Arguments (**bold** = required) |
|---|---|---|---|
| `list_lead_statuses` | R | List all available lead status options for this workspace. | _none_ |
| `create_lead_status` | W | Create a new pipeline status (stage) for leads. | **name**, order_index?, color_hex?, is_won?, is_lost?, is_active? |
| `update_lead_status` | W | Move a lead to a different pipeline stage (lead status). | **lead_id**, **status_id**, note? |
| `bulk_update_lead_status` | W | Move many leads to the same pipeline stage in one call. | **lead_ids**, **status_id** |
| `get_lead_kanban` | R | Get leads grouped by pipeline stage, in board order. | status_id?, limit_per_status? |
| `get_sales_dashboard` | R | Get a compact sales overview for the whole workspace. | _none_ |

### CRM — groups (lists/segments) (6)

| Tool | R/W | What it does | Arguments (**bold** = required) |
|---|---|---|---|
| `list_lead_groups` | R | List CRM lead groups (lists/segments) for this workspace. | search?, page?, page_size? |
| `list_group_leads` | R | List the leads that belong to one lead group. | **lead_group_id**, search?, page?, page_size? |
| `create_lead_group` | W | Create a new CRM lead group (list/segment). | **name**, description?, color_hex? |
| `add_lead_to_group` | W | Add a lead to a CRM lead group (list). | **lead_id**, **lead_group_id** |
| `add_leads_to_group` | W | Add many leads to a lead group in one call. | **lead_group_id**, **lead_ids** |
| `remove_leads_from_group` | W | Remove many leads from a lead group in one call. | **lead_group_id**, **lead_ids** |

### CRM — activities, tasks, meetings, follow-ups (12)

| Tool | R/W | What it does | Arguments (**bold** = required) |
|---|---|---|---|
| `list_lead_activities` | R | List the activity timeline (calls, emails, meetings, notes, SMS, real-estate | lead_id?, type?, happened_after?, happened_before?, search?, page?, page_size? |
| `create_lead_activity` | W | Log an activity on a lead (call, note, email, SMS, meeting, etc.) | **lead_id**, **type**, **content**, happened_at? |
| `list_tasks` | R | List and filter tasks. | lead_id?, status?, priority?, assignee_user_id?, due_after?, due_before?, overdue?, search?, page?, page_size? |
| `get_task` | R | Get full details of a single task by ID. | **task_id** |
| `create_task` | W | Create a task in CRM, optionally linked to a lead. | **title**, lead_id?, description?, due_date?, priority?, assignee_user_id? |
| `update_task` | W | Update a task. All fields except task_id are optional. | **task_id**, title?, description?, due_date?, priority?, status?, assignee_user_id? |
| `list_meetings` | R | List and filter meetings. | lead_id?, start_after?, start_before?, search?, page?, page_size? |
| `get_meetings_calendar` | R | Get meetings grouped by calendar date. | month?, date_from?, date_to? |
| `create_meeting` | W | Schedule a meeting linked to a lead. | **lead_id**, **title**, **start_time**, **end_time**, location?, description?, attendees? |
| `update_meeting` | W | Update a scheduled meeting. All fields except meeting_id are optional. | **meeting_id**, title?, start_time?, end_time?, location?, description?, status? |
| `get_lead_follow_up` | R | Get the follow-up schedule for one lead. | **lead_id** |
| `set_lead_follow_up` | W | Set or clear the next follow-up date on a lead, with an optional reminder. | **lead_id**, **follow_up_at**, reminder_enabled?, reminder_offset_minutes? |

### Directory (1)

| Tool | R/W | What it does | Arguments (**bold** = required) |
|---|---|---|---|
| `list_users` | R | List the users (team members) in this workspace. | search?, page_size? |

### WhatsApp — messaging & inbox (9)

| Tool | R/W | What it does | Arguments (**bold** = required) |
|---|---|---|---|
| `get_lead_chat` | R | Fetch WhatsApp chat history for a lead. | **lead_id**, page?, per_page? |
| `send_whatsapp_template` | W | Send a WhatsApp template message to a lead. | **lead_id**, **template_uid**, template_components?, note? |
| `send_whatsapp_text` | W | Send a plain text WhatsApp message to a lead. | **lead_id**, **text** |
| `agent_send_whatsapp` | W | Agent-audited WhatsApp template send. Same as send_whatsapp_template but | **lead_id**, **template_uid**, template_components?, note? |
| `assign_lead_chat_user` | W | Assign a team member to handle this lead's WhatsApp inbox chat. | **lead_id**, **user_uid** |
| `mark_chat_read` | W | Mark all WhatsApp messages for a lead as read in the inbox. | **lead_id** |
| `block_whatsapp_contact` | W | Block or unblock a lead's WhatsApp contact. | **lead_id**, block? |
| `get_whatsapp_templates` | R | List available WhatsApp templates for this tenant. | search? |
| `list_whatsapp_templates_detailed` | R | List approved WhatsApp templates with their body text. | search?, category? |

### WhatsApp — sequences & enrollments (13)

| Tool | R/W | What it does | Arguments (**bold** = required) |
|---|---|---|---|
| `list_sequences` | R | List WhatsApp follow-up sequences (drip campaigns) for this workspace. | is_active?, search?, page?, page_size? |
| `list_active_sequences_with_steps` | R | List every ACTIVE WhatsApp sequence together with its full ordered step list. | _none_ |
| `get_sequence_steps` | R | List the ordered steps of one WhatsApp sequence. | **sequence_id** |
| `create_sequence` | W | Create a new WhatsApp follow-up sequence. | **name**, description?, stop_on_reply? |
| `add_sequence_step` | W | Add a step to an existing sequence. | **sequence_id**, **step_number**, delay_days?, **template_uid**, template_name?, template_variable_mapping? |
| `update_sequence_step` | W | Update an existing sequence step. | **sequence_id**, **step_id**, delay_days?, template_uid?, template_name?, template_variable_mapping? |
| `delete_sequence_step` | W | Delete a step from a sequence. Only do this if the sequence has no active enrollments. | **sequence_id**, **step_id** |
| `get_lead_enrollments` | R | List sequence enrollments for a lead. | **lead_id** |
| `enroll_lead_in_sequence` | W | Enroll a lead into a WhatsApp follow-up sequence. | **lead_id**, **sequence_id** |
| `bulk_enroll_leads_in_sequence` | W | Enroll many leads into one WhatsApp follow-up sequence in a single call. | **lead_ids**, **sequence_id** |
| `pause_enrollment` | W | Pause an active sequence enrollment. | **enrollment_id** |
| `resume_enrollment` | W | Resume a paused sequence enrollment. | **enrollment_id** |
| `unenroll_lead` | W | Remove a lead from a sequence (sets status to OPTED_OUT). | **lead_id**, sequence_id? |

### WhatsApp — campaigns (6)

| Tool | R/W | What it does | Arguments (**bold** = required) |
|---|---|---|---|
| `list_campaigns` | R | List WhatsApp broadcast campaigns for this workspace. | status?, lead_group_id?, search?, page?, page_size? |
| `create_campaign` | W | Create a WhatsApp campaign targeting a lead group. | **name**, **lead_group_id**, **template_uid**, template_components?, scheduled_at?, notes? |
| `launch_campaign` | W | Launch a DRAFT campaign — submits it to the WhatsApp adapter and sets status to RUNNING. | **campaign_id** |
| `create_and_launch_campaign` | W | Create a WhatsApp broadcast campaign from an explicit list of leads and launch | **name**, **lead_ids**, **template_uid**, template_components?, scheduled_at? |
| `get_campaign_analytics` | R | Get delivery analytics for a campaign. | **campaign_id** |
| `get_campaign_replies` | R | List the contacts who replied to a launched WhatsApp campaign. | **campaign_id**, page?, per_page? |

### Agent context & audit (3)

| Tool | R/W | What it does | Arguments (**bold** = required) |
|---|---|---|---|
| `get_ai_context` | R | Get every id an agent needs in one call: WhatsApp templates, sequences, lead | _none_ |
| `log_agent_activity` | W | Log a custom agent activity to the DigiCRM AgentActionLog. | lead_id?, **action_type**, **summary**, payload? |
| `list_agent_action_logs` | R | Read the audit log of actions this AI agent has already performed for the | action_type?, limit? |

### Telephony (3)

| Tool | R/W | What it does | Arguments (**bold** = required) |
|---|---|---|---|
| `list_call_logs` | R | List call history (TeleCMI CDR records) for this workspace. | lead_id?, direction?, call_type?, agent_user_id?, date_from?, date_to?, page?, page_size? |
| `set_call_outcome` | W | Record the agent disposition (outcome) and an optional note on a completed | **call_id**, **outcome**, note? |
| `get_telephony_analytics` | R | Get team and per-agent call analytics for a recent date window. | days? |

### Payments (3)

| Tool | R/W | What it does | Arguments (**bold** = required) |
|---|---|---|---|
| `list_payments` | R | List payment records (invoices, advances, refunds) logged against leads. | lead_id?, type?, status?, date_from?, date_to?, search?, page?, page_size? |
| `create_payment` | W | Record a payment against a lead. | **lead_id**, **amount**, **type**, status?, date?, currency?, method?, reference_no?, notes? |
| `update_payment` | W | Update an existing payment record. | **payment_id**, amount?, type?, status?, date?, currency?, method?, reference_no?, notes? |

### Real estate (6)

| Tool | R/W | What it does | Arguments (**bold** = required) |
|---|---|---|---|
| `list_projects` | R | List real estate projects (developments) in this workspace. | status?, project_type?, search?, page?, page_size? |
| `get_project_summary` | R | Get inventory availability for one real estate project. | **project_id** |
| `list_units` | R | List individual sellable units (flats, villas, plots, shops, offices). | project_id?, block_id?, status?, unit_type?, floor_number?, search?, page?, page_size? |
| `create_project_interest` | W | Record that a lead is interested in a real estate project, with an optional | **lead_id**, **project_id**, preferred_unit_type?, budget_min?, budget_max?, notes? |
| `create_unit_lead` | W | Link a lead to a specific unit and record where they are in the buying journey | **lead_id**, **unit_id**, **relation_type**, notes? |
| `update_unit_status` | W | Change the sales status of a single unit (availability board update). | **unit_id**, **status** |

---

## REST API Endpoints

All under base path `/api/` · Auth: JWT Bearer token

### CRM (`/api/crm/`)
| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/api/crm/leads/` | List / create leads |
| GET/PATCH/DELETE | `/api/crm/leads/{id}/` | Get / update / delete lead |
| GET | `/api/crm/leads/lookup-by-phone/` | Resolve a lead from a phone number |
| GET | `/api/crm/leads/sales-dashboard/` | Compact sales overview |
| GET | `/api/crm/leads/kanban/` | Leads grouped by pipeline stage |
| POST | `/api/crm/leads/{id}/append-note/` | Non-destructive note append |
| GET/PATCH | `/api/crm/leads/{id}/follow-up-schedule/` | Read / set follow-up + reminder |
| POST | `/api/crm/leads/bulk-status-update/` | Move many leads to one stage |
| GET/POST | `/api/crm/statuses/` | Pipeline statuses |
| GET/POST | `/api/crm/activities/` | Lead activities |
| GET/POST | `/api/crm/lead-groups/` | Lead groups |
| GET | `/api/crm/lead-groups/{id}/leads/` | Members of a group |
| POST | `/api/crm/lead-groups/{id}/add-leads/` | Bulk add to group |
| POST | `/api/crm/lead-groups/{id}/remove-leads/` | Bulk remove from group |
| GET | `/api/crm/field-configurations/field_schema/` | Standard + custom lead field schema |
| GET | `/api/crm/users/` | Workspace user directory proxy |

### Tasks, meetings, payments (`/api/tasks/`, `/api/meetings/`, `/api/payments/`)
| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/api/tasks/` | List / create tasks |
| GET/PATCH/DELETE | `/api/tasks/{id}/` | Get / update / delete task |
| GET/POST | `/api/meetings/` | List / create meetings |
| GET | `/api/meetings/calendar/` | Meetings grouped by date |
| GET/POST | `/api/payments/` | List / create payments |
| GET/PATCH/DELETE | `/api/payments/{id}/` | Get / update / delete payment |

### Real estate (`/api/real-estate/`)
| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/api/real-estate/projects/` | Project CRUD |
| GET | `/api/real-estate/projects/{id}/summary/` | Unit counts by status / type / floor |
| GET/POST | `/api/real-estate/units/` | Unit CRUD |
| PATCH | `/api/real-estate/units/{id}/` | Update a unit (e.g. status) |
| POST | `/api/real-estate/project-interests/` | Link a lead to a project (`perform_create` also logs a CRM activity) |
| POST | `/api/real-estate/unit-leads/` | Link a lead to a unit (`perform_create` also logs a CRM activity) |

### Telephony (`/api/telephony/`)
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/telephony/calls/` | Call log list |
| PATCH | `/api/telephony/calls/{id}/outcome/` | Set the agent disposition on a call |
| GET | `/api/telephony/analytics/` | Team + per-agent call analytics (`?days=`) |

### WhatsApp (`/api/whatsapp/`)
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/whatsapp/templates/` | Templates list (proxy to Laravel) |
| GET/POST | `/api/whatsapp/campaigns/` | Campaign CRUD |
| POST | `/api/whatsapp/campaigns/{id}/launch/` | Launch campaign |
| GET | `/api/whatsapp/campaigns/{id}/analytics/` | Delivery analytics |
| GET | `/api/whatsapp/campaigns/{id}/replies/` | Inbound replies |
| GET/POST | `/api/whatsapp/sequences/` | Sequence CRUD |
| POST | `/api/whatsapp/sequences/{id}/steps/add/` | Add step |
| PUT/PATCH | `/api/whatsapp/sequences/{id}/steps/{step_id}/` | Edit step |
| DELETE | `/api/whatsapp/sequences/{id}/steps/{step_id}/delete/` | Delete step |
| GET | `/api/whatsapp/leads/{lead_id}/chat/` | WA chat history |
| POST | `/api/whatsapp/leads/{lead_id}/send/` | Send template msg |
| POST | `/api/whatsapp/leads/{lead_id}/send_text/` | Send text msg |
| PATCH | `/api/whatsapp/enrollments/{id}/` | Pause/resume/cancel enrolment |

### Agent endpoints (`/api/whatsapp/agent/`)
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/whatsapp/agent/send/` | Send WA (logged) |
| POST | `/api/whatsapp/agent/enroll/` | Enrol lead in sequence |
| POST | `/api/whatsapp/agent/campaign/` | Create + launch campaign |
| POST | `/api/whatsapp/agent/update-status/` | Update lead status |
| POST | `/api/whatsapp/agent/log-activity/` | Log agent action |
| GET | `/api/whatsapp/agent/logs/` | Agent action audit log |

### AI context endpoints (`/api/whatsapp/ai/`) — added 2026-06-14
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/whatsapp/ai/context/` | **All-in-one:** templates + sequences + statuses + lead groups |
| GET | `/api/whatsapp/ai/templates/` | Templates with uid, name, category, body · supports `?search=` `?category=` |
| POST | `/api/whatsapp/ai/campaign/launch/` | Create + launch from `lead_ids` + `template_uid` in one call |
| GET | `/api/whatsapp/ai/sequences/` | Active sequences with steps |

### Laravel WhatsApp Adapter (`https://whatsappapi.celiyo.com/api/{vendorUid}/adapter/`)
| Method | Path | Description |
|--------|------|-------------|
| POST | `/adapter/campaigns/from-contacts` | Create campaign with raw phone list |
| GET | `/adapter/campaigns/{uid}/analytics` | Campaign delivery stats |
| GET | `/adapter/campaigns/{uid}/replies` | Inbound replies for campaign |
| POST | `/adapter/messages/send` | Send template message |
| POST | `/adapter/messages/send-text` | Send plain text |
| GET | `/adapter/contacts/by-phone/{phone}/messages` | Chat history |
| POST | `/adapter/contacts/by-phone/{phone}/assign-user` | Assign chat user |
| POST | `/adapter/contacts/by-phone/{phone}/mark-read` | Mark read |
| POST | `/adapter/contacts/by-phone/{phone}/block` | Block contact |
| POST | `/adapter/contacts/by-phone/{phone}/unblock` | Unblock contact |

---

## Verification status

| Check | Result |
|---|---|
| `python -m py_compile mcp/server.py mcp/django_view.py mcp/test_http.py` | pass |
| `python manage.py check` | pass — 0 issues |
| `len(TOOLS)` == number of `_dispatch_tool` branches | 78 == 78, no orphans either way |
| Every new queryset compiled to SQL offline (`str(qs.query)`) | 39/39 compiled — no bad field names |
| `GET /mcp/health` tool count == `len(TOOLS)` | 78 == 78 |
| `POST /mcp/sse` `tools/list` over the real view | 78 tools, all with name + description + inputSchema |
| Every `required` key exists in that tool's `properties` | pass |
| No schema exposes `tenant_id` / `owner_user_id` / `created_by` / `by_user_id` | pass |
| Unauthenticated / wrong-secret request | 401 |
| Blank `MCP_SECRET` | 401 (fail closed) |
| Live `tools/call` against a database | **not run** — no local database available |

---

## Deliberately NOT built

Decided in `_plans/02-mcp-roadmap.md`; do not add these without a fresh decision.

| Area | Why not |
|---|---|
| Notifications (`list_notifications`, `mark_notifications_read`) | The REST viewset scopes to `recipient_user_id` from the JWT. On the MCP path that is always the fixed `MCP_OWNER_USER_ID` service account, so the agent would only ever see that account's notifications. |
| `send_sms` | Spends money per message and the MCP path has no RBAC gate. |
| Every delete (`bulk-delete`, lead/task/meeting/activity/payment/status/unit deletes) | Irreversible, and a status change is the correct agent-safe verb. |
| Credential endpoints (`telephony/credentials`, `storage-credentials`, `whatsapp/config`, `integrations/connections`) | Must never be reachable by a model. |
| Public webhooks (`telephony/webhook/*`, `whatsapp/webhooks/*`, `integrations/webhook/inbound/*`) | Inbound-only; wrapping them would let the agent forge events into its own CRM. |
| Binary / CSV I/O (lead export, attachments, project images, media proxy, call recordings) | Wrong shape for a text transport and blows the context window. |
| Batch 4 / P3 (workflows, execution logs, status config CRUD, template authoring, dialer push) | Build on demand only. |

---

## Fixes Needed (deferred)

### Fix #1 — `send_whatsapp_template` / `agent_send_whatsapp`
**Error:** `WhatsApp API error: (#132000) Number of parameters does not match`  
**Cause:** Template `new_property_lead_notification` has variable placeholders (`{{1}}`). The MCP call sends no `template_components`, so Meta rejects it.  
**Fix:**
1. Either use a template with zero variables for testing
2. Or update the MCP tool to require `template_components` when the template has variables, and document this clearly
3. In `test_http.py` around line 260: after discovering the template UID, also check if it has body variables — if yes, pass dummy values:
   ```python
   'template_components': [{'type': 'BODY', 'parameters': [{'type': 'text', 'text': 'Test'}]}]
   ```
**Files:** `mcp/test_http.py` (test fix) · no backend change needed

---

### Fix #2 — `assign_lead_chat_user`
**Error:** `Class "App\Yantrana\Components\User\Models\UserModel" not found`  
**Cause:** Wrong PHP namespace in `AdapterController.php` line 633.  
**Fix:**
1. Find correct User model path:
   ```bash
   grep -r "class UserModel" whatsapp_api/app/ --include="*.php"
   ```
2. Replace line 633 in `AdapterController.php`:
   ```php
   // Wrong:
   $user = \App\Yantrana\Components\User\Models\UserModel::where('_uid', $userUid)
   // Replace with correct namespace from grep above
   ```
3. Also need to pass a real `user_uid` in `test_http.py` (currently sends `'test-user-uid'` which won't exist in the vendor's user table)
**Files:** `whatsappapi/app/Yantrana/Components/WhatsAppService/Controllers/AdapterController.php`

---

### Fix #3 — `launch_campaign`
**Error:** `Laravel adapter error: Template not found for this vendor`  
**Cause:** `test_http.py` line ~302 hardcodes `template_uid: 'placeholder_uid'`. The real template UID is available earlier in the test (`t_uid` variable from `get_whatsapp_templates`).  
**Fix:** In `test_http.py`, thread `t_uid` through to the campaign launch section:
```python
# Around line 302, change:
'template_uid': 'placeholder_uid',
# To:
'template_uid': sample.get('template_uid', 'placeholder_uid'),
```
And earlier where `t_uid` is set, also do `sample['template_uid'] = t_uid`.  
**Files:** `mcp/test_http.py` only — no backend change

---

### Fix #4 — `create_sequence` duplicate key on re-run
**Error:** `duplicate key value violates unique constraint "unique_sequence_name_per_tenant"`  
**Cause:** Test creates `_MCP_TEST_SEQ` but never deletes it. Second run hits the unique constraint.  
**Fix (2 options):**
- **Option A (preferred):** Add timestamp to test name in `test_http.py`:
  ```python
  seq_name = '_MCP_TEST_SEQ_' + datetime.now().strftime('%H%M%S')
  ```
- **Option B:** Add teardown block at end of test that deletes all `_MCP_TEST*` records  
**Files:** `mcp/test_http.py` only

---

## Security posture

- [x] **`_check_auth` now fails closed.** It previously returned `True` whenever
  `MCP_SECRET` was blank, so an unconfigured deploy exposed every tool with no
  authentication. A blank secret now rejects all requests (401) and logs an error, and
  `/mcp/oauth/token` returns 503 instead of minting a `no-secret-configured` token.
  **Deploy consequence:** if `MCP_SECRET` is not set in production, `/mcp/sse` will start
  returning 401. Set it before deploying.
- [ ] **Rotate `MCP_SECRET`.** The previous value was committed to this file and appeared
  in test runs and session logs. Change it in `.env` and restart the service.
- [ ] **Add `DIGICRM_TENANT_ID` and `MCP_OWNER_USER_ID` to the production `.env`** if not
  already there. Several tools raise `RuntimeError` without them.
- [ ] **Standing gap — no RBAC on the MCP HTTP path.** `_dispatch_tool` talks to the ORM
  directly, so DRF's `HasCRMPermission` / `HasDigiPermission` never run. Every write tool
  (including payments and real-estate inventory) is guarded only by the shared
  `MCP_SECRET`. Any future destructive or billable tool inherits this.

---

## Deploy checklist

```bash
# DigiCRM (Django)
cd /path/to/digicrm
git pull
source venv/bin/activate
python manage.py migrate --check
sudo systemctl restart digicrm.service

# Laravel (WhatsApp API) — PHP parses fresh each request, no restart needed
# Just ensure the file is saved; verify with:
php -l whatsapp_api/app/Yantrana/Components/WhatsAppService/Controllers/AdapterController.php
```
