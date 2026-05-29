# Telephony API — TeleCMI Integration

Complete API reference for the `telephony` module. Covers every backend endpoint, request/response shapes, the WebRTC browser SDK integration, webhook setup, and all DB changes made in this session.

---

## Table of contents

1. [Overview](#1-overview)
2. [Authentication](#2-authentication)
3. [Setup flow](#3-setup-flow)
4. [Credentials management](#4-credentials-management)
5. [Agent configuration](#5-agent-configuration)
6. [Call control](#6-call-control)
7. [Call logs (CDR)](#7-call-logs-cdr)
8. [SMS](#8-sms)
9. [Caller ID](#9-caller-id)
10. [Break management](#10-break-management)
11. [Callbacks](#11-callbacks)
12. [WebRTC config](#12-webrtc-config)
13. [Webhooks](#13-webhooks)
14. [Error reference](#14-error-reference)
15. [Frontend WebRTC integration guide](#15-frontend-webrtc-integration-guide)
16. [Backend changes summary](#16-backend-changes-summary)

---

## 1. Overview

The telephony module connects DigiCRM to TeleCMI's cloud phone system. It provides:

- **Click-To-Call** — dial any lead's number from the browser; TeleCMI rings the agent's registered softphone first, then bridges to the contact
- **In-browser calling** — via the PIOPIY WebRTC SDK (`piopiyjs`), agents can make and receive calls entirely inside the CRM tab — no separate softphone app required
- **Call history** — all CDR (call detail records) synced from TeleCMI, linked to CRM Leads, and written as `LeadActivity` records
- **SMS** — send SMS to any number; logged as `LeadActivity(type=SMS)` on the lead
- **Missed call → Task** — missed inbound calls auto-create a callback Task on the matched lead
- **Webhooks** — TeleCMI pushes CDR and live call events to this backend in real time

**Base path:** `/api/telephony/`

**All authenticated endpoints require:** `Authorization: Bearer <jwt_token>`

---

## 2. Authentication

All endpoints except webhooks require the standard CRM JWT. The JWT is issued by the superadmin system and carries `tenant_id`, `user_id`, `permissions`, and `enabled_modules`.

```
Authorization: Bearer eyJhbGciOiJIUzI1NiJ9...
```

Webhooks are **public** (no JWT). They are optionally secured by a shared `webhook_secret` header instead (see [§13 Webhooks](#13-webhooks)).

---

## 3. Setup flow

Before any agent can make calls, two setup steps must be completed in the CRM settings UI:

### Step 1 — Tenant admin: connect TeleCMI account

`POST /api/telephony/credentials/` with the TeleCMI `app_id` and `secret`. One record per tenant.

### Step 2 — Each agent: register their TeleCMI credentials

`POST /api/telephony/agents/` with the agent's TeleCMI `telecmi_user_id` and `password`. One record per CRM user.

After both steps, the agent can make calls and the backend auto-manages TeleCMI login tokens.

### Step 3 — Configure TeleCMI webhooks (admin, one-time)

In the [TeleCMI dashboard](https://connle.telecmi.com/login), set:
- CDR webhook URL: `https://your-domain.com/api/telephony/webhook/cdr/?tenant_id=<your-tenant-uuid>`
- Live events URL: `https://your-domain.com/api/telephony/webhook/live/?tenant_id=<your-tenant-uuid>`

---

## 4. Credentials management

### `GET /api/telephony/credentials/`
List TeleCMI credentials for the current tenant.

**Response 200**
```json
{
  "count": 1,
  "results": [
    {
      "id": 1,
      "app_id": "12345",
      "sbc_region": "ind",
      "sbc_host": "sbcind.telecmi.com",
      "default_caller_id": "+918000000000",
      "webhook_secret": "my-webhook-secret",
      "is_active": true,
      "created_at": "2026-05-29T10:00:00Z",
      "updated_at": "2026-05-29T10:00:00Z"
    }
  ]
}
```

> `secret` is **write-only** and is never returned.

---

### `POST /api/telephony/credentials/`
Create TeleCMI tenant credentials.

**Request body**
```json
{
  "app_id": "12345",
  "secret": "xxxx-xxxx-xxxx-xxxx",
  "sbc_region": "ind",
  "default_caller_id": "+918000000000",
  "webhook_secret": "optional-shared-secret"
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `app_id` | string | yes | TeleCMI App ID |
| `secret` | string | yes (on create) | TeleCMI app secret. Write-only; stored encrypted. |
| `sbc_region` | string | no | `sg`, `ind`, `us`, `uk`. Default: `ind` |
| `default_caller_id` | string | no | Default outbound caller ID |
| `webhook_secret` | string | no | Shared secret for webhook verification |

**`sbc_region` values**

| Value | Region | SBC host |
|---|---|---|
| `ind` | India | `sbcind.telecmi.com` |
| `sg` | Asia (Singapore) | `sbcsg.telecmi.com` |
| `us` | Americas | `sbcus.telecmi.com` |
| `uk` | Europe | `sbcuk.telecmi.com` |

**Response 201** — same shape as GET response

---

### `PATCH /api/telephony/credentials/<id>/`
Update credentials (e.g. rotate secret, change caller ID).

**Request body** — any subset of the POST fields.

```json
{
  "secret": "new-secret-value",
  "default_caller_id": "+918111111111"
}
```

**Response 200** — updated credential object

---

### `DELETE /api/telephony/credentials/<id>/`
Delete credentials. **Response 204**

---

## 5. Agent configuration

Each CRM user who will make/receive calls needs one agent record per tenant.

### `GET /api/telephony/agents/`
List agent configs. Non-admin users only see their own record.

**Response 200**
```json
{
  "count": 1,
  "results": [
    {
      "id": 1,
      "user_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
      "telecmi_user_id": "103_1111112",
      "token_is_fresh": true,
      "is_active": true,
      "created_at": "2026-05-29T10:00:00Z",
      "updated_at": "2026-05-29T10:00:00Z"
    }
  ]
}
```

> `password` is **write-only** and never returned.
> `token_is_fresh` — `true` if the cached TeleCMI token is less than 20 hours old.

---

### `POST /api/telephony/agents/`
Register a TeleCMI agent for a CRM user.

**Request body**
```json
{
  "user_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
  "telecmi_user_id": "103_1111112",
  "password": "agent-password"
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `user_id` | UUID | yes | The CRM user's UUID |
| `telecmi_user_id` | string | yes | TeleCMI user ID (format: `<extension>_<appid>`) |
| `password` | string | yes (on create) | TeleCMI agent password. Write-only; stored encrypted. |

**Response 201** — agent object (without password)

---

### `PATCH /api/telephony/agents/<id>/`
Update agent config (e.g. new password).

**Request body**
```json
{ "password": "new-password" }
```

---

### `POST /api/telephony/agents/refresh-token/`
Force a fresh TeleCMI token for the currently authenticated user.  
Useful after a password change or if calls start failing with 401.

**Request body** — empty `{}`

**Response 200**
```json
{ "detail": "Token refreshed successfully." }
```

**Response 424** — no agent config exists for this user

---

## 6. Call control

### `POST /api/telephony/calls/click-to-call/`
Initiates a Click-To-Call. TeleCMI rings the agent's softphone first. Once the agent answers, TeleCMI dials `to_number`.

**Request body**
```json
{
  "to_number": "919000000000",
  "caller_id": "918000000000",
  "lead_id": 42,
  "extra_params": {}
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `to_number` | string | yes | Destination phone number with country code (no `+`) |
| `caller_id` | string | no | Override the caller ID for this call only |
| `lead_id` | integer | no | CRM Lead ID. Attached as `extra_params.lead_id` so CDR webhooks can link back. |
| `extra_params` | object | no | Any additional key-values forwarded to TeleCMI |

**Response 200**
```json
{
  "code": 200,
  "msg": "Call initiated",
  "request_id": "s96C6XK1BUHX0oVZfOo5NhoJfZZJd0y1nmrNN6dhdkW"
}
```

**Response 424** — TeleCMI agent not configured for this user  
**Response 502** — TeleCMI API error (check `error` field)

---

### `POST /api/telephony/calls/hangup/`
Hang up an active call by its TeleCMI Leg B UUID. The `cmiuuid` comes from a live event webhook payload.

**Request body**
```json
{
  "cmiuuid": "a0b0d95b-1d58-45f4-a210-1239e29547ec"
}
```

**Response 200** — TeleCMI hangup confirmation

---

### `POST /api/telephony/calls/add-note/`
Add a note to a call record in TeleCMI (visible in TeleCMI's call history).

**Request body**
```json
{
  "from_number": "919000000000",
  "caller_name": "Ravi Kumar",
  "timestamp_ms": 1639554230000,
  "message": "Interested in the premium plan, call back next week."
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `from_number` | string | yes | Caller's phone number |
| `caller_name` | string | no | Caller's name |
| `timestamp_ms` | integer | yes | UTC timestamp of the call in milliseconds |
| `message` | string | yes | Note text |

**Response 200** — TeleCMI confirmation

---

## 7. Call logs (CDR)

Call logs are populated automatically from TeleCMI webhooks, or manually via the sync endpoint. They are automatically linked to CRM Leads by phone number, and a `LeadActivity(type=CALL)` is created on the matched lead.

### `GET /api/telephony/calls/`
List all call logs for the current tenant.

**Query parameters**

| Param | Type | Description |
|---|---|---|
| `direction` | string | Filter: `inbound` or `outbound` |
| `call_type` | string | Filter: `missed` or `answered` |
| `lead_id` | integer | Filter by lead |
| `agent_user_id` | UUID | Filter by agent |
| `ordering` | string | `call_time`, `-call_time`, `duration`, `-duration` |
| `page` | integer | Page number |
| `page_size` | integer | Results per page |

**Response 200**
```json
{
  "count": 47,
  "next": "http://…/api/telephony/calls/?page=2",
  "previous": null,
  "results": [
    {
      "id": 1,
      "cmiuid": "a0b0d95b-1d58-45f4-a210-1239e29547ec",
      "direction": "inbound",
      "direction_display": "Inbound",
      "call_type": "answered",
      "call_type_display": "Answered",
      "from_number": "919000000000",
      "to_number": "918000000000",
      "duration": 125,
      "billed_sec": 120,
      "rate": "0.0100",
      "caller_name": "Ravi Kumar",
      "telecmi_notes": [
        { "msg": "Interested in premium", "date": 1639554230000, "agent": "103_1111112" }
      ],
      "call_time": "2021-12-15T07:43:50Z",
      "lead_id": 42,
      "agent_user_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
      "synced_via": "webhook",
      "created_at": "2026-05-29T10:00:00Z"
    }
  ]
}
```

---

### `GET /api/telephony/calls/<id>/`
Single call log detail. Same fields as above.

---

### `POST /api/telephony/calls/sync/`
Manually pull CDR from TeleCMI for the current agent and upsert into call logs. Useful for backfilling history or recovering from missed webhooks.

**Request body**
```json
{
  "hours_back": 24
}
```

| Field | Type | Description |
|---|---|---|
| `hours_back` | integer | How many hours of history to sync. Default: 24. Max: 720 (30 days). |

**Response 200**
```json
{
  "created": 8,
  "updated": 2,
  "errors": 0
}
```

---

## 8. SMS

### `POST /api/telephony/sms/send/`
Send an SMS to any phone number. The message is delivered via TeleCMI. If `lead_id` is provided, a `LeadActivity(type=SMS)` is created on the lead.

**Request body**
```json
{
  "to_number": "919000000000",
  "message": "Your appointment is confirmed for tomorrow at 10 AM.",
  "lead_id": 42
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `to_number` | string | yes | Recipient phone number with country code |
| `message` | string | yes | SMS text. ~160 chars per segment. |
| `lead_id` | integer | no | CRM Lead ID to link this SMS to |

**Response 201**
```json
{
  "id": 5,
  "from_number": null,
  "to_number": "919000000000",
  "message": "Your appointment is confirmed for tomorrow at 10 AM.",
  "status": "sent",
  "status_display": "Sent",
  "lead_id": 42,
  "sent_by_user_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
  "error_message": null,
  "created_at": "2026-05-29T10:00:00Z"
}
```

**Response 502** — TeleCMI API error. The SMS log is still created with `status: "failed"`.

```json
{
  "error": "Invalid user token",
  "sms_log_id": 5
}
```

---

### `GET /api/telephony/sms/`
List outgoing SMS logs for the current tenant.

**Query parameters**

| Param | Type | Description |
|---|---|---|
| `status` | string | Filter: `sent` or `failed` |
| `lead_id` | integer | Filter by lead |
| `sent_by_user_id` | UUID | Filter by sender |
| `ordering` | string | `created_at`, `-created_at` |

**Response 200** — paginated list of SMS log objects (same shape as 201 response above)

---

## 9. Caller ID

### `GET /api/telephony/caller-ids/`
List all caller IDs available to the current agent (fetched live from TeleCMI).

**Response 200** — TeleCMI's raw response
```json
{
  "code": 200,
  "callerids": [
    { "callerid": "918000000000", "name": "Main Office" },
    { "callerid": "918111111111", "name": "Support Line" }
  ]
}
```

---

### `POST /api/telephony/caller-ids/`
Set the active caller ID for the current agent.

**Request body**
```json
{
  "caller_id": "918111111111"
}
```

**Response 200** — TeleCMI confirmation

---

## 10. Break management

### `GET /api/telephony/break/`
Retrieve break records for the current agent.

**Query parameters**

| Param | Type | Description |
|---|---|---|
| `from_date_ms` | integer | UTC millisecond timestamp. Defaults to last 24 hours. |

**Response 200** — TeleCMI's raw break records

---

## 11. Callbacks

### `GET /api/telephony/callbacks/`
List callback records from TeleCMI for the current agent. These are call-back requests (missed calls that the caller requested a return call for).

**Query parameters**

| Param | Type | Description |
|---|---|---|
| `from_ts` | integer | UTC ms. Default: 24 hours ago |
| `to_ts` | integer | UTC ms. Default: now |
| `page` | integer | Default: 1 |
| `limit` | integer | Max 10. Default: 10 |

**Response 200** — TeleCMI's raw callback list

---

## 12. WebRTC config

### `GET /api/telephony/webrtc-config/`
Returns the configuration the frontend PIOPIY SDK needs to call `piopiy.login()`. Does **not** expose the agent's password.

**Response 200**
```json
{
  "telecmi_user_id": "103_1111112",
  "sbc_host": "sbcind.telecmi.com",
  "default_caller_id": "+918000000000"
}
```

The frontend uses `telecmi_user_id` and the agent's password to authenticate with PIOPIY. The password is **never** returned by this endpoint — the agent should enter it once during the settings setup flow and it is stored encrypted.

> **Note:** For the in-browser SDK, the agent authenticates directly against TeleCMI's SBC server using their TeleCMI credentials. The backend does not proxy the WebRTC audio stream — only call events are proxied via the REST API.

**Response 424** — TeleCMI is not configured for this tenant, or this user has no agent record.

---

## 13. Webhooks

Webhook endpoints are **public** (no JWT). They must be registered in the TeleCMI dashboard pointing to your server.

### Webhook URL format

```
POST https://your-domain.com/api/telephony/webhook/cdr/?tenant_id=<your-tenant-uuid>
POST https://your-domain.com/api/telephony/webhook/live/?tenant_id=<your-tenant-uuid>
```

The `tenant_id` query parameter identifies which tenant the webhook belongs to.

### Optional webhook secret

If you set a `webhook_secret` on your tenant credentials, TeleCMI must include it in every webhook request as a header:

```
X-Webhook-Secret: your-secret-value
```

Requests with a wrong or missing secret receive **401 Unauthorized**.

---

### `POST /api/telephony/webhook/cdr/?tenant_id=<uuid>`
**CDR webhook** — called by TeleCMI after every call ends.

**Payload from TeleCMI**
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
  "notes": [
    { "msg": "Support query", "date": 1639554230000, "agent": "103_1111112" }
  ]
}
```

**What the backend does automatically:**
1. Determines call direction from `call_type` field (`inbound`/`outbound`)
2. Detects missed vs answered from `duration` (0 = missed)
3. Matches `from` / `to` phone number against CRM Leads (exact match, then 10-digit suffix match)
4. Creates / upserts a `CallLog` record (idempotent — safe to re-send)
5. Creates a `LeadActivity(type=CALL)` on the matched lead (once only)
6. For missed inbound calls with a matched lead: auto-creates a Task titled `"Call back: <number>"`

**Response 200**
```json
{ "status": "ok" }
```

---

### `POST /api/telephony/webhook/live/?tenant_id=<uuid>`
**Live events webhook** — called by TeleCMI during an active call (ringing, answered, ended).

**Payload examples**

*Ringing:*
```json
{ "event": "ringing", "from": "919000000000", "cmiuid": "abc-123" }
```

*Answered:*
```json
{ "event": "answered", "cmiuid": "abc-123", "duration": 0 }
```

*Ended:*
```json
{ "event": "ended", "cmiuid": "abc-123", "duration": 45 }
```

The backend currently logs the event and responds with `{ "status": "ok" }`. Future versions will push these events to the browser via WebSocket/SSE to drive the in-browser softphone widget state.

---

## 14. Error reference

### HTTP status codes

| Code | Meaning |
|---|---|
| `200` | Success |
| `201` | Resource created |
| `204` | No content (DELETE) |
| `400` | Validation error — check the `errors` object |
| `401` | No/invalid JWT token (or invalid webhook secret) |
| `403` | CRM module not enabled for this user |
| `404` | Object not found |
| `424 Failed Dependency` | TeleCMI not configured for this user/tenant |
| `502 Bad Gateway` | TeleCMI API returned an error — check the `error` string |

### Error body shape

```json
{
  "error": "Human-readable error message from TeleCMI or the backend"
}
```

Validation errors (400):
```json
{
  "to_number": ["This field is required."],
  "message": ["This field may not be blank."]
}
```

---

## 15. Frontend WebRTC integration guide

### Install the SDK

```bash
npm install piopiyjs
# or
yarn add piopiyjs
```

### Initialize and login

```javascript
import PIOPIY from 'piopiyjs';

const piopiy = new PIOPIY({
  name: 'Agent Display Name',
  debug: false,
  autoplay: true,   // handle audio stream automatically
  ringTime: 60,     // seconds to ring before auto-reject
});

// 1. Fetch config from the backend (user must be logged in to CRM)
const config = await fetch('/api/telephony/webrtc-config/', {
  headers: { Authorization: `Bearer ${jwtToken}` }
}).then(r => r.json());

// config = { telecmi_user_id: "103_1111112", sbc_host: "sbcind.telecmi.com", ... }

// 2. Login to TeleCMI SBC (agent enters their password once in settings)
//    Store the password securely in the CRM settings UI — never in localStorage
piopiy.login(config.telecmi_user_id, agentPassword, config.sbc_host);
```

### Event handlers — wire these to your softphone widget state

```javascript
piopiy.on('login', ({ code }) => {
  if (code === 200) setCallStatus('ready');
});

piopiy.on('loginFailed', ({ code }) => {
  console.error('TeleCMI login failed:', code);
  setCallStatus('error');
});

piopiy.on('inComingCall', (payload) => {
  // payload contains caller number, cmiuid etc.
  setCallStatus('ringing-inbound');
  setCallerInfo(payload);
});

piopiy.on('trying',   () => setCallStatus('dialling'));
piopiy.on('ringing',  () => setCallStatus('ringing-outbound'));
piopiy.on('answered', () => setCallStatus('active'));
piopiy.on('callStream', ({ stream }) => {
  // Attach MediaStream to an <audio> element if autoplay=false
});

piopiy.on('hangup',  () => setCallStatus('ready'));
piopiy.on('ended',   ({ code }) => {
  setCallStatus('ready');
  if (code !== 200) console.warn('Call ended with code', code);
});

piopiy.on('hold',   () => setCallStatus('on-hold'));
piopiy.on('unhold', () => setCallStatus('active'));
```

### Make an outbound call

```javascript
// Option A — Click-To-Call (backend triggers it, rings agent's softphone)
const response = await fetch('/api/telephony/calls/click-to-call/', {
  method: 'POST',
  headers: { Authorization: `Bearer ${jwt}`, 'Content-Type': 'application/json' },
  body: JSON.stringify({ to_number: '919000000000', lead_id: 42 }),
});

// Option B — Direct WebRTC call (audio in browser)
piopiy.call('919000000000', { lead_id: '42' });
```

### Handle inbound call in browser

```javascript
// Show incoming call UI, then:
piopiy.answer();  // accept
// or
piopiy.reject();  // decline
```

### Call controls during active call

```javascript
piopiy.hold();           // put on hold
piopiy.unHold();         // resume
piopiy.mute();           // mute microphone
piopiy.unMute();         // unmute
piopiy.sendDtmf('1');    // press DTMF tone (IVR navigation)
piopiy.transfer('918111111111');  // transfer to another number/extension
piopiy.merge();          // merge after transfer
piopiy.terminate();      // hang up
piopiy.getCallId();      // returns current call ID
```

### Logout

```javascript
piopiy.logout();
piopiy.on('logout', ({ code }) => {
  if (code === 200) setCallStatus('disconnected');
});
```

### Recommended softphone widget state machine

```
disconnected
    ↓ (login called)
ready
    ↓ (inComingCall event)         ↓ (piopiy.call() / click-to-call)
ringing-inbound                  dialling
    ↓ (piopiy.answer())               ↓ (ringing event)
    ↓                            ringing-outbound
    ↓                                 ↓ (answered)
    └─────────→  active  ←────────────┘
                   ↓ (hold)
                on-hold
                   ↓ (unhold)
                 active
                   ↓ (hangup / ended)
                  ready
```

---

## 16. Backend changes summary

### New files

| Path | Purpose |
|---|---|
| `telephony/apps.py` | Django app config |
| `telephony/models.py` | `TeleCMICredential`, `TeleCMIAgent`, `CallLog`, `SMSLog` |
| `telephony/serializers.py` | DRF serializers for all resources |
| `telephony/views.py` | All view classes |
| `telephony/urls.py` | URL routing for the app |
| `telephony/migrations/0001_initial.py` | Creates all telephony DB tables |
| `telephony/migrations/0002_add_credential_ordering.py` | Adds `-created_at` ordering to credential model |
| `telephony/services/telecmi_client.py` | Raw HTTP adapter for TeleCMI REST API |
| `telephony/services/token_service.py` | Per-user token get / cache / refresh |
| `telephony/services/call_log_service.py` | CDR sync, phone→Lead matching, Activity creation |
| `telephony/services/callback_service.py` | Missed call → Task auto-creation |
| `telephony/tests/test_models.py` | 13 model tests |
| `telephony/tests/test_services.py` | 30 service tests |
| `telephony/tests/test_views.py` | 29 view + webhook tests |

### Modified files

| Path | What changed |
|---|---|
| `digicrm/settings.py` | Added `'telephony'` to `INSTALLED_APPS` |
| `digicrm/urls.py` | Added `path('api/telephony/', include('telephony.urls'))` |
| `integrations/models.py` | Added `TELECMI = 'TELECMI', 'TeleCMI Telephony'` to `IntegrationTypeEnum` |
| `common/middleware.py` | Added `'/api/telephony/webhook/'` to `PUBLIC_PATHS` so webhooks bypass JWT auth |

### New database tables

| Table | Purpose |
|---|---|
| `telephony_credentials` | One row per tenant — TeleCMI app_id, encrypted secret, SBC region |
| `telephony_agents` | One row per CRM user — TeleCMI user_id, encrypted password, cached token |
| `telephony_call_logs` | Normalized CDR records synced from TeleCMI; linked to leads |
| `telephony_sms_logs` | Record of every SMS sent through the CRM |

### Automatic CRM side-effects

When a CDR webhook arrives (or manual sync runs):

1. **`CallLog`** created/updated (idempotent by `cmiuid`)
2. **`LeadActivity(type=CALL)`** created on the matched lead — visible in the lead activity feed
3. **`Task`** created titled `"Call back: <number>"` — only for missed inbound calls with a matched lead

When SMS is sent:

1. **`SMSLog`** created
2. **`LeadActivity(type=SMS)`** created on the lead (if `lead_id` was provided)

### Tenant isolation

All data is scoped to `tenant_id`. The `TenantViewSetMixin` on every ViewSet filters queryset to `request.tenant_id` automatically. Cross-tenant data access is impossible at the ORM layer.

---

*Generated from code review — all shapes are verified against the live implementation and 72/72 tests passing.*
