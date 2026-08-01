# DigiCRM Telephony Analytics & Intelligence Plan
> **Date:** 2026-07-29  
> **Repo:** `digicrm` (Django backend) + `sepratecrm` (React/TS frontend)  
> **Telephony provider:** TeleCMI (PIOPIY WebRTC SDK + TeleCMI REST API)  
> **Author:** Architecture review + gap analysis

---

## Table of Contents

1. [Current State Audit](#1-current-state-audit)
2. [Critical Gap: agent_user_id is Never Set](#2-critical-gap-agent_user_id-is-never-set)
3. [Gap Analysis — Full List](#3-gap-analysis--full-list)
4. [TeleCMI API Surface We're Not Using](#4-telecmi-api-surface-were-not-using)
5. [Phase 1 — Foundation Fixes (Must-do first)](#5-phase-1--foundation-fixes-must-do-first)
6. [Phase 2 — Admin Analytics Dashboard](#6-phase-2--admin-analytics-dashboard)
7. [Phase 3 — Sales Executive KPIs (Real Estate)](#7-phase-3--sales-executive-kpis-real-estate)
8. [Phase 4 — Real-Time Monitoring](#8-phase-4--real-time-monitoring)
9. [Phase 5 — AI Bridges for Future Agentic CRM](#9-phase-5--ai-bridges-for-future-agentic-crm)
10. [Copy-to-Dial Button Audit](#10-copy-to-dial-button-audit)
11. [Database Schema Changes Needed](#11-database-schema-changes-needed)
12. [Implementation Priority Matrix](#12-implementation-priority-matrix)

---

## 1. Current State Audit

### What's fully built ✅

**Backend (`digicrm/telephony/`):**
- `TeleCMICredential` — tenant-level app_id/secret (encrypted), SBC region, webhook secret
- `TeleCMIAgent` — per-user TeleCMI credentials, 20-hour token cache
- `CallLog` — CDR records (cmiuid, direction, call_type, from/to numbers, duration, billed_sec, rate, recording_file, lead_id, agent_user_id, telecmi_notes)
- `SMSLog` — outbound SMS log
- `/webhook/cdr/` — post-call CDR ingestion → upserts `CallLog` → creates `LeadActivity(CALL)` → creates callback Task on missed calls
- `/webhook/live/` — live ringing/answered/ended events → publishes to Pusher → frontend softphone state
- `/calls/click-to-call/` — passes `lead_id` as `extra_params` to TeleCMI
- `/calls/<pk>/recording/` — proxy-streams recording from TeleCMI (never exposes credentials)
- `/calls/sync/` — manual CDR pull via per-user tokens
- `/calls/add-note/` — adds note to TeleCMI call record
- Celery periodic task `sync_all_telecmi_cdrs` (every 5 min safety net)
- Lead phone matching: exact match → 10-digit suffix fallback
- Pusher real-time events via `telephony.<tenant_id>` channel

**Frontend (`sepratecrm/src/`):**
- `Softphone.tsx` — full WebRTC widget: login, dialpad, incoming/outgoing call UI, mute, hold, DTMF, transfer, merge, logout
- `LeadTelephonyHistory.tsx` — per-lead call logs + SMS, inline recording player (lazy-fetch blob), optimistic "Calling…" row, Pusher live revalidation
- `CallLogsPage.tsx` — global paginated call log table with filters (direction, call_type, agent, date range), recording playback
- `CopyPhoneButton.tsx` — clipboard copy button ✅ already exists
- `useTelephonyLiveEvents` hook — Pusher subscription for real-time events
- Optimistic call row prepended before CDR webhook confirms

### What partially exists but has gaps ⚠️

- `agent_user_id` field exists on `CallLog` but is **never populated** from webhooks
- `recording_file` correctly parsed from CDR payload (`record` or `file` field)
- Live event webhook `_normalize_live_event()` is deliberately defensive — guessing at TeleCMI field names
- `CopyPhoneButton` exists in `LeadDetailsPage.tsx` but NOT confirmed in lead drawer sidebar

### What's completely missing ❌

- Per-user aggregated call analytics endpoint
- Admin dashboard with sales executive performance metrics
- Call disposition / outcome tracking
- Pipeline-level call analytics (calls per status/stage)
- TeleCMI `POST /v2/analysis` endpoint not integrated
- Agent presence/status visibility (who is currently on a call)
- Call transcription bridge
- Export (CSV/XLSX) of call logs
- Daily call target tracking per user
- Call-level tagging/labeling for AI training

---

## 2. Critical Gap: `agent_user_id` is Never Set

This is the single most important fix. Without it, **zero per-user analytics are possible from our database.**

### Why it's blank

The CDR webhook payload from TeleCMI looks like:
```json
{
  "cmiuid": "abc-123",
  "from": 919000000000,
  "to": 918000000000,
  "duration": 125,
  "call_type": "outbound",
  "notes": [
    { "msg": "Interested", "date": 1639554230000, "agent": "103_1111112" }
  ]
}
```

The `notes[0].agent` field **contains the `telecmi_user_id`** (e.g. `"103_1111112"`). This is what we need to reverse-lookup to find the CRM `user_id` from `TeleCMIAgent.telecmi_user_id`.

Additionally for outbound calls, the agent who made the call is the `from` side. For click-to-call, we pass `extra_params.lead_id` and also need to pass `extra_params.crm_user_id` so the webhook brings it back.

### Fix in `call_log_service.py`

```python
def _resolve_agent_user_id(tenant_id, telecmi_user_id: str):
    """Map telecmi_user_id → CRM user_id via TeleCMIAgent table."""
    if not telecmi_user_id:
        return None
    from telephony.models import TeleCMIAgent
    agent = TeleCMIAgent.objects.filter(
        tenant_id=tenant_id,
        telecmi_user_id=telecmi_user_id,
        is_active=True,
    ).values('user_id').first()
    return agent['user_id'] if agent else None


def process_cdr_record(tenant_id, raw_cdr, direction, synced_via='webhook'):
    # ... existing code ...
    
    # Extract agent from notes array
    notes = raw_cdr.get('notes') or []
    telecmi_agent_id = None
    if notes and isinstance(notes, list):
        telecmi_agent_id = notes[0].get('agent')
    
    # Also check extra_params for crm_user_id (from click-to-call)
    extra = raw_cdr.get('extra_params') or {}
    crm_user_id = extra.get('crm_user_id')
    
    agent_user_id = None
    if crm_user_id:
        agent_user_id = crm_user_id  # direct mapping, already a CRM UUID
    elif telecmi_agent_id:
        agent_user_id = _resolve_agent_user_id(tenant_id, telecmi_agent_id)
    
    # Pass agent_user_id into get_or_create defaults
```

### Fix in `views.py` ClickToCallView

Also pass `crm_user_id` in extra_params when initiating click-to-call:
```python
extra_params['crm_user_id'] = str(_user_id(request))
```

---

## 3. Gap Analysis — Full List

### GAP-01: `agent_user_id` never populated ❌ CRITICAL
- **Impact:** Blocks ALL per-user analytics from our database
- **Fix:** Extract from `notes[0].agent` in CDR → reverse-lookup `TeleCMIAgent.telecmi_user_id` → set `agent_user_id`
- **Also:** Pass `crm_user_id` in `extra_params` during click-to-call for reliable attribution
- **File:** `telephony/services/call_log_service.py`, `telephony/views.py`

### GAP-02: No analytics API endpoint ❌ CRITICAL
- **Missing:** `GET /api/telephony/analytics/users/` — per-user call stats for a date range
- **Should return:**
  ```json
  {
    "date_from": "2026-07-01",
    "date_to": "2026-07-29",
    "users": [
      {
        "user_id": "uuid",
        "user_name": "Ravi Kumar",
        "role": "Sales Executive",
        "total_calls": 47,
        "answered_calls": 32,
        "missed_calls": 15,
        "outbound_calls": 30,
        "inbound_calls": 17,
        "total_talk_time_sec": 4500,
        "avg_call_duration_sec": 140,
        "calls_with_recording": 28,
        "leads_assigned": 45,
        "leads_called_today": 12
      }
    ]
  }
  ```
- **Also needed:** Day-by-day breakdown per user for trend charts
- **File:** New `telephony/views.py` `UserCallAnalyticsView`, new `telephony/analytics.py`

### GAP-03: No admin analytics dashboard page ❌ CRITICAL
- **Missing:** Frontend page/panel showing per-user telephony KPIs
- **Should show:**
  - Table: each sales exec's name, leads assigned, calls today, total talk time, missed calls, answer rate
  - Charts: calls per day per user (bar/line), talk time breakdown
  - Recording access: admin can play any recording from this view
- **File:** New `sepratecrm/src/pages/telephony/UserAnalyticsPage.tsx`

### GAP-04: Live event webhook field names unconfirmed ⚠️ MEDIUM
- **Problem:** `_normalize_live_event()` tries multiple field name guesses (`event`, `status`, etc.)
- **Risk:** If TeleCMI sends a non-matching payload shape, real-time updates silently fail
- **Fix:** Log first 10 real payloads to a `TeleCMIWebhookLog` model, then tighten the parser
- **File:** `telephony/views.py`, new migration

### GAP-05: No call disposition / outcome tracking ❌ HIGH
- **Missing:** After a call, the agent should be able to mark its outcome
- **For real estate:** Interested / Not Interested / Follow Up / Callback Requested / DND / Convert to Visit
- **Implementation:**
  - Add `outcome` and `outcome_notes` fields to `CallLog`
  - Frontend: post-call popup in `LeadTelephonyHistory` asking for disposition
  - This data is gold for AI analysis and pipeline conversion tracking
- **File:** Migration on `CallLog`, new `PUT /api/telephony/calls/<pk>/outcome/` endpoint, frontend popup

### GAP-06: No per-pipeline-stage call analytics ❌ MEDIUM
- **Missing:** How many calls have been made to leads in each stage?
- **Useful for real estate:** "We have 20 leads in 'Site Visit Scheduled' — how many have been called this week?"
- **Fix:** Join `CallLog.lead_id` with `crm.Lead.status_id` → aggregate
- **File:** New query in analytics endpoint

### GAP-07: TeleCMI Analysis API not integrated ⚠️ MEDIUM
- **Available:** `POST https://rest.telecmi.com/v2/analysis` with `appid + secret + start_date + end_date`
- **Returns:** `{ total, answered, missed }` — global totals for the tenant's account
- **Useful as:** Quick sanity check or dashboard overview stat (TeleCMI-verified total vs our DB count)
- **File:** `telephony/services/telecmi_client.py`, new view

### GAP-08: CopyPhoneButton missing in lead drawer ⚠️ MEDIUM
- **Status:** `CopyPhoneButton` EXISTS and is used correctly in `LeadDetailsPage.tsx` (line 754)
- **Gap:** Need to verify it's also in the lead drawer (`SideDrawer.tsx` / `LeadFormDrawer` / `contact-drawer`)
- **Check files:** `src/components/lead-drawer/`, `src/components/contact-drawer/`
- **Note:** The user's main workflow is: open lead drawer → see phone number → click copy → open softphone → paste → call

### GAP-09: No agent presence / "on call" status ❌ MEDIUM
- **Missing:** Admin cannot see which agents are currently active on a call in real-time
- **Implementation options:**
  - Store `is_on_call: bool` on `TeleCMIAgent`, set true on `answered` live event, false on `ended`/CDR
  - Expose via `GET /api/telephony/agents/presence/`
  - Frontend: small green/red dot next to each user in the Users page and admin dashboard

### GAP-10: No recording download button in global CallLogsPage ⚠️ LOW
- **Status:** Recording proxy exists (`/calls/<pk>/recording/`) and works in `LeadTelephonyHistory`
- **Gap:** The global `CallLogsPage.tsx` has recording UI but check if it calls the right endpoint
- **Fix:** Ensure the global call logs page can also play/download recordings

### GAP-11: No CDR webhook `direction` field validation ⚠️ MEDIUM
- **Current logic:** `_detect_direction(payload)` reads `payload.get('call_type')` to infer direction
- **Problem:** The model also uses `call_type` for missed/answered (different semantic!)
- **TeleCMI CDR payload:** `call_type` = `"inbound"` or `"outbound"` (the DIRECTION)
- **Our model field:** `call_type` = `"missed"` or `"answered"` (the OUTCOME)
- **Risk:** If TeleCMI changes the payload field name, direction detection breaks silently
- **Fix:** Add `WebhookLog` for raw payloads, and explicitly handle `call_type` as direction in CDR processing, NOT using it for both purposes

### GAP-12: No export of call data ❌ LOW
- **Missing:** Admins/managers can't export call logs to Excel/CSV
- **Fix:** `GET /api/telephony/calls/export/?format=csv&date_from=&date_to=&user_id=`
- **File:** New action on `CallLogViewSet`

### GAP-13: No daily call target tracking ❌ MEDIUM (for real estate)
- **Missing:** Manager can't set "Sales exec A should make 50 calls today"
- **Implementation:**
  - New model: `UserCallTarget(user_id, date, target_count)`
  - New endpoint: `POST /api/telephony/targets/` (admin only)
  - Dashboard shows: progress bar per exec (calls made / target)

### GAP-14: No call-level tags for AI training ❌ FUTURE
- **Missing:** No way to tag a call's content category ("price inquiry", "location question", "complaint")
- **For AI:** Training data for intent classification and auto-routing
- **Implementation:** Add `tags: JSONField` to `CallLog`, allow agent to tag after call

---

## 4. TeleCMI API Surface We're Not Using

Based on TeleCMI documentation:

| API | Status | Notes |
|-----|--------|-------|
| `POST /v2/analysis` | ❌ Not used | Returns total/answered/missed for tenant by date range |
| `POST /v2/user/in_cdr` | ✅ Used in manual sync | Returns inbound CDR per user |
| `POST /v2/user/out_cdr` | ✅ Used in manual sync | Returns outbound CDR per user |
| `POST /v2/click2call` | ✅ Implemented | |
| `POST /v2/c2c/hangup` | ✅ Implemented | |
| `POST /v2/user/notes/add` | ✅ Implemented | |
| `POST /v2/get_callerid` | ✅ Implemented | |
| `POST /v2/set_callerid` | ✅ Implemented | |
| `POST /v2/messages` (SMS) | ✅ Implemented | |
| `POST /v2/user_get_break` | ✅ Implemented | |
| `POST /v2/callback` | ✅ Implemented | |
| `GET /v2/play` | ✅ Implemented (proxy stream) | |
| `POST /v2/user/login` | ✅ Implemented | |
| `POST /v2/token` (admin token) | ✅ Implemented | |
| Webhook CDR | ✅ Implemented | |
| Webhook live events | ✅ Implemented | |

**Key missing API call: `POST /v2/analysis`** — This is the only TeleCMI-native analytics endpoint. We should hit it for cross-validation but our own DB aggregations will give richer data once `agent_user_id` is populated.

---

## 5. Phase 1 — Foundation Fixes (Must-do first)

These fixes unlock everything else. Do these before building any dashboard.

### P1-1: Fix `agent_user_id` population in CDR webhook

**Backend: `telephony/services/call_log_service.py`**

```python
def _resolve_agent_user_id(tenant_id, telecmi_user_id: str):
    """Map TeleCMI user ID string → CRM UUID via TeleCMIAgent table."""
    if not telecmi_user_id:
        return None
    from telephony.models import TeleCMIAgent
    agent = (
        TeleCMIAgent.objects
        .filter(tenant_id=tenant_id, telecmi_user_id=telecmi_user_id, is_active=True)
        .values('user_id')
        .first()
    )
    return agent['user_id'] if agent else None
```

In `process_cdr_record`:
```python
# Step 1: try extra_params.crm_user_id (set by click-to-call)
extra = raw_cdr.get('extra_params') or {}
crm_user_id_str = extra.get('crm_user_id')

# Step 2: fall back to notes[0].agent reverse-lookup
notes = raw_cdr.get('notes') or []
telecmi_agent_id = notes[0].get('agent') if notes else None

agent_user_id = None
if crm_user_id_str:
    import uuid
    try:
        agent_user_id = uuid.UUID(crm_user_id_str)
    except ValueError:
        pass
if not agent_user_id and telecmi_agent_id:
    agent_user_id = _resolve_agent_user_id(tenant_id, telecmi_agent_id)
```

**Backend: `telephony/views.py` ClickToCallView**

```python
extra_params['crm_user_id'] = str(_user_id(request))
extra_params['lead_id'] = str(data['lead_id'])
extra_params['crm'] = 'true'
```

### P1-2: Add `call_outcome` field to `CallLog`

New migration adding:
```python
class CallOutcomeEnum(models.TextChoices):
    INTERESTED = 'interested', 'Interested'
    NOT_INTERESTED = 'not_interested', 'Not Interested'
    CALLBACK = 'callback', 'Callback Requested'
    FOLLOW_UP = 'follow_up', 'Follow Up'
    CONVERTED = 'converted', 'Converted to Visit'
    DND = 'dnd', 'Do Not Disturb'
    NO_ANSWER = 'no_answer', 'No Answer / Voicemail'
    WRONG_NUMBER = 'wrong_number', 'Wrong Number'

# On CallLog:
call_outcome = models.CharField(
    max_length=20, choices=CallOutcomeEnum.choices,
    null=True, blank=True,
    help_text='Agent-set call outcome/disposition'
)
outcome_notes = models.TextField(null=True, blank=True)
outcome_set_at = models.DateTimeField(null=True, blank=True)
outcome_set_by = models.UUIDField(null=True, blank=True)
```

New endpoint: `PATCH /api/telephony/calls/<pk>/outcome/`

### P1-3: Add `TeleCMIWebhookLog` for raw payload capture

```python
class TeleCMIWebhookLog(models.Model):
    tenant_id = models.UUIDField(db_index=True)
    webhook_type = models.CharField(max_length=10)  # 'cdr' or 'live'
    raw_payload = models.JSONField()
    processed_ok = models.BooleanField(default=True)
    error_message = models.TextField(null=True, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'telephony_webhook_logs'
        # Auto-purge after 30 days via Celery task
```

Keep first 100 per tenant for debugging, then auto-delete old ones. This immediately reveals the true TeleCMI payload shape so `_normalize_live_event` can be hardened.

### P1-4: Confirm `CopyPhoneButton` in lead drawer

**Check these files for phone number display without copy button:**
- `src/components/lead-drawer/LeadDetailsForm.tsx`
- `src/components/contact-drawer/ContactDetailPanel.tsx`
- `src/components/SideDrawer.tsx` (if it renders lead phone in header)

If not present, add `<CopyPhoneButton phone={lead.phone} />` inline wherever the phone number is displayed. The component already exists and works.

---

## 6. Phase 2 — Admin Analytics Dashboard

### Backend: New analytics endpoint

**`GET /api/telephony/analytics/users/`**

Query params: `date_from`, `date_to`, `user_id` (optional filter)

Implementation using Django ORM aggregations on `CallLog`:

```python
from django.db.models import Count, Sum, Avg, Q

class UserCallAnalyticsView(APIView):
    """
    GET /api/telephony/analytics/users/
    Per-user call statistics for a date range.
    Admin only.
    """
    def get(self, request):
        from_dt = parse_date_param(request.query_params.get('date_from'))
        to_dt = parse_date_param(request.query_params.get('date_to'))
        
        qs = CallLog.objects.filter(
            tenant_id=_tenant_id(request),
            call_time__gte=from_dt,
            call_time__lte=to_dt,
            agent_user_id__isnull=False,
        ).values('agent_user_id').annotate(
            total_calls=Count('id'),
            answered_calls=Count('id', filter=Q(call_type='answered')),
            missed_calls=Count('id', filter=Q(call_type='missed')),
            outbound_calls=Count('id', filter=Q(direction='outbound')),
            inbound_calls=Count('id', filter=Q(direction='inbound')),
            total_talk_time_sec=Sum('duration'),
            avg_call_duration_sec=Avg('duration'),
            calls_with_recording=Count('id', filter=Q(recording_file__isnull=False) & ~Q(recording_file='')),
        )
        
        # Enrich with CRM user details (name, role)
        # Join with CRM User model
        # Also add: leads_assigned count, leads_called_today
        
        return Response({'users': list(qs), 'date_from': from_dt, 'date_to': to_dt})
```

**`GET /api/telephony/analytics/daily/`**

Day-by-day breakdown per user for trend charts:
```python
# Group by date(call_time) + agent_user_id
# Returns array of { date, user_id, calls, talk_time }
```

**`GET /api/telephony/analytics/overview/`**

Tenant-level summary (also calls TeleCMI `/v2/analysis` for cross-validation):
```json
{
  "total_calls_db": 847,
  "total_calls_telecmi": 850,
  "answered": 612,
  "missed": 235,
  "total_talk_time_min": 1820,
  "most_active_user": { "name": "Ravi Kumar", "calls": 120 },
  "leads_with_zero_calls": 34
}
```

### Frontend: Admin Telephony Dashboard Page

**`src/pages/telephony/UserAnalyticsPage.tsx`**

Layout:
```
┌─────────────────────────────────────────────────────────────┐
│  📊 Telephony Analytics        [Date Range Picker]  [Export]│
├──────────────────┬──────────────────┬───────────────────────┤
│ Total Calls      │ Total Talk Time  │ Answer Rate           │
│ 847              │ 30h 20m          │ 72.3%                 │
├─────────────────────────────────────────────────────────────┤
│ Per-User Performance Table                                   │
│                                                             │
│ Name         | Role     | Leads | Calls | Answered | Missed │
│              |          | Asgn  | Today | Total    | Total  │
│              |          |       |       | Talk Min | Rate   │
│ ─────────────────────────────────────────────────────────── │
│ Ravi Kumar   │ Sr. SE   │  45   │  12   │   320    │  8     │
│ Priya Singh  │ SE       │  32   │  8    │   195    │  5     │
│ ...                                                         │
├─────────────────────────────────────────────────────────────┤
│ [Bar Chart] Daily calls per user — last 7 days              │
└─────────────────────────────────────────────────────────────┘
```

Each row: click to expand → see that user's call log for the period, play their recordings.

**Conditional rendering:** This page only shows when telephony module is enabled (same pattern as other conditional modules).

---

## 7. Phase 3 — Sales Executive KPIs (Real Estate)

These are the features that make the CRM worth selling to real estate firms.

### 7.1 Sales Executive Profile Panel

For each agent, a dedicated profile page (or expandable panel) showing:

```
┌─────────────────────────────────────────────────────────────┐
│ 👤 Ravi Kumar — Sales Executive                             │
│ ────────────────────────────────────────────────────────────│
│ LEADS                    CALLS (This Month)                 │
│ Total assigned: 45       Total made: 180                    │
│ New this week: 8         Answered: 134 (74%)               │
│ In pipeline:             Missed: 46                         │
│   • Prospect: 12         Outbound: 160                      │
│   • Site Visit: 8        Inbound: 20                        │
│   • Negotiation: 5       Total talk time: 8h 20m           │
│   • Closed: 3            Avg call duration: 2m 47s          │
│                                                             │
│ TODAY: 12 calls made / 30 target (40%)  [progress bar]      │
│                                                             │
│ RECENT RECORDINGS        [▶ Play] [🔗 Lead]                 │
│ Ravi → Priya Singh  2h ago   3m 12s  Answered              │
│ Ravi → Amit Patel  3h ago   0m 00s  Missed                 │
└─────────────────────────────────────────────────────────────┘
```

### 7.2 Pipeline × Calls Matrix

Shows which pipeline stages have adequate call coverage:

```
Stage               Leads  Calls Made  Avg Calls/Lead  Coverage
────────────────────────────────────────────────────────────────
New Lead            28     0           0.0             🔴 Cold
Prospect            45     87          1.9             🟡 Warm
Site Visit Pending  12     52          4.3             🟢 Active
Negotiation          8     38          4.8             🟢 Active
Closed Won           5     --          --              ✅
Closed Lost          6     --          --              ✅
```

### 7.3 Call Outcome Funnel (Real Estate Specific)

```
Calls Made (180)
    ↓
Answered (134) ──────── Missed (46) → Auto-callback task created ✓
    ↓
Interested (45)    Not Interested (38)    Follow Up (51)
    ↓
Site Visit Booked (12)
    ↓
Converted (3)
```

This is only possible once `call_outcome` field is added (Phase 1) and agents are marking outcomes.

### 7.4 Lead "Called Today" / "Never Called" Views

Filterable views in the leads table:
- Leads never called
- Leads not called in last 7 days
- Leads with 5+ missed calls (consider re-assignment)
- Leads with no activity in 30 days

Backend: New filter `GET /api/crm/leads/?called_today=true|false&last_called_days=7`

---

## 8. Phase 4 — Real-Time Monitoring

### 8.1 Agent Presence (Who Is On A Call Right Now)

**Backend:**
```python
# Add to TeleCMIAgent model:
is_on_call = models.BooleanField(default=False)
current_call_started_at = models.DateTimeField(null=True, blank=True)

# Update in live event webhook:
# On 'answered' event → set is_on_call=True, current_call_started_at=now
# On 'ended'/'cdr' → set is_on_call=False, clear timestamp
```

**New endpoint:** `GET /api/telephony/agents/presence/`
```json
{
  "agents": [
    {
      "user_id": "uuid",
      "name": "Ravi Kumar",
      "is_on_call": true,
      "call_started_at": "2026-07-29T11:30:00Z",
      "call_duration_sec": 245
    }
  ]
}
```

**Frontend:** Real-time presence dot next to each user in admin dashboard. Pusher event `agent.status` can broadcast presence changes.

### 8.2 Live Call Feed

Admin-only feed showing calls happening right now:
```
🟢 LIVE CALLS (3)
────────────────────────────────────
Ravi Kumar → +91 9000000000    3m 12s
Priya Singh → +91 8800000000   1m 05s
Amit Patel ← +91 7700000000   0m 45s  [INBOUND]
```

Use the existing Pusher `telephony.<tenant_id>` channel — admin subscribes and updates live feed on `answered`/`ended` events.

### 8.3 Webhook Log Dashboard

Admin page showing:
- Last 50 webhook events received (from `TeleCMIWebhookLog`)
- Processing status (ok / error)
- Payload preview (for debugging)
- Webhook health indicator (last received < 1hr ago = green)

---

## 9. Phase 5 — AI Bridges for Future Agentic CRM

These features don't need AI yet — they create the data structures that AI will consume later.

### 9.1 Call Transcript Bridge

Post-call, optionally transcribe the recording:

```python
class CallTranscript(models.Model):
    call_log = models.OneToOneField(CallLog, on_delete=models.CASCADE)
    transcript_text = models.TextField()
    transcript_json = models.JSONField(null=True)  # speaker-diarized segments
    language = models.CharField(max_length=10, default='hi')  # Hindi or English
    transcription_provider = models.CharField(max_length=30)  # 'whisper', 'deepgram', etc.
    transcribed_at = models.DateTimeField(auto_now_add=True)
    confidence_score = models.FloatField(null=True)
```

**Celery task:** `transcribe_call_recording(call_log_id)` — triggered post-CDR for answered calls with recordings.

**Why now:** You can set up the model and Celery task skeleton now, keep `transcription_provider='none'`, and swap in Whisper/Deepgram/Sarvam (Indian language) when ready.

### 9.2 Call Intelligence Fields

Add to `CallLog` (or separate `CallIntelligence` model):
```python
class CallIntelligence(models.Model):
    call_log = models.OneToOneField(CallLog, on_delete=models.CASCADE)
    sentiment = models.CharField(max_length=20, null=True)  # positive/neutral/negative
    intent_tags = models.JSONField(null=True)  # ['price_inquiry', 'location_question']
    next_action_suggested = models.CharField(max_length=100, null=True)
    risk_flags = models.JSONField(null=True)  # ['long_silence', 'customer_frustrated']
    generated_by = models.CharField(max_length=50, null=True)  # 'gpt-4o', 'claude-3'
    generated_at = models.DateTimeField(null=True)
```

### 9.3 Structured Export API for AI Consumption

**`GET /api/telephony/export/ai-feed/?from=&to=`** (admin only)

Returns a structured JSONL format suitable for AI training or analysis:
```jsonl
{"call_id": 123, "agent": "Ravi Kumar", "lead_name": "Priya", "lead_stage": "Prospect", "duration": 180, "outcome": "follow_up", "transcript": "...", "recording_url": "/api/telephony/calls/123/recording/"}
{"call_id": 124, ...}
```

This is the bridge Anthropic's API (or your own model) will call when doing "analyze my team's calls this week."

### 9.4 Auto-Insights Celery Task

Weekly task that generates per-user summaries:
```python
@shared_task
def generate_weekly_telephony_insights(tenant_id):
    """
    Every Monday: for each active agent, compute:
    - Calls made vs last week (trend)
    - Answer rate vs team average
    - Most common call outcomes
    - Leads that haven't been called yet
    
    Store in AgentWeeklyInsight model.
    Frontend: show as "cards" in admin dashboard.
    """
```

---

## 10. Copy-to-Dial Button Audit

### Current Status

| Location | Phone Shown | CopyPhoneButton | Notes |
|----------|-------------|-----------------|-------|
| `LeadDetailsPage.tsx` line 754 | ✅ | ✅ DONE | Working |
| `src/components/lead-drawer/` | Need to check | ❓ | Check LeadDetailsForm.tsx |
| `src/components/contact-drawer/ContactDetailPanel.tsx` | Need to check | ❓ | Check this file |
| `CRMLeads.tsx` (table rows) | ✅ shown | ❓ | Row phone is displayed |
| `Softphone.tsx` dialpad | N/A | N/A | User pastes here |

### What to check

Run a grep across the frontend for any phone number display that's missing the copy button:
```bash
grep -r "lead\.phone\|contact\.phone\|\.phone}" src/ --include="*.tsx" | grep -v CopyPhoneButton | grep -v "CopyPhone"
```

### The full flow you want

```
Lead Detail Page / Drawer
  Phone: +91 9000000000  [📋 Copy]
                                ↓
                         Clipboard: "919000000000"
                                ↓
                         Open Softphone widget (bottom-right)
                                ↓
                         Paste → Call
```

The copy button should strip the `+` automatically so the number is paste-ready for the PIOPIY dialpad. Update `CopyPhoneButton` to normalize the number on copy:
```tsx
const normalizedForDial = phone.replace(/[^\d]/g, ''); // strip + and spaces
await navigator.clipboard.writeText(normalizedForDial);
```

---

## 11. Database Schema Changes Needed

### New fields on existing models

```sql
-- On telephony_call_logs (already have agent_user_id, just needs to be populated)
-- No schema change needed for GAP-01

-- Call disposition
ALTER TABLE telephony_call_logs ADD COLUMN call_outcome VARCHAR(20) NULL;
ALTER TABLE telephony_call_logs ADD COLUMN outcome_notes TEXT NULL;
ALTER TABLE telephony_call_logs ADD COLUMN outcome_set_at TIMESTAMPTZ NULL;
ALTER TABLE telephony_call_logs ADD COLUMN outcome_set_by UUID NULL;

-- Agent presence
ALTER TABLE telephony_agents ADD COLUMN is_on_call BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE telephony_agents ADD COLUMN current_call_started_at TIMESTAMPTZ NULL;
```

### New tables

```sql
-- Raw webhook log (for debugging)
CREATE TABLE telephony_webhook_logs (
    id BIGSERIAL PRIMARY KEY,
    tenant_id UUID NOT NULL,
    webhook_type VARCHAR(10) NOT NULL,  -- 'cdr' or 'live'
    raw_payload JSONB NOT NULL,
    processed_ok BOOLEAN NOT NULL DEFAULT TRUE,
    error_message TEXT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_tel_wh_log_tenant ON telephony_webhook_logs(tenant_id);
CREATE INDEX idx_tel_wh_log_time ON telephony_webhook_logs(received_at);

-- Daily call targets per user
CREATE TABLE telephony_user_call_targets (
    id BIGSERIAL PRIMARY KEY,
    tenant_id UUID NOT NULL,
    user_id UUID NOT NULL,
    target_date DATE NOT NULL,
    call_target INTEGER NOT NULL DEFAULT 0,
    created_by UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, user_id, target_date)
);

-- Call transcripts (Phase 5)
CREATE TABLE telephony_call_transcripts (
    id BIGSERIAL PRIMARY KEY,
    call_log_id BIGINT UNIQUE NOT NULL REFERENCES telephony_call_logs(id),
    transcript_text TEXT NOT NULL,
    transcript_json JSONB NULL,
    language VARCHAR(10) NOT NULL DEFAULT 'hi',
    transcription_provider VARCHAR(30) NOT NULL,
    transcribed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    confidence_score FLOAT NULL
);

-- Call intelligence (Phase 5)
CREATE TABLE telephony_call_intelligence (
    id BIGSERIAL PRIMARY KEY,
    call_log_id BIGINT UNIQUE NOT NULL REFERENCES telephony_call_logs(id),
    sentiment VARCHAR(20) NULL,
    intent_tags JSONB NULL,
    next_action_suggested VARCHAR(100) NULL,
    risk_flags JSONB NULL,
    generated_by VARCHAR(50) NULL,
    generated_at TIMESTAMPTZ NULL
);
```

---

## 12. Implementation Priority Matrix

| # | Feature | Phase | Effort | Impact | Do This Week |
|---|---------|-------|--------|--------|--------------|
| 1 | Fix `agent_user_id` in CDR webhook | 1 | S | 🔴 Critical | ✅ YES |
| 2 | Pass `crm_user_id` in click-to-call extra_params | 1 | XS | 🔴 Critical | ✅ YES |
| 3 | Add `call_outcome` field + endpoint | 1 | S | 🔴 Critical | ✅ YES |
| 4 | Add `TeleCMIWebhookLog` model | 1 | S | 🟡 High | ✅ YES |
| 5 | Verify `CopyPhoneButton` in lead drawer | 1 | XS | 🟡 High | ✅ YES |
| 6 | Per-user analytics API endpoint | 2 | M | 🔴 Critical | Next week |
| 7 | Admin Analytics Dashboard (frontend) | 2 | L | 🔴 Critical | Next week |
| 8 | Daily breakdown chart per user | 2 | M | 🟡 High | Next week |
| 9 | Agent presence / is_on_call | 4 | S | 🟡 High | Next sprint |
| 10 | Call outcome funnel visualization | 3 | M | 🟡 High | Next sprint |
| 11 | Pipeline × calls matrix | 3 | M | 🟡 High | Next sprint |
| 12 | Sales exec profile panel | 3 | L | 🟡 High | Next sprint |
| 13 | "Never called" / "Not called in 7 days" lead filter | 3 | S | 🟡 High | Next sprint |
| 14 | Daily call targets | 3 | M | 🟢 Medium | Future |
| 15 | TeleCMI `/v2/analysis` integration | 4 | XS | 🟢 Medium | Future |
| 16 | Live call feed (admin) | 4 | M | 🟢 Medium | Future |
| 17 | Webhook log dashboard (frontend) | 4 | S | 🟢 Medium | Future |
| 18 | CSV/XLSX export | 2 | S | 🟢 Medium | Future |
| 19 | Call transcript bridge (Whisper) | 5 | L | 🔵 AI-bridge | Future |
| 20 | Call intelligence model | 5 | M | 🔵 AI-bridge | Future |
| 21 | AI-feed export API | 5 | S | 🔵 AI-bridge | Future |
| 22 | Weekly auto-insights task | 5 | M | 🔵 AI-bridge | Future |

**Effort:** XS = few hours, S = 1 day, M = 2-3 days, L = 1 week

---

## Appendix A: TeleCMI CDR Webhook — True Payload Shape

Based on the docs and what the codebase currently handles:

```json
{
  "cmiuid": "a0b0d95b-1d58-45f4-a210-1239e29547ec",
  "duration": 125,
  "billedsec": 120,
  "rate": 0.01,
  "name": "Ravi Kumar",
  "from": 919000000000,
  "to": 918000000000,
  "time": 1639554230000,
  "call_type": "inbound",
  "record": "demo_1111113.wav",
  "notes": [
    { "msg": "Interested in premium", "date": 1639554230000, "agent": "103_1111112" }
  ]
}
```

**Key field to extract `agent_user_id` from:** `notes[0].agent` = TeleCMI user ID → lookup in `TeleCMIAgent.telecmi_user_id` → get CRM `user_id`.

The `TeleCMIWebhookLog` will confirm if `record` field is always present or sometimes named differently.

---

## Appendix B: PIOPIY Live Event Payload Shape

TeleCMI's PIOPIY SDK browser events (from frontend `piopiy.on('...')`) vs webhook events are separate:

- Browser SDK: `inComingCall`, `ringing`, `answered`, `hangup`, `ended`, `hold`, `unhold` — these drive the softphone UI ✅ already wired
- Webhook live events: POSTed to `/webhook/live/` — these drive `_normalize_live_event()` which is currently guessing field names

The `TeleCMIWebhookLog` will show us the exact fields in the webhook live event payload within the first few real calls.

---

*Plan authored after full codebase audit of `digicrm/telephony/` and `sepratecrm/src/` on 2026-07-29.*
