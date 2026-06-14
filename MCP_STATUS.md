# DigiCRM MCP — Status, Endpoints & Fix Guide

**Last tested:** 2026-06-14  
**Test command:** `python mcp/test_http.py --url http://127.0.0.1:8000/mcp/sse --secret "letmegoin@0008"`  
**Result:** 23 passed · 6 failed · 1 skipped / 30 total

---

## MCP Server

- **Endpoint:** `POST /mcp/sse`  
- **Auth:** `Authorization: Bearer <MCP_SECRET>`  
- **Protocol:** MCP Streamable HTTP 2025-03-26 (JSON-RPC 2.0)  
- **Tools registered:** 34

---

## All 34 MCP Tools

### Phase 1 — CRM Core (13 tools) ✅ All passing

| Tool | Description | Status |
|------|-------------|--------|
| `list_leads` | Paginated lead list, supports search/filter | ✅ |
| `get_lead` | Get single lead by ID | ✅ |
| `list_lead_statuses` | List pipeline statuses (id, name, color_hex) | ✅ |
| `create_lead` | Create a new lead | ✅ |
| `update_lead` | Update lead fields (name, phone, notes, etc.) | ✅ |
| `update_lead_status` | Move lead to a different pipeline stage | ✅ |
| `bulk_import_leads` | Import array of leads in one call | ✅ |
| `add_lead_to_group` | Add lead to a lead group | ✅ |
| `create_lead_activity` | Log a call/note/email activity on a lead | ✅ |
| `create_task` | Create a task linked to a lead | ✅ |
| `update_task` | Update task (title, due date, status) | ✅ |
| `create_meeting` | Schedule a meeting for a lead | ✅ |
| `update_meeting` | Update meeting details | ✅ |

### Phase 2 — WhatsApp (10 tools) — 7 passing, 3 failing

| Tool | Description | Status |
|------|-------------|--------|
| `get_whatsapp_templates` | List available WA templates with `_uid`, name, category | ✅ |
| `get_lead_chat` | Fetch WA message history for a lead by phone | ✅ |
| `get_lead_enrollments` | List sequence enrollments for a lead | ✅ |
| `send_whatsapp_text` | Send plain text WA message to a lead | ✅ |
| `mark_chat_read` | Mark all inbound messages as read for a lead | ✅ |
| `block_whatsapp_contact` | Block/unblock a WA contact by phone | ✅ |
| `log_agent_activity` | Log AI agent action to audit trail | ✅ |
| `send_whatsapp_template` | Send a template message (with components) | ❌ see Fix #1 |
| `agent_send_whatsapp` | Agent-flavoured template send (auto-logged) | ❌ see Fix #1 |
| `assign_lead_chat_user` | Assign a WA chat to a team member | ❌ see Fix #2 |

### Phase 3 — Sequences & Campaigns (11 tools) — 9 passing, 2 failing

| Tool | Description | Status |
|------|-------------|--------|
| `create_sequence` | Create a drip sequence | ✅ * |
| `add_sequence_step` | Add a step to a sequence | ✅ |
| `update_sequence_step` | Edit a sequence step | ✅ |
| `delete_sequence_step` | Remove a step | ✅ |
| `enroll_lead_in_sequence` | Enrol a lead into a sequence | ✅ |
| `pause_enrollment` | Pause an active enrolment | ✅ |
| `resume_enrollment` | Resume a paused enrolment | ✅ |
| `unenroll_lead` | Opt a lead out of a sequence | ✅ |
| `create_campaign` | Create a WhatsApp broadcast campaign (DRAFT) | ✅ |
| `launch_campaign` | Launch campaign via Laravel adapter | ❌ see Fix #3 |
| `get_campaign_analytics` | Get delivery stats for a launched campaign | ❌ cascades from Fix #3 |

\* `create_sequence` fails on repeat runs due to unique name constraint — see Fix #4 (test-data issue only, not a backend bug).

---

## REST API Endpoints

All under base path `/api/` · Auth: JWT Bearer token

### CRM (`/api/crm/`)
| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/api/crm/leads/` | List / create leads |
| GET/PATCH/DELETE | `/api/crm/leads/{id}/` | Get / update / delete lead |
| GET/POST | `/api/crm/statuses/` | Pipeline statuses |
| GET/POST | `/api/crm/activities/` | Lead activities |
| GET/POST | `/api/crm/lead-groups/` | Lead groups |

### WhatsApp (`/api/whatsapp/`)
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/whatsapp/templates/` | Templates list (proxy to Laravel) |
| GET/POST | `/api/whatsapp/campaigns/` | Campaign CRUD |
| POST | `/api/whatsapp/campaigns/{id}/launch/` | Launch campaign |
| GET | `/api/whatsapp/campaigns/{id}/analytics/` | Delivery analytics |
| GET | `/api/whatsapp/campaigns/{id}/replies/` | Inbound replies |
| GET/POST | `/api/whatsapp/sequences/` | Sequence CRUD |
| POST | `/api/whatsapp/sequences/{id}/add-step/` | Add step |
| PATCH | `/api/whatsapp/sequences/{id}/update-step/{step_id}/` | Edit step |
| DELETE | `/api/whatsapp/sequences/{id}/delete-step/{step_id}/` | Delete step |
| GET | `/api/whatsapp/leads/{lead_id}/chat/` | WA chat history |
| POST | `/api/whatsapp/leads/{lead_id}/send/` | Send template msg |
| POST | `/api/whatsapp/leads/{lead_id}/send-text/` | Send text msg |
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

## Security TODOs

- [ ] **Rotate `MCP_SECRET`** — `letmegoin@0008` has appeared in multiple test runs and session logs. Change in `.env` and restart service.
- [ ] **Add `DIGICRM_TENANT_ID` and `MCP_OWNER_USER_ID` to production `.env`** if not already there.

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
