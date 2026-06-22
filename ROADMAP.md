# Celiyo AI Platform — Development Roadmap

**Stack:** DigiCRM (Django) · Laravel WhatsApp API · celiyoagents (Node.js/TS) · n8n / Make  
**Security rule:** `tenant_id` always from server env/JWT, never from agent payload.

---

## Phase 1 — Fix & Stabilize (DigiCRM MCP)

**Goal:** All 34 MCP tools passing. Foundation must be solid before building on top.

### 1.1 Fix remaining MCP tool bugs

**Fix #0 — `list_lead_groups` MCP tool missing ✅ DONE**
- Added `list_lead_groups` tool to `mcp/server.py` (tool definition + `_dispatch` handler)
- Endpoint: `GET /api/crm/lead-groups/?search=&page=&page_size=`
- Returns: `id`, `name`, `description`, `lead_count` per group
- Added to `test_http.py` Phase 1 — discovered `lead_group_id` now threaded into `add_lead_to_group` and `create_campaign` (no more hardcoded `1`)
- Total MCP tools: **35**

**Fix #1 — `send_whatsapp_template` / `agent_send_whatsapp`: template parameter error**
- File: `mcp/test_http.py`
- Problem: template `new_property_lead_notification` has `{{1}}` variables, test sends no `template_components`
- Fix: pass dummy components OR use a no-variable template in the test

**Fix #2 — `assign_lead_chat_user`: uses DigiCRM user system ✅ DONE**
- File: `whatsapp_integration/views.py` → `assign_chat_user`
- Fix: `Lead.assigned_to = user_uid` written directly in DigiCRM using the admin.celiyo.com JWT user UUID
- Laravel adapter called as best-effort only (its user model is separate; failure is non-blocking)
- Activity log entry created on every assignment

**Fix #3 — `launch_campaign`: template not found**
- File: `mcp/test_http.py` campaign section
- Problem: hardcoded `placeholder_uid` instead of real `t_uid` from `get_whatsapp_templates`
- Fix: thread `t_uid` from the templates call into the campaign section

**Fix #4 — `create_sequence`: duplicate key on re-run**
- File: `mcp/test_http.py`
- Problem: `_MCP_TEST_SEQ` already exists on second test run → unique constraint violation
- Fix: add timestamp suffix: `_MCP_TEST_SEQ_{int(time.time())}`

### 1.2 Fix Invalid Token

- Problem: `WA_API_TOKEN` in DigiCRM `.env` doesn't match `vendor_api_access_token` in Laravel DB
- Fix: run this on the Laravel DB:
  ```sql
  SELECT meta_value FROM whatsapp_settings
  WHERE meta_key = 'vendor_api_access_token'
  AND vendors__id = (SELECT _id FROM vendors WHERE _uid = '90d99df2-4fc7-4957-a5ac-c5d95b771ee1');
  ```
  Copy result → update `WA_API_TOKEN` in DigiCRM `.env`

### 1.3 Rotate MCP secret

- `letmegoin@0008` has appeared in logs — rotate before production deploy
- Update `MCP_SECRET` in `.env` and update any clients using it

### 1.4 Deploy to production

```bash
git pull
sudo systemctl restart digicrm.service
python mcp/test_http.py --url https://crm.celiyo.com/mcp/sse --secret 'NEW_SECRET'
```

**Exit criteria:** 34/34 tools pass. Zero red in test output.

---

## Phase 2 — Context Capsule + WhatsApp AI Pipeline

**Goal:** Incoming WhatsApp message → AI replies automatically using full lead context.

### Architecture

```
Incoming WhatsApp message
        ↓
Laravel webhook → DigiCRM WhatsAppWebhookView
        ↓
DigiCRM assembles context capsule (one DB read pass)
        ↓
POST /agents/{agent_id}/chat  →  celiyoagents
   { message, context_capsule }
        ↓
celiyoagents injects capsule into system prompt
LLM reasons immediately — no warm-up tool calls needed
        ↓
Write actions via CRM tools (update status, enroll, send template)
        ↓
Reply via Laravel adapter sendTextMessage
```

### 2.1 Extend AIContextView for lead-specific capsule (DigiCRM)

- File: `whatsapp_integration/views.py` → `AIContextView`
- Add optional `?lead_id=` query param
- When provided, return full capsule:
  ```json
  {
    "lead": { "id", "name", "phone", "status", "source", "notes", "assigned_to" },
    "conversation": [ last 15 WhatsApp messages with timestamps ],
    "active_sequence": { "name", "current_step", "next_action_at" },
    "lead_groups": [ "Hot Leads", "Mumbai" ],
    "recent_activities": [ last 5 CRM activities ],
    "whatsapp_templates": [ uid, name, category, body ],
    "sequences": [ active sequences ],
    "lead_statuses": [ all statuses ]
  }
  ```
- When no `lead_id`, return existing static context (templates + sequences + statuses + groups)

### 2.2 Wire DigiCRM webhook to celiyoagents

- File: `whatsapp_integration/views.py` → `WhatsAppWebhookView`
- On inbound message event:
  1. Identify lead by phone number
  2. Assemble context capsule (call `AIContextView` logic internally)
  3. `POST {CELIYOAGENTS_URL}/agents/{WHATSAPP_AGENT_ID}/chat` with `{ message, context_capsule }`
  4. Receive reply text
  5. Send reply via `adapter.send_text_message(lead.phone, reply_text)`
- New env vars: `CELIYOAGENTS_URL`, `CELIYOAGENTS_WHATSAPP_AGENT_ID`, `CELIYOAGENTS_API_KEY`

### 2.3 Build chat endpoint in celiyoagents

- File: `voice-orchestrator/server/src/chat/chat.controller.ts`
- `POST /agents/:agentId/chat`
- Body: `{ message: string, context_capsule: object, conversation_id?: string }`
- Handler:
  1. Load agent config (system prompt, LLM provider, tool list)
  2. Inject context capsule into system prompt as structured text
  3. Call LLM with write tools available
  4. Execute any tool calls → feed result back → continue until done
  5. Return `{ reply: string, tools_used: [], conversation_id }`

### 2.4 Static context caching in celiyoagents

- On startup, fetch `GET /api/whatsapp/ai/context/` (no lead_id) from DigiCRM
- Cache: templates, active sequences, lead statuses, lead groups
- Refresh every 30 minutes
- Avoids sending shared static config inside every capsule

### 2.5 End-to-end test

- Send real WhatsApp → AI reply arrives < 5 seconds
- `log_agent_activity` called → visible in DigiCRM agent action logs
- Lead status updated if agent decided to change it

**Exit criteria:** Real WhatsApp message → AI replies. CRM updated. Activity logged.

---

## Phase 3 — n8n / Make Channels + Automation Loops

**Goal:** Time-based and event-based automations feeding into DigiCRM and celiyoagents.
No voice/post-call work in this phase.

### Architecture: Two valid paths from n8n/Make

```
External event (form, ad, schedule, webhook)
        ↓
    n8n / Make
       /        \
      ↓          ↓
DigiCRM REST   celiyoagents /chat
(CRUD only)    (AI reasoning + CRM write via tools)
```

Use DigiCRM REST directly when the action is deterministic (create lead, update status, enroll sequence).  
Use celiyoagents when you need AI reasoning over the data before acting.

### 3.1 n8n/Make → DigiCRM direct (no AI needed)

These are pure CRUD workflows — no LLM involved:

| Trigger | Action via DigiCRM REST |
|---|---|
| Facebook/Instagram lead form | `POST /api/crm/leads/` → create lead |
| Google Ads lead form | Same + `POST /api/whatsapp/agent/send/` → welcome WhatsApp |
| Every Monday 9am | `GET /api/crm/leads/?status=COLD` → `POST /api/whatsapp/agent/enroll/` |
| Daily missed follow-up check | `GET /api/crm/tasks/?overdue=true` → create tasks for team |
| Lead status changes to WON | `POST /api/whatsapp/agent/send/` → congratulations template |

### 3.2 n8n/Make → celiyoagents (AI reasoning needed)

Use this when the automation needs to decide what to do based on lead context:

| Trigger | celiyoagents does |
|---|---|
| New lead from cold database | AI qualifies lead, drafts first WhatsApp, decides which sequence |
| Lead replied to campaign | AI reads reply, decides next step (enroll / update status / create task) |
| Weekly re-engagement batch | AI personalises message per lead based on their history |

n8n calls:
```
POST {CELIYOAGENTS_URL}/agents/{agent_id}/chat
Authorization: Bearer {CELIYOAGENTS_API_KEY}
{
  "message": "New lead from Facebook ad. Decide next action.",
  "context_capsule": { ... assembled by n8n from DigiCRM API ... }
}
```

### 3.3 Conversation state / memory

- celiyoagents stores `Conversation` records per lead
- Multi-turn WhatsApp conversations persist across messages without DigiCRM re-sending full history
- DigiCRM capsule still sent per-message for live lead data (status, assignments)
- Prevents context window bloat

### 3.4 Write tool set for chat agent

Chat agent in celiyoagents needs these tools (already imported as CRM tools):
- `crm_update_lead_status` — move lead through pipeline
- `crm_agent_enroll_sequence` — start a nurture sequence
- `crm_agent_send_whatsapp` — send a WhatsApp template
- `crm_log_agent_activity` — log what the agent did and why
- `crm_create_task` — create a follow-up task for a human
- `crm_update_lead` — update notes/fields

### 3.5 Human handoff

- Agent detects escalation: angry message, explicit "talk to human", unresolved after N turns
- Creates CRM task: "Human review needed — [reason]"
- Sends WhatsApp: "Connecting you with a team member shortly"
- Marks conversation `ESCALATED` in celiyoagents — auto-reply paused until human releases it

**Exit criteria:** At least one n8n workflow live (e.g. Facebook lead → DigiCRM → welcome WhatsApp). celiyoagents handles a 5-turn WhatsApp conversation with CRM writes. Human handoff tested.

---

## Dependency Map

```
Phase 1 (MCP stable + Invalid Token fixed)
    └─→ Phase 2 (capsule + webhook + celiyoagents chat)
            └─→ Phase 3 (n8n/Make channels + conversation memory + human handoff)
```

Phase 2 cannot start until Invalid Token is fixed (WhatsApp sends must work).  
Phase 3 can partially start in parallel with Phase 2 (n8n → DigiCRM REST flows don't need celiyoagents).

---

## Security Checklist

- [ ] `tenant_id` only from JWT/env — never from request body or agent payload
- [ ] MCP secret rotated (Phase 1)
- [ ] `CELIYOAGENTS_API_KEY` — scoped MCP key (AGENT scope, not ALL) for WhatsApp agent
- [ ] celiyoagents never receives `WA_API_TOKEN` or `WA_VENDOR_UID` — stays in DigiCRM env only
- [ ] Context capsule contains no secrets — lead data only
- [ ] Every agent write action logged in CRM activity log
- [ ] n8n/Make webhooks use HMAC secret validation before processing

---

## Out of Scope (deferred)

- Voice agent / post-call automation — deferred to future phase
- Multi-agent routing (Sales vs Support agent) — after Phase 3 stable
