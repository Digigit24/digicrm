"""
ai/tools.py — request-scoped CRM tool executor for the AI copilot (Phase 2a).

Design (see AI_COPILOT_PHASE2_PLAN.md §1):
  * Tools are executed by RE-CALLING digicrm's OWN REST API over localhost,
    forwarding the *caller's* JWT (Authorization header). Because those DRF
    viewsets already run JWTAuthenticationMiddleware + HasDigiPermission +
    TenantViewSetMixin, every tool call inherits tenant isolation and
    per-permission (own/team/all) scoping for FREE — we duplicate NO permission
    logic and never touch the mcp/ env-bound service account.
  * The tool *catalog / JSON schemas* are the single source of truth in
    mcp/server.py (TOOLS). We expose a curated safe subset here.
  * The model can never supply a tenant or user id — those come only from the
    JWT. Any tenant_id / owner_user_id in tool args is stripped before the call.

Exposed Phase-2a tools (10):
  reads  (auto-run)          : list_leads, get_lead, list_lead_statuses, list_users
  writes (confirm-before-run): create_lead, update_lead, update_lead_status,
                               create_task, create_meeting, create_lead_activity
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

import requests
from decouple import config

logger = logging.getLogger(__name__)

# Base URL of our own API for the internal self-call. Django's runserver is
# threaded, so a localhost self-call while streaming works fine. Configurable so
# it also works behind the real host in prod.
# NOTE: strip any inline "# comment" — python-decouple keeps it in the value,
# which would corrupt the URL (same .env gotcha that crashed boot).
AI_INTERNAL_BASE_URL = re.split(
    r"\s#", config("AI_INTERNAL_BASE_URL", default="http://127.0.0.1:8001"), maxsplit=1
)[0].strip().rstrip("/")

# (connect, read) timeouts for the internal REST hop.
_TIMEOUT = (5, 30)

# ─────────────────────────────────────────────────────────────────────────────
# Tool sets
# ─────────────────────────────────────────────────────────────────────────────

READ_TOOLS = {
    "list_leads",
    "get_lead",
    "list_lead_statuses",
    "list_users",
    "get_lead_context",   # composite read — stable envelope for AI grounding
}

# Write tools require the frontend confirm handshake before they run.
WRITE_TOOLS = {
    "create_lead",
    "update_lead",
    "update_lead_status",
    "create_task",
    "create_meeting",
    "create_lead_activity",
    "append_note",        # atomic append to Lead.notes (human page body)
    "create_lead_status", # new pipeline stage (backs Create-with-AI)
    "create_lead_group",  # new lead group/list (backs Create-with-AI)
}

EXPOSED_TOOLS = READ_TOOLS | WRITE_TOOLS
CONFIRMATION_REQUIRED = set(WRITE_TOOLS)

# Copilot-specific tools NOT in the mcp/server.py catalog — their schemas are
# defined here (get_lead_context is a composite read; append_note hits a
# dedicated append action).
_LOCAL_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_lead_context",
            "description": (
                "Fetch a single structured snapshot of a lead for grounding: its "
                "properties, freeform notes, recent activity timeline (calls, "
                "notes, meetings…), and open tasks. Call this before answering "
                "questions about a lead or logging a transcript."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "lead_id": {"type": "integer", "description": "ID of the lead"},
                    "activity_limit": {
                        "type": "integer",
                        "description": "Max recent activities to include (default 20)",
                    },
                },
                "required": ["lead_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "append_note",
            "description": (
                "Append a timestamped note block to the lead's freeform notes "
                "(the human 'page body'). Does NOT overwrite existing notes. Use "
                "for durable summaries; use create_lead_activity(type=NOTE) for a "
                "discrete timeline entry."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "lead_id": {"type": "integer", "description": "ID of the lead"},
                    "text": {"type": "string", "description": "Note text to append"},
                },
                "required": ["lead_id", "text"],
            },
        },
    },
]

# Fields the model must never be able to set — identity/tenant come from the JWT.
_FORBIDDEN_ARG_KEYS = {"tenant_id", "owner_user_id", "created_by", "by_user_id"}


def is_write_tool(name: str) -> bool:
    return name in CONFIRMATION_REQUIRED


def requires_confirmation(name: str) -> bool:
    return name in CONFIRMATION_REQUIRED


# ─────────────────────────────────────────────────────────────────────────────
# Tool schemas — pulled from the MCP catalog (single source of truth)
# ─────────────────────────────────────────────────────────────────────────────

def get_tool_schemas() -> List[dict]:
    """Return the exposed tools as OpenAI-compatible function-tool definitions.

    Reuses the JSON-Schema `inputSchema` defined once in mcp/server.py so the
    tool contract never drifts.
    """
    try:
        from mcp.server import TOOLS as MCP_TOOLS
    except Exception:  # noqa: BLE001 — never let a bad import kill the endpoint
        logger.exception("ai.tools: could not import MCP tool catalog")
        return []

    schemas: List[dict] = []
    covered = set()
    for t in MCP_TOOLS:
        name = t.get("name")
        if name not in EXPOSED_TOOLS:
            continue
        covered.add(name)
        schemas.append({
            "type": "function",
            "function": {
                "name": name,
                "description": (t.get("description") or "").strip(),
                "parameters": t.get("inputSchema") or {"type": "object", "properties": {}},
            },
        })
    # Append copilot-only tools not present in the MCP catalog.
    for local in _LOCAL_TOOL_SCHEMAS:
        if local["function"]["name"] in EXPOSED_TOOLS and local["function"]["name"] not in covered:
            schemas.append(local)
    return schemas


# ─────────────────────────────────────────────────────────────────────────────
# HTTP client — forwards the caller's JWT
# ─────────────────────────────────────────────────────────────────────────────

class ToolError(Exception):
    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message)
        self.status_code = status_code


class RequestScopedClient:
    """Calls digicrm's own REST API using the caller's Bearer JWT."""

    def __init__(self, auth_header: str):
        if not auth_header:
            raise ToolError("Missing Authorization header for tool execution", 401)
        self._auth = auth_header

    def _headers(self) -> dict:
        return {
            "Authorization": self._auth,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def call(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
    ) -> Any:
        url = f"{AI_INTERNAL_BASE_URL}{path}"
        try:
            resp = requests.request(
                method, url,
                headers=self._headers(),
                params=params,
                json=json_body,
                timeout=_TIMEOUT,
            )
        except requests.exceptions.Timeout:
            raise ToolError("CRM API timed out", 504)
        except requests.exceptions.ConnectionError as exc:
            raise ToolError(f"Cannot reach CRM API: {exc}", 503)

        # 204 / empty body
        if resp.status_code == 204 or not resp.content:
            if resp.status_code >= 400:
                raise ToolError(f"HTTP {resp.status_code}", resp.status_code)
            return {"ok": True}

        try:
            data = resp.json()
        except ValueError:
            if resp.status_code >= 400:
                raise ToolError(f"HTTP {resp.status_code}", resp.status_code)
            return {"raw": resp.text[:500]}

        if resp.status_code >= 400:
            msg = None
            if isinstance(data, dict):
                msg = data.get("detail") or data.get("error") or data.get("message")
                if not msg:
                    # DRF field errors -> compact string
                    msg = "; ".join(f"{k}: {v}" for k, v in data.items())
            raise ToolError(msg or f"HTTP {resp.status_code}", resp.status_code)

        return data


# ─────────────────────────────────────────────────────────────────────────────
# Tool → REST plan builders
# ─────────────────────────────────────────────────────────────────────────────
# Each builder returns (method, path, params, json_body). Field names are mapped
# from the MCP tool-arg names to the REST serializer field names (verified in
# crm/meetings/tasks serializers): lead_id→lead, start_time→start_at, etc.

def _clean(d: dict) -> dict:
    """Drop None values and forbidden identity/tenant keys."""
    return {
        k: v for k, v in d.items()
        if v is not None and k not in _FORBIDDEN_ARG_KEYS
    }


def _plan(name: str, args: dict):
    a = {k: v for k, v in (args or {}).items() if k not in _FORBIDDEN_ARG_KEYS}

    # ---- reads ----
    if name == "list_leads":
        params: Dict[str, Any] = {}
        for k in ("search", "page", "page_size"):
            if a.get(k) is not None:
                params[k] = a[k]
        if a.get("assigned_to"):
            params["assigned_to"] = a["assigned_to"]
        elif a.get("unassigned"):
            params["assigned_to__isnull"] = "true"
        return "GET", "/api/crm/leads/", params, None

    if name == "get_lead":
        return "GET", f"/api/crm/leads/{a['lead_id']}/", None, None

    if name == "list_lead_statuses":
        return "GET", "/api/crm/statuses/", None, None

    if name == "list_users":
        params = {"page_size": a.get("page_size", 100)}
        if a.get("search"):
            params["search"] = a["search"]
        return "GET", "/api/crm/users/", params, None

    # ---- writes ----
    if name == "create_lead":
        body = _clean({
            "name": a.get("name"),
            "phone": a.get("phone"),
            "email": a.get("email"),
            "source": a.get("source"),
            "lead_score": a.get("lead_score"),
            "notes": a.get("notes"),
            "assigned_to": a.get("assigned_to"),
            "metadata": a.get("custom_fields"),  # custom_fields -> metadata JSON
        })
        return "POST", "/api/crm/leads/", None, body

    if name == "update_lead":
        lead_id = a["lead_id"]
        body = _clean({
            "name": a.get("name"),
            "phone": a.get("phone"),
            "email": a.get("email"),
            "source": a.get("source"),
            "lead_score": a.get("lead_score"),
            "notes": a.get("notes"),
            "assigned_to": a.get("assigned_to"),
            "metadata": a.get("custom_fields"),
        })
        return "PATCH", f"/api/crm/leads/{lead_id}/", None, body

    if name == "update_lead_status":
        lead_id = a["lead_id"]
        body: Dict[str, Any] = {"status": a["status_id"]}  # status_id -> status FK
        if a.get("note"):
            body["notes"] = a["note"]
        return "PATCH", f"/api/crm/leads/{lead_id}/", None, body

    if name == "create_task":
        body = _clean({
            "title": a.get("title"),
            "description": a.get("description"),
            "lead": a.get("lead_id"),          # lead_id -> lead
            "due_date": a.get("due_date"),
            "priority": a.get("priority"),
            "assignee_user_id": a.get("assignee_user_id"),
        })
        return "POST", "/api/tasks/", None, body

    if name == "create_meeting":
        body = _clean({
            "title": a.get("title"),
            "lead": a.get("lead_id"),          # lead_id -> lead
            "start_at": a.get("start_time"),   # start_time -> start_at
            "end_at": a.get("end_time"),       # end_time -> end_at
            "location": a.get("location"),
            "description": a.get("description"),
        })
        return "POST", "/api/meetings/", None, body

    if name == "create_lead_activity":
        body = _clean({
            "lead": a.get("lead_id"),          # lead_id -> lead
            "type": a.get("type"),
            "content": a.get("content"),
            "happened_at": a.get("happened_at"),
        })
        return "POST", "/api/crm/activities/", None, body

    if name == "append_note":
        lead_id = a["lead_id"]
        return "POST", f"/api/crm/leads/{lead_id}/append-note/", None, {"text": a.get("text")}

    if name == "create_lead_status":
        body = _clean({
            "name": a.get("name"),
            "order_index": a.get("order_index"),  # optional — server appends if omitted
            "color_hex": a.get("color_hex"),
            "is_won": a.get("is_won"),
            "is_lost": a.get("is_lost"),
            "is_active": a.get("is_active"),
        })
        return "POST", "/api/crm/statuses/", None, body

    if name == "create_lead_group":
        body = _clean({
            "name": a.get("name"),
            "description": a.get("description"),
            "color_hex": a.get("color_hex"),
        })
        return "POST", "/api/crm/lead-groups/", None, body

    raise ToolError(f"Unknown or unexposed tool: {name}", 400)


# ─────────────────────────────────────────────────────────────────────────────
# Public entrypoint
# ─────────────────────────────────────────────────────────────────────────────

def _auth_header(request) -> str:
    return request.META.get("HTTP_AUTHORIZATION") or getattr(
        getattr(request, "headers", None), "get", lambda *_: None
    )("Authorization")


def _results_list(payload) -> list:
    """Normalize a paginated or plain-list REST payload to a list."""
    if isinstance(payload, dict):
        return payload.get("results") or []
    if isinstance(payload, list):
        return payload
    return []


def _get_lead_context(client: "RequestScopedClient", args: dict) -> dict:
    """Composite read → stable envelope for AI grounding.

    Combines the lead, its freeform notes, recent activity timeline, and open
    tasks into one consistent shape. Each sub-call is JWT-forwarded, so tenant +
    RBAC apply; a failed sub-call degrades to an empty section rather than
    failing the whole context.
    """
    lead_id = args["lead_id"]
    limit = args.get("activity_limit") or 20

    lead = client.call("GET", f"/api/crm/leads/{lead_id}/")

    try:
        acts = client.call("GET", "/api/crm/activities/", params={
            "lead": lead_id, "ordering": "-happened_at", "page_size": limit,
        })
        recent_activities = [
            {"type": r.get("type"), "content": r.get("content"),
             "happened_at": r.get("happened_at"), "by_user_id": r.get("by_user_id")}
            for r in _results_list(acts)
        ]
    except ToolError:
        recent_activities = []

    try:
        tasks = client.call("GET", "/api/tasks/", params={
            "lead": lead_id, "ordering": "due_date", "page_size": 50,
        })
        open_tasks = [
            {"id": r.get("id"), "title": r.get("title"),
             "status": r.get("status"), "due_date": r.get("due_date"),
             "priority": r.get("priority")}
            for r in _results_list(tasks)
            if r.get("status") not in ("DONE", "CANCELLED")
        ]
    except ToolError:
        open_tasks = []

    lead_props = {
        k: lead.get(k) for k in (
            "id", "name", "phone", "email", "company", "title", "status",
            "status_name", "priority", "lead_score", "source", "owner_user_id",
            "assigned_to", "city", "state", "country",
            "last_contacted_at", "next_follow_up_at",
        )
    } if isinstance(lead, dict) else {}

    return {
        "lead": lead_props,
        "notes": lead.get("notes") if isinstance(lead, dict) else None,
        "custom_fields": lead.get("metadata") if isinstance(lead, dict) else None,
        "recent_activities": recent_activities,
        "open_tasks": open_tasks,
    }


def execute_tool(request, name: str, args: Optional[dict]) -> dict:
    """Execute one tool as the calling user. Never raises — returns a dict.

    Returns the REST payload on success, or {"error", "status"} on failure so
    the model can read the error and explain it to the user.
    """
    args = args or {}
    if name not in EXPOSED_TOOLS:
        return {"error": f"Tool '{name}' is not available.", "status": 400}

    try:
        auth = _auth_header(request)
        client = RequestScopedClient(auth)
        logger.info(
            "AI tool call: tool=%s tenant=%s user=%s",
            name,
            getattr(request, "tenant_id", None),
            getattr(request, "user_id", None),
        )
        # Composite read tool (multiple sub-calls) — handle before _plan.
        if name == "get_lead_context":
            return _get_lead_context(client, args)

        method, path, params, body = _plan(name, args)
        result = client.call(method, path, params=params, json_body=body)
        return result if isinstance(result, (dict, list)) else {"result": result}
    except ToolError as exc:
        logger.warning("AI tool '%s' failed: %s (%s)", name, exc, exc.status_code)
        return {"error": str(exc), "status": exc.status_code}
    except KeyError as exc:
        return {"error": f"Missing required argument: {exc}", "status": 400}
    except Exception as exc:  # noqa: BLE001 — never crash the stream
        logger.exception("AI tool '%s' unexpected error", name)
        return {"error": f"Internal error: {exc}", "status": 500}
