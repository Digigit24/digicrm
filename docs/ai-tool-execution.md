# Two tool-execution systems, and why they must stay separate

There are **two** systems in this repo that "run CRM tools for an AI". They
share one thing — the tool *schema catalog* — and differ in every other
respect that matters. Conflating them is the failure mode this document exists
to prevent, because the two have **different auth models**: one runs as a
single env-bound service account, the other runs as the individual signed-in
user with their own permissions. Merging them would either hand copilot users
unscoped access to a whole tenant, or break every external MCP client.

If you are adding a tool or exposing a new API, read [When you add a new
tool](#when-you-add-a-new-tool) — it says exactly which files to touch.

---

## 1. The MCP server — for external clients

**Entry point:** `POST /mcp/sse` (plus `/mcp/message`, `/mcp/health`, and the
OAuth shims) — `mcp/django_view.py`, routed via `mcp_urlpatterns`.

**Who calls it:** MCP clients outside this codebase — Claude Desktop, Codex,
any MCP-speaking tool the owner points at the server.

**Auth model: a shared secret plus ONE hard-bound tenant.**

- Authenticated with `Authorization: Bearer <MCP_SECRET>`, compared in constant
  time (`mcp/django_view.py`). It **fails closed**: if `MCP_SECRET` is unset the
  server refuses every MCP request rather than running open.
- The tenant is **not** derived from the caller. It comes from the
  `DIGICRM_TENANT_ID` environment variable, and `_dispatch_tool()` raises
  outright if it is missing.
- **There is no per-user identity and no permission check.** Anyone holding
  `MCP_SECRET` acts with full access to that one configured tenant.

**Execution:** `mcp/django_view.py::_dispatch_tool` talks to the **ORM
directly** — `Lead.objects.filter(tenant_id=TENANT_ID)` and similar. It does
not go through DRF views, serializers, `HasDigiPermission`, or
`TenantViewSetMixin`. Tenant safety rests entirely on every query in that
function remembering to filter by `TENANT_ID`.

**Scope:** the full catalog — all 81 tools in `mcp/server.py::TOOLS`.

---

## 2. The in-app AI Copilot — for signed-in users

**Entry point:** `POST /api/ai/chat/` — `ai/views.py::AIChatView`, streaming
SSE. Used by **both** frontends: crmflutter's `/copilot` screen and
sepratecrm's assistant UI.

**Who calls it:** a real, signed-in CRM user, in their browser or phone.

**Auth model: the caller's own JWT, with their own permissions.**

- Normal tenant JWT, same as every other `/api/` call. `AIChatView` requires
  authentication like any other view.
- Tenant and user identity come **only** from that JWT. The model cannot supply
  them: `_FORBIDDEN_ARG_KEYS` in `ai/tools.py` strips `tenant_id`,
  `owner_user_id`, `created_by` and `by_user_id` out of tool args before
  anything runs.
- Full RBAC applies, including `own` / `team` / `all` data scoping.

**Execution:** `ai/tools.py::execute_tool` → `RequestScopedClient.call()`,
which **dispatches digicrm's own DRF views in-process**:

1. `django.urls.resolve(path)` finds the same view the REST API would use.
2. A synthetic request is built with `RequestFactory` and given the caller's
   already-verified identity, copied from the outer request
   (`_AUTH_CONTEXT_ATTRS` — the exact attribute set
   `JWTAuthenticationMiddleware` sets and `JWTRequestAuthentication` reads).
3. The view is called directly, in the same thread.
4. The DRF `Response.data` is read straight off the object.

Because the **real view runs**, tool calls still get `JWTRequestAuthentication`,
`HasDigiPermission`, `TenantViewSetMixin` and the real serializers. **No
permission logic is duplicated in `ai/tools.py`, and none should ever be added
there** — that property is the entire reason this design is safe.

**Scope:** a deliberately curated subset — `EXPOSED_TOOLS` in `ai/tools.py`
(currently 14: 5 read, 9 write). Being in the MCP catalog is **not** enough to
be reachable from the copilot.

**Writes require confirmation.** Tools in `WRITE_TOOLS` are proposed to the
user, the stream pauses, and nothing executes until the client re-POSTs with an
explicit approval (`pending_tool_calls` + `confirmations`). See
`ai/providers.py::stream_agent`.

### Why in-process instead of an HTTP self-call

This path used to call digicrm's REST API over HTTP at
`AI_INTERNAL_BASE_URL` (`http://127.0.0.1:8001`), forwarding the caller's
bearer token. Same security properties, but a serious operational flaw:

`execute_tool()` runs **inside the SSE generator, while the streaming response
is still open and holding a worker**. Making a blocking HTTP call back into the
same server from there means one worker waits on another worker to answer it.
Under gunicorn with N sync workers, N concurrent copilot turns occupy every
worker and each is blocked on a request that needs a free worker — a
**self-call deadlock**. It does not fail loudly. It hangs, only under
concurrency, so it looks fine in testing and falls over in production.

The old code reasoned "Django's runserver is threaded, so a localhost self-call
while streaming works fine." That is true of `runserver` and false of a small
sync-worker deployment — an assumption that quietly stopped holding once this
was deployed for real.

The in-process dispatcher removes the hop entirely: a nested function call
cannot wait on a worker. It also removed a config knob (`AI_INTERNAL_BASE_URL`)
that had to be correct in every environment and, on the live host, was pointing
at a port nothing listened on — so every tool call was failing with
`Connection refused` before this change.

---

## Side by side

| | MCP server | AI Copilot |
|---|---|---|
| Route | `POST /mcp/sse` | `POST /api/ai/chat/` |
| Caller | External MCP client | Signed-in CRM user |
| Auth | `MCP_SECRET` shared secret | The user's tenant JWT |
| Tenant | `DIGICRM_TENANT_ID` env var, fixed | From the JWT, per request |
| Per-user permissions | **None** | Full RBAC, incl. own/team/all |
| Execution | ORM directly | DRF views, in-process |
| Tenant safety rests on | Every query filtering by `TENANT_ID` | `TenantViewSetMixin` on the real view |
| Tools | All 81 | Curated 14 (`EXPOSED_TOOLS`) |
| Writes | Immediate | Confirm-before-execute |
| Code | `mcp/django_view.py::_dispatch_tool` | `ai/tools.py::execute_tool` |

**The one shared thing:** `mcp/server.py::TOOLS` is the single source of truth
for tool **names, descriptions and JSON schemas**. `ai/tools.py::get_tool_schemas()`
imports it and filters to `EXPOSED_TOOLS`, so a schema is never written twice
and the two systems cannot describe the same tool differently.

---

## When you add a new tool

**Always:**

1. Add it to `mcp/server.py::TOOLS` via `_tool(...)` — name, description, JSON
   schema. This is the single source of truth for the schema.
2. Implement it in `mcp/django_view.py::_dispatch_tool` (ORM). This makes it
   available to external MCP clients.

**Additionally, only if the in-app Copilot should be able to call it:**

3. Add the name to `READ_TOOLS` or `WRITE_TOOLS` in `ai/tools.py`.
   `WRITE_TOOLS` automatically means confirm-before-execute
   (`CONFIRMATION_REQUIRED` is derived from it).
4. Add a plan builder branch in `ai/tools.py::_plan()` returning
   `(method, path, params, body)` for the **REST** endpoint. Note the arg names
   in the MCP schema are often not the serializer's field names — the mapping
   (`lead_id`→`lead`, `start_time`→`start_at`, …) lives here and must be
   checked against the actual serializer, not assumed.
5. **There must be a REST endpoint.** The copilot path dispatches DRF views; it
   cannot call the ORM. If the capability only exists as ORM code in
   `_dispatch_tool`, build the REST endpoint first — do not add a second ORM
   path to `ai/tools.py`.
6. Add a test in `ai/tests/test_tool_dispatch.py`, including a cross-tenant
   negative case for anything that reads or writes tenant data.
7. Update the frontends' tool card registries so the call renders as something
   a human recognises rather than a raw blob:
   `crmflutter/lib/features/copilot/application/copilot_tool_registry.dart` and
   sepratecrm's copilot tool registry.

**Do not** implement a tool only in `ai/tools.py`. It would be invisible to
external MCP clients and its schema would live somewhere other than the
catalog.

## Why they must NOT be merged

- **Different auth.** One is a shared secret bound to a single tenant with no
  user concept; the other is a per-user JWT with granular permissions. There is
  no single code path that can be correct for both — you would either strip
  RBAC from the copilot or invent a fake user for MCP clients.
- **Different tenancy.** MCP's tenant is a deploy-time constant; the copilot's
  is per-request. A shared executor would need the tenant threaded through as a
  parameter, which is exactly the kind of thing that gets defaulted wrong once
  and leaks across tenants silently.
- **Different exposure.** MCP exposes all 81 tools to a trusted operator; the
  copilot exposes 14 to every user in every tenant. The narrower list is a
  deliberate safety boundary, not an implementation gap to be closed.
- **Different execution context.** MCP runs in a normal request. The copilot
  runs inside an open SSE stream — which is why its executor must never make a
  blocking call back into this server.

---

## Tests

`ai/tests/test_tool_dispatch.py` covers the security properties, not just the
happy path: cross-tenant reads and writes, a real primary key from another
tenant, `tenant_id`/`owner_user_id` smuggled through tool args, `own` vs `all`
view scope from two different callers, missing view/create permission, module
access, the tool allow-list, and an explicit assertion that the dispatcher
**opens no socket** — so if an HTTP self-call ever comes back, it fails in CI
rather than deadlocking in production months later.
