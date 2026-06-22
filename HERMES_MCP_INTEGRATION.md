# Connecting Hermes to the DigiCRM MCP Server

How a Hermes (Nous Research) agent connects to the live DigiCRM MCP server and calls its tools.

- **Server:** `digicrm` · MCP Streamable HTTP / SSE · JSON-RPC 2.0
- **Production base URL:** `https://crm.celiyo.com`
- **Tools exposed:** 39 (CRM, Users & Assignment, WhatsApp, Sequences & Campaigns)

---

## 1. Endpoint & Auth

| Item | Value |
|------|-------|
| JSON-RPC endpoint | `POST https://crm.celiyo.com/mcp/sse` |
| SSE stream (optional) | `GET https://crm.celiyo.com/mcp/sse` |
| Health check | `GET https://crm.celiyo.com/mcp/health` |
| Auth header | `Authorization: Bearer <MCP_SECRET>` |
| Auth fallback | `?secret=<MCP_SECRET>` query param |
| Content-Type | `application/json` |

Auth is a single shared secret (`MCP_SECRET`), **not** per-user OAuth. Keep it server-side; never expose it to the model or in client code shipped to users.

> The simplest integration is **plain `POST /mcp/sse`** with a JSON-RPC body. You do not need the SSE stream — that exists only for UI connectors (Claude Desktop etc.). Hermes should POST directly.

---

## 2. The 3-call handshake

Every session follows the same JSON-RPC flow. `id` is any integer you choose.

### a) `initialize`
```bash
curl -sX POST https://crm.celiyo.com/mcp/sse \
  -H "Authorization: Bearer $MCP_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize",
       "params":{"protocolVersion":"2025-03-26","capabilities":{},
                 "clientInfo":{"name":"hermes-agent","version":"1.0"}}}'
```
Returns `serverInfo: {name:"digicrm"}`. Accepted `protocolVersion`: `2024-11-05` or `2025-03-26`.

### b) `tools/list` — fetch the tool schemas to feed Hermes
```bash
curl -sX POST https://crm.celiyo.com/mcp/sse \
  -H "Authorization: Bearer $MCP_SECRET" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
```
Returns `result.tools[]`, each with `name`, `description`, `inputSchema` (JSON Schema). Map these straight into Hermes' tool / function-calling list.

### c) `tools/call` — execute whatever Hermes selects
```bash
curl -sX POST https://crm.celiyo.com/mcp/sse \
  -H "Authorization: Bearer $MCP_SECRET" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call",
       "params":{"name":"list_leads","arguments":{"page":1,"search":"acme"}}}'
```
Result comes back as `result.content[0].text` — a JSON string. Parse it and return it to the model as the tool result.

---

## 3. Drop-in client config

If your Hermes runtime uses a standard MCP client config block (remote SSE server):
```json
{
  "mcpServers": {
    "digicrm": {
      "url": "https://crm.celiyo.com/mcp/sse",
      "headers": { "Authorization": "Bearer ${MCP_SECRET}" }
    }
  }
}
```

If Hermes is your own code, the loop is just: `initialize` once → `tools/list` once → on each model turn, POST `tools/call` for the tool Hermes picked, parse `result.content[0].text`, feed it back. Minimal Python:

```python
import os, requests
URL, SECRET = "https://crm.celiyo.com/mcp/sse", os.environ["MCP_SECRET"]
H = {"Authorization": f"Bearer {SECRET}", "Content-Type": "application/json"}
_id = 0

def rpc(method, params=None):
    global _id; _id += 1
    body = {"jsonrpc": "2.0", "id": _id, "method": method}
    if params is not None: body["params"] = params
    r = requests.post(URL, headers=H, json=body, timeout=30).json()
    if "error" in r: raise RuntimeError(r["error"]["message"])
    return r["result"]

rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                   "clientInfo": {"name": "hermes-agent", "version": "1.0"}})
tools = rpc("tools/list")["tools"]          # -> give to Hermes as its toolset

def call_tool(name, arguments):              # -> call when Hermes emits a tool call
    out = rpc("tools/call", {"name": name, "arguments": arguments})
    import json; return json.loads(out["content"][0]["text"])
```

---

## 4. Tool catalog (39)

**CRM core (13)** — `list_leads` (now filterable by `assigned_to` / `unassigned`), `get_lead`, `list_lead_statuses`, `create_lead`, `update_lead`, `update_lead_status`, `bulk_import_leads`, `add_lead_to_group`, `create_lead_activity`, `create_task`, `update_task`, `create_meeting`, `update_meeting`

**Users & assignment (5)** — `list_users` (resolve a name to a user UUID, sourced from admin.celiyo.com), `assign_lead`, `bulk_assign_leads`, `create_lead_group`, `create_lead_status`

**WhatsApp (10)** — `get_whatsapp_templates`, `get_lead_chat`, `get_lead_enrollments`, `send_whatsapp_text`, `send_whatsapp_template`, `agent_send_whatsapp`, `mark_chat_read`, `assign_lead_chat_user`, `block_whatsapp_contact`, `log_agent_activity`

**Sequences & campaigns (11)** — `create_sequence`, `add_sequence_step`, `update_sequence_step`, `delete_sequence_step`, `enroll_lead_in_sequence`, `pause_enrollment`, `resume_enrollment`, `unenroll_lead`, `create_campaign`, `launch_campaign`, `get_campaign_analytics`

Authoritative names + argument schemas always come from `tools/list` at runtime — treat the list above as a reference, not a contract.

---

## 5. Notes & gotchas

- **Tenant is server-side.** The agent never sends a tenant or user ID. `DIGICRM_TENANT_ID` and the service JWT are bound to the `MCP_SECRET` on the server, so every call is automatically scoped to the Digitech tenant.
- **Errors** arrive as JSON-RPC `error` objects (`code`, `message`). A failed tool returns code `-32603`; unknown method returns `-32601`; bad auth returns HTTP `401`.
- **Notifications** (`notifications/initialized`) get HTTP `202` with no body — safe to fire-and-forget or skip entirely.
- **Known-flaky tools** (per current production status): `send_whatsapp_template` / `agent_send_whatsapp` require `template_components` when the template has `{{n}}` variables; `assign_lead_chat_user` and `launch_campaign` have open server-side fixes. Prefer `send_whatsapp_text` and variable-free templates until those land.
- **Rotate the secret** before going wide — the current `MCP_SECRET` has appeared in test logs.
- **Assigning leads is a two-step flow.** `assigned_to` is a user UUID, not a name. Call `list_users` first to resolve the person, then pass that UUID to `assign_lead` / `bulk_assign_leads` (or to `list_leads` to filter). Pass `assigned_to: null` to unassign. Users come from admin.celiyo.com via the `/api/crm/users/` proxy.

---

*Reference: `mcp/django_view.py` (routes/auth), `MCP_STATUS.md` (tool status). Base URL: `https://crm.celiyo.com`.*
