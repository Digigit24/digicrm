# TeleCMI Telephony Integration — Full Audit

*digicrm (Django) + crmflutter (Flutter) + celiyocrmmobileapp (React Native), as of 2026-08-30*

This audit covers the full TeleCMI telephony stack: the `digicrm/telephony/` Django app, crmflutter's `lib/features/telephony/` mobile implementation, and the RN app as a parity baseline. It was produced by reading code directly (models, views, services, tests, docs) rather than from prior assumptions — file:line citations are given throughout.

---

## 1. Backend (digicrm) — what's done

The `telephony` Django app (`digicrm/telephony/`) is a full telephony subsystem, not a thin CDR mirror.

**Models** (`telephony/models.py`, 653 lines):
- `TeleCMICredential` — one per tenant (`tenant_id` unique, line 61). Holds `app_id`, encrypted `secret`, SBC region, an optional shared "default" extension + password, `webhook_secret`.
- `TeleCMIAgent` — per-user extension credentials.
- `TeleCMICallingProfile` + `TeleCMIProfileAssignment` — named "lines" (e.g. Sales, Support) a tenant can define and assign users to, with one flagged `is_default`. Built specifically because the original one-extension-per-tenant model left most users hitting `424 no_agent`. Design doc: `_plans/07-telecmi-multi-callerid.md` (referenced live from `softphone_service.py:216`, not listed in `_plans/README.md`'s "current active" table).
- `CallLog` — rich CDR: direction, call_type, duration, billed_sec, rate, recording metadata (incl. Zata archive status), lead/agent linkage, outbound leg-A/leg-B dedup fields, voicemail fields, and `call_outcome`/`call_outcome_note`/`call_outcome_set_at` (agent disposition).
- `SMSLog`, `ZataStorageCredential` (per-tenant private S3-compatible recording archive), `TeleCMICampaign`, `DeviceToken` (FCM/VoIP push to wake a backgrounded mobile softphone).

**Encryption**: two-layer envelope scheme (`telephony/services/crypto.py`) — a per-tenant Fernet DEK wrapped by one deployment-wide master key. `TeleCMICredential`/`TeleCMICallingProfile` secrets use this. **Gap**: `TeleCMIAgent.password_encrypted` (per-user rows) still uses the older single shared-key scheme, not the envelope scheme — inconsistent, not a leak.

**Endpoints** (`telephony/urls.py`, `telephony/views.py`, 1349 lines) — full surface: credentials/agents/calling-profiles CRUD + verify/assign, click-to-call, hangup, add-note, CDR list/detail/sync, recording proxy-stream + presigned-access, call outcome, analytics (+ daily), full campaigns CRUD + push-leads/push-group/toggle-active, SMS send/list, caller-ID, device-token register/remove, break records, callbacks list, WebRTC config (SIP credential issuance). All authenticated ones behind `JWTRequestAuthentication` + `HasDigiPermission`. Two **public** webhook endpoints (`/webhook/cdr/`, `/webhook/live/`) bypass JWT middleware entirely (listed in `PUBLIC_PATHS`).

**Webhook/CDR handling**: real, working receivers. CDR ingestion upserts `CallLog` idempotently (dedup on `cmiuid`, leg-A/leg-B linking), creates a `LeadActivity`, auto-creates a callback `Task` on missed calls, republishes over Pusher for near-real-time frontend updates. Live events (ringing/answered/ended) trigger a mobile wake-push via `resolve_user_ids_for_extension`. A Celery Beat task (`sync_all_telecmi_cdrs`, every 5 min) is a safety net; `reconcile_outbound_call` fires 60s after every click-to-call.

**Multi-tenancy**: every credential/agent/profile/call-log lookup traced is `tenant_id`-scoped with no global fallback. `TenantViewSetMixin.get_queryset()` hard-scopes every ViewSet and returns `queryset.none()` if `tenant_id` is missing (no silent unfiltered fallback). `request.tenant_id` comes from the verified JWT payload, not a client header. Dedicated tenant-isolation tests exist (`test_tenant_a_only_sees_own_logs`, `test_cross_tenant_cmiuid_not_shared`, `test_webhook_tenant_isolation`, `test_sms_tenant_isolation`, `test_list_is_tenant_scoped`). **No cross-tenant leak pattern found** (unlike the earlier WhatsApp audit).

**Analytics** (`telephony/services/analytics_service.py`, 191 lines): real, implemented. `get_agent_summary`, `get_team_summary`, `get_outcome_breakdown`, `get_agent_daily_stats` are genuine ORM aggregations. `get_missed_unattended(tenant_id, hours_threshold=2)` implements the "missed calls needing follow-up" logic: inbound missed calls in the last 24h with no later outbound call to the same number, flagged urgent past 2h. Uses a per-row `.exists()` check (N+1 pattern, not batched — a performance note for scale, not a correctness bug).

**Campaigns** (`telephony/services/campaign_service.py`, 186 lines): a real feature. Actually calls TeleCMI's own `/v3/campaign/*` API to create/update/delete campaigns and `/v3/leads/add-custom` to push CRM lead lists into TeleCMI's dialer (chunked at 1000/request), using the CRM lead ID for CDR correlation. digicrm is the system of record for campaign definition; actual dialing execution happens on TeleCMI's platform.

---

## 2. Backend — gaps / risks found

1. **Webhook auth is optional per-tenant, not enforced (highest priority).** `_verify_webhook_secret()` (`views.py:1315-1333`) returns `True` when a tenant's `webhook_secret` is unset — the default/optional state, not required at credential creation. `_verify_webhook_app_id()` (`views.py:1336-1349`) similarly passes when the payload simply omits the `appid`/`app_id` field. Combined: any tenant that hasn't explicitly set a webhook secret has effectively open CDR/live-event webhooks, gated only by guessing a tenant UUID. An attacker could inject fake call records, fabricate a missed call (auto-creating a callback Task on a real lead), or spoof a live-ringing event that triggers a mobile wake push. `docs/telephony-api.md:562-570` documents the secret as if it always gates access — it doesn't disclose the open-by-default state.
2. **Doc/code contradiction on WebRTC config.** `docs/telephony-api.md` explicitly states *"The password is never returned by this endpoint"* (line ~541) for `/webrtc-config/` — the actual code deliberately returns the plaintext SIP password by design (`views.py:1070-1096`, with an in-code justification: the PIOPIY SDK needs it for SIP digest auth). The behavior is a considered, contained design choice; the documentation actively misstating it is the problem — a security reviewer reading only the doc reaches the wrong conclusion.
3. **`_normalize_live_event()` is an explicit best-effort guess** (`views.py:1274-1297`) since TeleCMI's live-event payload isn't fully documented publicly. Unmatched payloads are logged and silently dropped — a payload shape TeleCMI uses that wasn't guessed correctly means real-time ringing/answered UI updates silently stop working with no alarm. The plan doc's proposed `TeleCMIWebhookLog` capture table was never built.
4. **Stale planning doc.** `digicrm/docs/TELEPHONY_ANALYTICS_PLAN.md` (2026-07-29) lists `agent_user_id` never populated, no `call_outcome` field/endpoint, and no analytics endpoint as open critical gaps — all three are now implemented (`call_log_service.py:90-103`, `views.py:618-642`, `views.py:652-697`). Reads as a live backlog to anyone who finds it; should be superseded/banner-marked like the WhatsApp docs were.
5. **No test coverage for analytics or campaigns.** A direct search across `telephony/tests/` for analytics/campaign-related test code returned zero matches. `analytics_service.py` and `campaign_service.py` are both completely untested, despite call-log processing and views having heavy coverage (3,346 lines of tests total across the app).
6. **`docs/telephony-api.md` is materially out of date** beyond the two issues above — its own footer describes an earlier code state. §16's "new database tables" lists 4; actual schema has 11. Calling profiles, campaigns, analytics, Zata storage, device tokens, recording endpoints, and call outcome are all implemented in code but entirely undocumented.

---

## 3. crmflutter — what's done

On `master` (merged via `38e3e72`, originally built on `feature/telephony-core` as `719939b`):

- **Models**: `call_log.dart` (`CallLog` + 6-value `CallOutcome` enum), `telephony_analytics.dart` — match the backend's JSON contract field-for-field.
- **Service layer** (`data/telephony_service.dart`): real Dio calls — `clickToCall`, `getWebRTCConfig`, `getRecentCalls`, `getCalls`, `setCallOutcome`, `getAnalytics`. Deliberately does date-range filtering client-side since the backend's `CallLogViewSet.filterset_fields` silently ignores unsupported query params rather than erroring.
- **Providers** (`application/telephony_providers.dart`): `callsProvider`/`leadCallsProvider`/`telephonyAnalyticsProvider` families, `TelephonyMutations` (setOutcome, clickToCall) with a documented cache-invalidation graph.
- **Screens**: real Call Log screen (filters, CallRow, CallDetailSheet) and Analytics dashboard, both routed and in the drawer under a permission-gated Telephony section.
- **Lead Detail integration**: Quick Action Call button and a real Lead Calls tab, both wired to live data — no TODOs/mocks found.
- **Click-to-call**: shared `placeCall()` helper used by all three call sites (Call Log, Analytics, Lead Detail) — genuinely calls the production `POST /telephony/calls/click-to-call/` endpoint, falls back to the OS `tel:` dialer on failure or missing permission. Reviewed and wired correctly; has not been fired against a *working* SIP registration (see §5), so "does the callee's phone actually ring" is unverified end-to-end.
- **Hidden dev-only SIP test screen** (`TelephonySipTestScreen`, route `/dev/telephony-sip-test`) — confirmed genuinely hidden (not in drawer/nav, direct-route-only). Fully functional: real `SipService.register()` via the `sip_ua` package, a click-to-call card, a CDR list. Kept deliberately as manual-test tooling for re-verifying once TeleCMI's SBC issue clears.

---

## 4. crmflutter — what's stubbed/incomplete

- **Telephony Campaigns (Lane B)** — as of this audit, code-complete in a worktree (`crmflutter-wt-telephony-campaigns`, branch `feature/telephony-campaigns`) but **uncommitted**: real service methods, provider, a fully-built read-only list screen (loading/error/empty/loaded states, progress bar with a divide-by-zero guard), and a 500-line test file — but sitting only in the working tree, not committed, and behind current master (needs a rebase). On master today, `TelephonyCampaignsScreen` is still the literal placeholder stub. (Status: actively being finished and merged as of this writing — see team task board.)
- **Click-to-call not fired live end-to-end** — code path is real and reviewed but the only available test extension can't complete SIP registration (see §5).
- Outbound campaign creation/launch from mobile is **out of scope by design** (both the execution plan and the RN app's equivalent treat Campaigns as read-only; creation happens via web admin) — not a gap.
- **Call recording playback** — not found in crmflutter's telephony screens. Backend endpoints (`/calls/<pk>/recording/`, `/recording-access/`) exist and work (used by the web frontend). Owner has approved building this on mobile as a follow-up.

---

## 5. The known calling blocker

Extension `5002_33338188` receives an immediate `403 Forbidden` from TeleCMI's Kamailio SBC (`sbcind.telecmi.com`, Kamailio 5.7.5 per the `Server` response header) on the very first SIP REGISTER — no `WWW-Authenticate` challenge is issued, meaning the password is never even checked. Captured request/response:

```
REGISTER sip:sbcind.telecmi.com SIP/2.0
Via: SIP/2.0/WSS tix141d606uh.invalid;branch=z9hG4bK97258767
To:   <sip:5002_33338188@sbcind.telecmi.com>
From: "5002_33338188" <sip:5002_33338188@sbcind.telecmi.com>;tag=m229933112
CSeq: 1 REGISTER
Contact: <sip:a8118632@tix141d606uh.invalid;transport=wss>;+sip.ice;reg-id=1;...;expires=600
(no Authorization header — correct for a first attempt)

SIP/2.0 403 Forbidden
Server: kamailio (5.7.5 (x86_64/linux))
Via: ...;received=106.213.83.230
(no WWW-Authenticate — no challenge issued)
```

This is corroborated by the same extension registering successfully from a separate, already-working web-based softphone (in `sepratecrm`). Nothing in either `digicrm` or `crmflutter` is implicated — backend credential issuance for this tenant works correctly (the WebRTC config endpoint resolves the identity and returns valid SBC/password data), and the Flutter SIP client sends a standard REGISTER. In Kamailio, a 403 before any challenge is almost always an explicit config reject — source-IP permissions, a domain check, or an unprovisioned AoR — not a credential mismatch.

**Data points for TeleCMI support:**
- Our public IP as seen by their SBC: `106.213.83.230` (if they IP-allowlist, this is the address to permit)
- Contact header user (`a8118632`) does not match the AoR (`5002_33338188`) — normal JsSIP/WebRTC behavior, but some Kamailio configs reject a Contact whose user differs from the AoR
- Via host (`tix141d606uh.invalid`) is JsSIP's synthetic WSS domain — also normal, also something a strict config can reject
- The working browser softphone's Contact/Via values (from the same extension) are available for TeleCMI to compare against if requested

This is blocked entirely on TeleCMI's support team; it cannot be diagnosed or fixed further from either repo.

---

## 6. What's next — prioritized punch list

**Blocked on TeleCMI support:**
1. Get TeleCMI to explain/fix the 403-with-no-challenge for `5002_33338188`. Everything downstream (in-app SIP calling, click-to-call's "ring the agent's device" leg) is stuck until this clears.

**Actioned as of 2026-08-30** (see team task board for live status):
2. Commit, rebase, and merge the Lane B Campaigns branch onto current master.
3. Enforce `webhook_secret` on every tenant's `TeleCMICredential` (mandatory going forward); fail-closed the `app_id` check.
4. Add webhook payload capture/logging to confirm real live-event field names and tighten `_normalize_live_event()`.
5. Archive/supersede `TELEPHONY_ANALYTICS_PLAN.md`; fix the `telephony-api.md` doc/code contradictions and undocumented features.
6. Build call recording playback on mobile against the existing, working backend endpoints.

**Still pending, once the SBC issue clears:**
7. An actual live device-to-device test call through both the SIP test screen and production click-to-call, to close out the "reviewed but never fired live" caveat.

**Product decisions still open:**
8. Whether real in-app SIP/WebRTC calling (currently the hidden dev screen) becomes a first-class, always-available production feature once TeleCMI's issue clears, or click-to-call (ring desk phone, no in-app audio) remains the intended production UX with the SIP screen staying dev-only. The RN app has no SIP equivalent at all — this is a Flutter-only capability decision, not a parity gap.
9. Whether the backend's richer feature set beyond what either mobile app currently surfaces (multiple calling profiles/lines per tenant, agent presence status — not yet built, Zata recording archival) should be exposed in the CRM apps or stays admin/web-only.

---

**Key files referenced**: `digicrm/telephony/{models,views,urls,serializers,tasks}.py`, `digicrm/telephony/services/{crypto,token_service,softphone_service,analytics_service,campaign_service,call_log_service}.py`, `digicrm/docs/{telephony-api.md,TELEPHONY_ANALYTICS_PLAN.md}`, `digicrm/_plans/README.md`, `_plans/07-telecmi-multi-callerid.md`; `crmflutter/lib/features/telephony/**`, `crmflutter/lib/router/{app_router,app_routes,app_drawer}.dart`, `crmflutter/lib/features/leads/presentation/{screens/lead_detail_screen.dart,tabs/lead_calls_tab.dart}`, `crmflutter/docs/{telephony-module.md,telephony-execution-plan.md}`; `crmflutter-wt-telephony-campaigns/lib/features/telephony/**`; `celiyocrmmobileapp/app/(app)/telephony/**`.
