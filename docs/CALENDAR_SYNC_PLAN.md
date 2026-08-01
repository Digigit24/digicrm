# Calendar Sync & Meetings Integration — Implementation Plan

> **Goal:** Let every team member connect their own Google Calendar (and, via open standards, iCloud / Fastmail / Nextcloud / Zoho / Outlook-CalDAV) so that DigiCRM shows a unified calendar in one UI — CRM Meetings **plus** each user's external events — with per-tenant, per-user, and team-level visibility control.
>
> **TL;DR answers to your questions:**
> - **Is Composio required?** **No.** You already own 90% of the plumbing (Google OAuth, encrypted token vault, Celery polling, multi-tenant `Connection` model). Build Calendar natively now. Adopt Composio *later* as a breadth layer for the long tail of integrations + AI-agent actions — see §9.
> - **Use CalDAV / open standards?** **Yes — as the second track**, alongside the native Google Calendar API. Google Calendar API for Google accounts (better webhooks/latency), CalDAV + iCalendar (RFC 4791 / RFC 5545) for everyone else. This is the "proper open standardisation" path.
> - **Tenant-wise syncing?** Already natural. `Connection` carries both `tenant_id` and `user_id`. Each user connects their own calendar; RBAC scopes (`own` / `team` / `all`) already used by Meetings decide who sees whose calendar. See §7.

---

## 1. What you already have (reuse this — don't rebuild)

The integrations app is a production-grade, Zapier-style engine. The calendar feature slots directly into it.

| Asset | Location | How the calendar feature reuses it |
|-------|----------|-------------------------------------|
| **Google OAuth 2.0 flow** | `integrations/utils/oauth.py` (`GoogleOAuthHandler`) | Add Calendar scopes; issue authorization URL + exchange/refresh tokens. **One change needed** — see §5.1. |
| **OAuth connect/callback endpoints** | `integrations/views.py` → `ConnectionViewSet.initiate_oauth` / `oauth_callback` | State already encodes `tenant_id:user_id:uuid` (`views.py:166`) and is cached 10 min (`views.py:175`). Works as-is for Calendar. |
| **Encrypted token vault** | `integrations/utils/encryption.py` (Fernet, `encrypt_token`/`decrypt_token`) | Store Google refresh tokens **and** CalDAV app-passwords encrypted at rest. |
| **Multi-tenant Connection model** | `integrations/models.py` → `Connection` (`tenant_id`, `user_id`, `access_token_encrypted`, `refresh_token_encrypted`, `token_expires_at`, `connection_data` JSON, `status`) | This IS your "calendar account" table. One row per connected calendar per user. No new auth table needed. |
| **Integration registry** | `integrations/models.py` → `Integration` + `IntegrationTypeEnum` | Add `GOOGLE_CALENDAR` and `CALDAV` enum values + registry rows. `oauth_config` JSON already holds per-integration scopes. |
| **Celery + Beat** | `digicrm/celery.py`, `CELERY_BEAT_SCHEDULE` (`settings.py:477`) | Schedule incremental sync + token refresh, exactly like `poll_workflow_triggers`. |
| **Token-refresh task pattern** | `integrations/tasks.py` (`refresh_expiring_tokens`) | Clone for calendar connections. |
| **Meetings API + calendar view** | `meetings/views.py` (`MeetingViewSet.calendar`, groups by date) | Becomes one *source* in the unified feed; extend to merge external events. |
| **RBAC with scopes** | `common/permissions.py` (`HasCRMPermission`, `own`/`team`/`all`), `common/mixins.py` (`TenantViewSetMixin`) | Drives "see only my calendar" vs "see the whole team's". |

**Multi-tenancy recap** (from `docs/superadmin-auth-multitenancy.md`): no local user table — a shared SuperAdmin JWT sets `request.tenant_id`, `request.user_id`, `request.permissions` (nested, scope-aware), `request.enabled_modules`. Every calendar row is keyed by `tenant_id` + `user_id`; isolation is automatic.

---

## 2. Standards primer — pick the right protocol per provider

| Protocol | Standard | Best for | Sync mechanism | Notes |
|----------|----------|----------|----------------|-------|
| **Google Calendar API** | Google REST v3 | Google Workspace / Gmail | **Incremental `syncToken` + push (watch channels → webhook)** | Real-time, efficient. You already have the OAuth. |
| **CalDAV** | RFC 4791 (+ iCalendar RFC 5545) | iCloud, Fastmail, Nextcloud, Zoho, Yahoo, generic | **`sync-collection` REPORT (RFC 6578) or ETag polling** | The open-standard track. No native webhooks → poll on an interval. |
| **Microsoft Graph** *(optional, phase 4)* | Graph REST | Outlook / Office 365 | Delta query + subscriptions | Outlook *does* speak CalDAV too, but Graph is richer. Defer. |
| **ICS feed (read-only)** | iCalendar RFC 5545 | "Subscribe to a public URL" | Periodic fetch | Cheapest fallback; one-way, read-only. Good for external/shared calendars. |

**Recommendation:** Two first-class tracks — **Google Calendar API** and **CalDAV** — sharing one internal event model and one sync engine. That covers Google + essentially every standards-compliant provider through one CalDAV client (`caldav` + `icalendar` Python libs). Add Microsoft Graph only if a tenant specifically needs Outlook and CalDAV-for-Outlook proves too limited.

---

## 3. Target architecture

```
                        ┌─────────────────────────────────────────────┐
                        │              Unified Calendar UI             │
                        │   (CRM Meetings + each user's ext. events)   │
                        └───────────────────────┬─────────────────────┘
                                                │  GET /api/calendar/unified/?scope=team
                                                ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                              calendar module (new)                              │
│                                                                                 │
│   CalendarAccount ── 1:1 ──► integrations.Connection  (OAuth tokens / CalDAV)   │
│        │                     (tenant_id + user_id + encrypted creds)            │
│        ▼                                                                         │
│   ExternalCalendarEvent  (local read-mostly mirror of provider events)          │
│        ▲                                                                         │
│        │ two-way sync                                                            │
│   meetings.Meeting  (CRM-native; optionally pushed OUT to Google/CalDAV)        │
│                                                                                 │
│   SyncEngine:  pull (syncToken / sync-collection)  +  push (create/patch/delete)│
└───────┬─────────────────────────────────────────┬───────────────────────────────┘
        │ Google Calendar API v3                   │ CalDAV (RFC 4791) + iCalendar
        ▼                                          ▼
  Google watch channel  ──webhook──►  /api/calendar/webhook/google/<channel>/
  CalDAV                ──Celery Beat poll every N min──►  SyncEngine.pull()
```

**Why a new `calendar` app (not just extend `meetings`)?**
- `Meeting` is CRM-owned business data (linked to a `Lead`, has notes, drives workflows). External events are read-mostly, high-volume, and provider-shaped. Mixing them pollutes the Meetings table and its uniqueness constraints.
- Keep `Meeting` as the *authoritative CRM* object; keep `ExternalCalendarEvent` as the *mirror*. The unified view is a **merge/read model** over both. A `Meeting` can optionally be *projected out* to a provider as an event (with a stored `external_event_id` back-reference).

---

## 4. Data model (new `calendar` app)

### 4.1 `CalendarAccount` — one connected calendar source per user
```python
class CalendarProviderEnum(models.TextChoices):
    GOOGLE  = 'GOOGLE',  'Google Calendar'
    CALDAV  = 'CALDAV',  'CalDAV (iCloud/Fastmail/Nextcloud/…)'
    ICS     = 'ICS',     'ICS feed (read-only)'
    # MICROSOFT = 'MICROSOFT', 'Outlook / Microsoft 365'   # phase 4

class CalendarAccount(models.Model):
    id             = models.BigAutoField(primary_key=True)
    tenant_id      = models.UUIDField(db_index=True)
    user_id        = models.UUIDField(db_index=True)          # calendar owner
    provider       = models.CharField(max_length=20, choices=CalendarProviderEnum.choices)
    connection     = models.ForeignKey('integrations.Connection', on_delete=models.CASCADE,
                                       related_name='calendar_accounts')   # reuses token vault
    provider_account_email = models.CharField(max_length=255, null=True)   # e.g. gmail address
    caldav_url     = models.URLField(null=True, blank=True)                 # CalDAV/ICS base
    # per-calendar-list selection (a Google account has many calendars):
    calendars      = models.JSONField(default=list)   # [{id, name, color, selected, read_only}]
    sync_token     = models.TextField(null=True, blank=True)   # Google syncToken / CalDAV sync-token
    sync_direction = models.CharField(max_length=10, default='BOTH')  # IN | OUT | BOTH
    is_shared_with_team = models.BooleanField(default=False)   # user opt-in to team visibility
    watch_channel_id     = models.CharField(max_length=255, null=True)  # Google push channel
    watch_resource_id    = models.CharField(max_length=255, null=True)
    watch_expires_at     = models.DateTimeField(null=True)
    status         = models.CharField(max_length=20, default='CONNECTED')
    last_synced_at = models.DateTimeField(null=True)
    last_error     = models.TextField(null=True, blank=True)
    created_at/updated_at ...

    class Meta:
        constraints = [UniqueConstraint(fields=['tenant_id','user_id','provider','provider_account_email'],
                                        name='uniq_calendar_account_per_user')]
        indexes = [Index(fields=['tenant_id','user_id']), Index(fields=['status'])]
```

### 4.2 `ExternalCalendarEvent` — local mirror of provider events
```python
class ExternalCalendarEvent(models.Model):
    id                = models.BigAutoField(primary_key=True)
    tenant_id         = models.UUIDField(db_index=True)
    account           = models.ForeignKey(CalendarAccount, on_delete=models.CASCADE,
                                           related_name='events')
    external_id       = models.CharField(max_length=512)    # provider event id / iCal UID
    external_calendar_id = models.CharField(max_length=255) # which sub-calendar
    ical_uid          = models.CharField(max_length=512, null=True)
    etag              = models.CharField(max_length=255, null=True)  # CalDAV change detection
    title             = models.TextField()
    description       = models.TextField(null=True, blank=True)
    location          = models.TextField(null=True, blank=True)
    start_at          = models.DateTimeField(db_index=True)
    end_at            = models.DateTimeField()
    all_day           = models.BooleanField(default=False)
    timezone          = models.CharField(max_length=64, null=True)
    status            = models.CharField(max_length=20)     # confirmed/tentative/cancelled
    organizer_email   = models.CharField(max_length=255, null=True)
    attendees         = models.JSONField(default=list)
    recurrence        = models.JSONField(null=True)         # RRULE etc.
    raw               = models.JSONField(null=True)         # provider payload for round-trips
    # link back to a CRM meeting if this event originated here:
    source_meeting    = models.ForeignKey('meetings.Meeting', null=True, blank=True,
                                           on_delete=models.SET_NULL, related_name='external_projections')
    deleted           = models.BooleanField(default=False)  # tombstone from provider
    created_at/updated_at ...

    class Meta:
        constraints = [UniqueConstraint(fields=['account','external_calendar_id','external_id'],
                                        name='uniq_event_per_account_calendar')]
        indexes = [Index(fields=['tenant_id','start_at']), Index(fields=['account','start_at'])]
```

### 4.3 `Meeting` — one additive migration
Add a nullable back-reference so a CRM meeting can be pushed to a provider and re-matched on sync (prevents echo/duplication):
```python
external_event_id      = models.CharField(max_length=512, null=True, blank=True)
external_account       = models.ForeignKey('calendar.CalendarAccount', null=True, blank=True,
                                            on_delete=models.SET_NULL)
sync_status            = models.CharField(max_length=20, null=True)  # SYNCED/PENDING/FAILED
```
*(No change to existing Meeting columns → safe additive migration; note the current unique constraint `unique_meeting_per_tenant` on `(tenant_id, title, start_at)` in `meetings/models.py:40` — keep it in mind when projecting many external events back as Meetings, which is why external events live in their own table.)*

---

## 5. Backend work items

### 5.1 Make Google scopes per-integration (the ONE real OAuth change)
`GoogleOAuthHandler.SCOPES` in `integrations/utils/oauth.py:43` is **hardcoded to Sheets/Drive**. The `Integration` model already stores `oauth_config['scopes']` (see README example), but the handler ignores it. Fix: let the handler accept scopes so Calendar can request calendar scopes without breaking Sheets.

```python
class GoogleOAuthHandler:
    def __init__(self, scopes: list[str] | None = None):
        self.scopes = scopes or self.DEFAULT_SHEETS_SCOPES
    # get_authorization_url / exchange_code_for_tokens use self.scopes
```
Calendar scopes:
```
https://www.googleapis.com/auth/calendar          # read/write
# or read-only if a tenant only wants to *view*:
https://www.googleapis.com/auth/calendar.readonly
https://www.googleapis.com/auth/calendar.events
openid, userinfo.email, userinfo.profile          # already used
```
`initiate_oauth` (`views.py:169`) then builds the handler from the selected `Integration.oauth_config['scopes']`. **This keeps the existing Sheets flow untouched** and is backward-compatible.

> Two more concrete edits in the same view: `initiate_oauth` currently hard-guards `integration.type == GOOGLE_SHEETS` (`views.py:145`) — widen it to also accept `GOOGLE_CALENDAR`. And existing Sheets `Connection` rows won't carry calendar scope, so **Calendar must be its own `Integration`/`Connection`** (re-consent), not a scope bolted onto the Sheets connection — the model already supports many connections per user, so this is free.

> ⚠️ Google requires a **security review / verification** for sensitive calendar scopes on published apps. Budget for it early. For internal-only (single Workspace) you can use an internal OAuth consent screen and skip verification.

### 5.2 `calendar/services/google_calendar.py`
- `list_calendars(account)` → populate `CalendarAccount.calendars`.
- `pull(account)` → `events.list(calendarId, syncToken=…, singleEvents=True)`; on `410 Gone` reset token + full resync; upsert `ExternalCalendarEvent`; persist new `syncToken`.
- `push(meeting)` / `patch` / `delete` → project a `Meeting` to a Google event; store `external_event_id`.
- `watch(account)` → `events.watch()` to register a push channel → store `watch_*`; renew before expiry (channels last ~7 days).

### 5.3 `calendar/services/caldav_client.py` (open-standard track)
- Libs: `caldav>=1.3`, `icalendar>=5.0` (add to `requirements.txt`).
- Auth: Basic auth with **app-specific password** (iCloud/Fastmail/Zoho) stored via `encrypt_token`, or discovered principal URL. Store base URL in `caldav_url`.
- `pull(account)` → prefer `sync-collection` REPORT (RFC 6578) using stored sync-token; fall back to ETag diff. Parse VEVENTs with `icalendar` → upsert.
- `push(meeting)` → build a VEVENT, `PUT` to the collection; capture ETag.
- No webhooks → Celery Beat poll (default every 5–10 min, configurable per tenant).

### 5.4 `calendar/services/sync_engine.py`
Thin dispatcher: `for account in due_accounts: provider_service(account).pull()`. Echo-suppression: when an event's `ical_uid`/`external_event_id` matches a `Meeting.external_event_id`, update the link instead of creating a duplicate. Conflict policy: **last-writer-wins by `updated`/`ETag`**, with a per-tenant setting to make CRM authoritative.

### 5.5 Celery tasks (`calendar/tasks.py`) + Beat entries
```python
sync_calendar_account(account_id)          # single account, on-demand + fan-out
poll_due_calendar_accounts()               # Beat: every 5 min → enqueue CalDAV/ICS + Google-without-webhook
refresh_calendar_watch_channels()          # Beat: hourly → renew Google watch before expiry
refresh_expiring_calendar_tokens()         # Beat: hourly → clone of integrations.refresh_expiring_tokens
```
Add to `CELERY_BEAT_SCHEDULE` (`settings.py:477`) next to the existing integration jobs.

### 5.6 API surface (`calendar/urls.py`, DRF viewsets — mirror integrations)
```
POST   /api/calendar/accounts/connect/google/     → returns authorization_url (reuses OAuth state)
POST   /api/calendar/accounts/connect/caldav/     → {url, username, app_password} → validate + store
GET    /api/calendar/accounts/                     → my connected calendars (+ team if permitted)
PATCH  /api/calendar/accounts/:id/                 → toggle calendars[].selected, sync_direction, is_shared_with_team
POST   /api/calendar/accounts/:id/sync/            → force sync now
DELETE /api/calendar/accounts/:id/                 → disconnect (revoke + purge mirror)
GET    /api/calendar/unified/?start=&end=&scope=own|team|all&user_ids=
        → merged feed: Meetings + ExternalCalendarEvents, RBAC-scoped
POST   /api/calendar/webhook/google/:channel/      → Google push receiver (public path, verify channel+token)
```
Reuse `TenantViewSetMixin` + `HasCRMPermission` with a new `crm.calendar` resource (or reuse `crm.meetings`). The **unified endpoint** is the heart of the UX — it does the merge + scope filtering server-side so the frontend just renders.

---

## 6. Two-way sync design (the tricky part)

| Direction | Google | CalDAV |
|-----------|--------|--------|
| **Provider → CRM (pull)** | `syncToken` incremental + `events.watch` webhook (near-real-time); Beat safety-net poll | `sync-collection` token / ETag diff via Beat poll |
| **CRM → Provider (push)** | On `Meeting` save/delete signal → enqueue push; store `external_event_id` | Same, via `PUT`/`DELETE` + ETag |
| **Echo suppression** | Match on `external_event_id` / `ical_uid`; skip re-import of self-originated events | Same via `ical_uid` |
| **Conflicts** | Last-writer-wins by `updated`; per-tenant "CRM authoritative" override | By ETag; refetch-on-412 |
| **Deletes** | Tombstone (`status=cancelled`) → mark `deleted=True`, hide from feed | `404`/removed href → mark deleted |
| **Recurrence** | Use `singleEvents=True` to expand; store master `RRULE` in `recurrence` | Expand VEVENT RRULE with `icalendar`/`recurring-ical-events` |

Start **read-only (pull-only, `sync_direction=IN`)** in Phase 1 to de-risk. Turn on push in Phase 3.

---

## 7. Tenant-wise + team visibility (your core requirement)

Two independent axes, both already supported by your stack:

**Axis 1 — Tenant isolation (automatic).** Every `CalendarAccount` / `ExternalCalendarEvent` row carries `tenant_id`. `TenantViewSetMixin` filters all queries. Tenant A can never see Tenant B's calendars. Nothing extra to build.

**Axis 2 — Who within a tenant sees whose calendar (RBAC scopes).** Reuse the exact pattern Meetings already uses (`get_queryset_for_permission` in `meetings/views.py:188` with `own`/`team`/`all`):
- `crm.calendar.view = own` → user sees only their own connected calendars + their meetings.
- `= team` → sees calendars of their team (needs a team/manager mapping from the JWT/SuperAdmin; you already resolve team scope for meetings).
- `= all` → tenant-wide (admins).
- **Plus a user opt-in gate:** `CalendarAccount.is_shared_with_team`. Even with `team`/`all` scope, a colleague's events are only exposed if that colleague flipped `is_shared_with_team=True` (privacy-first). Unshared calendars still sync for the owner's own view.
- **Optional tenant policy:** a tenant-level setting `calendar_team_sharing = off | opt_in | mandatory` to let an org force-share (e.g., "everyone's busy/free is visible").

**Free/busy vs full detail:** offer a per-account "share as **free/busy only**" mode — team sees blocked time without titles/attendees. Cheap to implement (the unified endpoint redacts fields when the viewer ≠ owner and detail-sharing is off). This is usually what teams actually want.

---

## 8. Frontend (unified UI)
- One calendar grid consuming `GET /api/calendar/unified/`. Each event carries `source` (`meeting` | `google` | `caldav`) and `owner_user_id` for color-coding per person.
- "Connect calendar" screen lists providers → Google (OAuth redirect) / CalDAV (URL + app-password form) / ICS (paste URL).
- Per-calendar toggles (show/hide, color), a "share with team" switch, and a scope selector (My / Team / Everyone) that maps to `?scope=`.
- Reuse the existing `integrations` connection UI patterns — this is a sibling of the Sheets connect flow.

---

## 9. Composio — where it fits, where it doesn't

**Is it required for this? No.** You already have native Google OAuth, a Fernet token vault, Celery polling, and a multi-tenant connection model. For **Calendar specifically**, native Google Calendar API + CalDAV is strictly better on the dimensions that matter here:

| Dimension | Native (recommended now) | Composio |
|-----------|--------------------------|----------|
| Latency / real-time | Google **watch webhooks** = near-instant | Polling/action calls; extra hop |
| Open standards / CalDAV | Full control (iCloud, Fastmail, Nextcloud…) | Google Calendar yes; **CalDAV coverage weak/none** |
| Cost | API calls only | Per-action / seat pricing |
| Two-way + recurrence + free/busy | Full fidelity | Abstracted, less control |
| Effort | ~90% plumbing already exists | New dependency + auth model |
| Data residency / tenant tokens | Tokens in *your* DB, encrypted | Tokens brokered by third party |

**Where Composio genuinely earns its place (adopt later, in the integrations module — as you planned):**
- **Breadth:** the long tail of connectors (HubSpot, Salesforce, Slack, Notion, Jira, hundreds more) without you maintaining an OAuth app + client per provider.
- **Managed multi-tenant auth:** Composio's "connected accounts" are per-end-user — maps cleanly onto your `tenant_id`+`user_id`. Good when you'd otherwise be registering dozens of OAuth clients.
- **AI-agent tool-calling:** if your `ai` app / copilot needs to *take actions* across many SaaS tools, Composio exposes them as ready-made agent tools. This is its strongest fit for you.

**Recommendation:**
1. **Now:** build Calendar natively (Google API + CalDAV) on the existing integrations infra.
2. **Later:** introduce Composio as a *new connection type* in the integrations module for the long-tail apps and for agent actions — **not** to replace the calendar sync you build now.
3. If you truly want a single control plane, you can still register Composio-managed connections alongside native ones (`IntegrationTypeEnum.COMPOSIO`), and route calendar through native while everything else goes through Composio. Best of both.

*(If, contrary to this, you decide to route Google Calendar through Composio too — it's possible via Composio's Google Calendar toolkit — but you'd lose the CalDAV/open-standard track and take on cost + latency. Not recommended for the calendar use case.)*

---

## 10. Phased roadmap

| Phase | Scope | Outcome |
|-------|-------|---------|
| **0 — Foundations** | Add `calendar` app; `CalendarAccount` + `ExternalCalendarEvent` models + migrations; add `caldav`/`icalendar` to requirements; register `GOOGLE_CALENDAR` + `CALDAV` integrations | Schema + registry ready |
| **1 — Google read-only** | Per-integration scopes fix (§5.1); Google connect via existing OAuth; `list_calendars`; `pull` with `syncToken`; Beat poll; **unified endpoint (Meetings + Google, own-scope)** | You see your Google events next to CRM meetings |
| **2 — Real-time + CalDAV** | Google `events.watch` webhook + renewal; CalDAV connect + pull; ICS feeds | Near-real-time Google; open-standard providers working |
| **3 — Two-way + team** | Push Meetings → provider; echo-suppression/conflict rules; RBAC `team`/`all` scope on unified endpoint; `is_shared_with_team` + free/busy redaction | Team-wide calendar with privacy controls; CRM meetings appear in personal calendars |
| **4 — Optional** | Microsoft Graph (Outlook); Composio for long-tail integrations + agent actions | Breadth |

**First PR I'd open:** Phase 0 + the §5.1 scopes refactor + Google read-only pull behind a feature flag, with the unified endpoint returning `own` scope only. Smallest slice that's demoable end-to-end.

---

## 11. Security & gotchas checklist
- [ ] Google **sensitive-scope verification** for calendar on published OAuth apps (start early; internal Workspace can skip via internal consent screen).
- [ ] Verify Google webhook authenticity: check `X-Goog-Channel-ID` / `X-Goog-Channel-Token` against stored `watch_*`; webhook path must be in the JWT-middleware **PUBLIC_PATHS** (like the existing OAuth callback and inbound webhook).
- [ ] CalDAV app-passwords and Google refresh tokens → **always** `encrypt_token` at rest (never log plaintext; mirror the discipline in `Connection.generate_inbound_key`).
- [ ] Token refresh failures → mark `CalendarAccount.status` + surface a "reconnect" prompt (reuse `Connection.mark_as_error`).
- [ ] Idempotent upserts keyed on `(account, external_calendar_id, external_id)`; recurrence expansion bounded to the queried window to avoid unbounded fan-out.
- [ ] Rate limits: Google per-user quotas → backoff; batch `events.list` with `pageToken`.
- [ ] Purge mirror + revoke token on disconnect (GDPR / tenant offboarding).
- [ ] Timezones: store UTC + original `timezone`; respect `all_day` (date-only) events.
- [ ] **Cache backend caveat:** OAuth `state` is stored in Django cache, which is currently **FileBasedCache** at `.cache/` (`settings.py:455-460`), *not* Redis. Single-host is fine, but if you run multiple web hosts the state won't be shared → move `CACHES` to Redis before horizontal scaling (Redis is already a dependency for Celery).
- [ ] **Config gap:** `.env.example` does **not** document `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_REDIRECT_URI` / `INTEGRATION_ENCRYPTION_KEY` (settings read them at `settings.py:422-424,432`). Add them (plus the new calendar redirect URI) to `.env.example` as part of Phase 0.

---

## 12. Open questions for you (Ritik)
1. **Providers priority:** Google first (assumed). Which CalDAV provider matters most next — iCloud, Fastmail, Zoho, Nextcloud, Outlook-via-CalDAV?
2. **Direction:** OK to ship **pull-only** first and add "CRM meetings push into personal calendars" in Phase 3? Or is push a day-1 requirement?
3. **Team sharing default:** privacy-first **opt-in** (recommended) or org-mandated **free/busy visible to all** by default?
4. **Detail level for teammates:** full event details, or **free/busy only** when viewing others?
5. **Google app type:** internal single-Workspace (skip verification) or public multi-tenant (needs Google security review)?
6. **RBAC resource:** new `crm.calendar.*` permission keys, or reuse `crm.meetings.*` scopes?

---

*Prepared by FULL Stack Agent — grounded in the existing `integrations`, `meetings`, and `common` apps. File: `docs/CALENDAR_SYNC_PLAN.md`.*
