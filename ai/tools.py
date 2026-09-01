"""
ai/tools.py — request-scoped CRM tool executor for the AI copilot (Phase 2a).

Design (see docs/ai-tool-execution.md):
  * Tools are executed by dispatching digicrm's OWN REST views IN-PROCESS as
    the calling user — `resolve()` the path, hand the view a request carrying
    the caller's already-verified identity, call it in the same thread.
    Because those DRF viewsets still run JWTRequestAuthentication +
    HasDigiPermission + TenantViewSetMixin, every tool call inherits tenant
    isolation and per-permission (own/team/all) scoping for FREE — we
    duplicate NO permission logic and never touch the mcp/ env-bound service
    account.
  * This used to be an HTTP self-call over localhost. It ran from inside an
    open SSE response, so one worker blocked waiting on another worker to
    answer it — a deadlock under gunicorn sync workers that only shows up
    under concurrency. See RequestScopedClient for the full reasoning.
  * There are TWO tool-execution systems in this repo and they must not be
    merged: this one (per-user JWT) and mcp/django_view.py (env-bound service
    account, direct ORM, external MCP clients). docs/ai-tool-execution.md
    explains which to touch when adding a tool.
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
import json
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.test import RequestFactory
from django.urls import Resolver404, resolve

from common.middleware import get_current_tenant_id, set_current_tenant_id

logger = logging.getLogger(__name__)

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


# Attributes `JWTAuthenticationMiddleware` puts on an authenticated request.
# `JWTRequestAuthentication.authenticate()` reads exactly these to build the
# TenantUser, and `HasDigiPermission` + the ownership scoping in
# `common/permissions.py` read them too. Copying the whole set onto the
# synthetic request is what makes the in-process call behave identically to the
# HTTP one — miss one and a view could silently see a different identity than
# the caller's.
_AUTH_CONTEXT_ATTRS = (
    "user_id",
    "email",
    "tenant_id",
    "tenant_slug",
    "is_super_admin",
    "permissions",
    "enabled_modules",
    "roles",
)


class RequestScopedClient:
    """
    Runs digicrm's own REST views IN-PROCESS as the calling user.

    WHY NOT HTTP (this used to POST to itself over localhost)
    --------------------------------------------------------
    `execute_tool()` is invoked from inside the SSE generator of
    `AIChatView`, i.e. while a streaming response is still open and holding a
    worker. Making a blocking `requests` call back into the same server from
    there means one worker waits on another worker to answer it. Under
    gunicorn with N sync workers, N concurrent copilot turns occupy every
    worker and each is blocked on a request that needs a free worker — a
    self-call deadlock. It does not fail loudly; it hangs, and only under
    load, so it looks fine in testing and falls over in production.

    The old module docstring reasoned "Django's runserver is threaded, so a
    localhost self-call while streaming works fine." True of runserver, and
    not true of a small sync-worker deployment. That assumption is now gone
    rather than merely documented.

    WHY THIS IS STILL SAFE
    ----------------------
    The point of the HTTP hop was that it inherited tenant isolation and
    per-permission scoping for free by running the real DRF stack. This keeps
    exactly that: `resolve()` finds the same view, and calling it runs the
    same authentication class, the same `HasDigiPermission`, the same
    `TenantViewSetMixin` and the same serializers. NO permission logic is
    duplicated here, which is the property that must never be traded away.

    What changes is only the transport: a nested Python call instead of a
    socket round-trip. The identity travels as the request attributes the JWT
    middleware would have set, copied verbatim from the already-authenticated
    outer request, so nothing is re-decoded and nothing can drift.

    The model still cannot supply a tenant or user id: those are read from the
    outer request only, and `_FORBIDDEN_ARG_KEYS` strips them from tool args
    before any of this runs.
    """

    def __init__(self, request):
        if request is None:
            raise ToolError("Missing request context for tool execution", 401)
        if getattr(request, "user_id", None) is None or getattr(request, "tenant_id", None) is None:
            # Unauthenticated callers must never reach a view through this
            # path. The outer AIChatView already requires authentication; this
            # is the belt-and-braces check for any future caller.
            raise ToolError("Unauthenticated request cannot execute tools", 401)
        self._outer = request
        self._factory = RequestFactory()

    def _build_request(self, method: str, path: str, params, json_body):
        """A synthetic request carrying the caller's verified identity."""
        method = method.upper()
        if method == "GET":
            inner = self._factory.get(path, data=params or None)
        elif method == "DELETE":
            inner = self._factory.delete(path)
        else:
            inner = self._factory.generic(
                method,
                path,
                data=json.dumps(json_body or {}, default=str),
                content_type="application/json",
            )
            if params:
                inner.META["QUERY_STRING"] = urlencode(params, doseq=True)

        for attr in _AUTH_CONTEXT_ATTRS:
            if not hasattr(self._outer, attr):
                # Fail loudly. Every missing attribute degrades fail-closed
                # (no permissions -> refused, no tenant -> empty queryset), so
                # the danger is not a leak, it is a silent "the assistant sees
                # nothing" that looks like a data problem for hours.
                raise ToolError(
                    f"Outer request is missing '{attr}'; refusing to dispatch "
                    "a tool with partial identity.",
                    401,
                )
            setattr(inner, attr, getattr(self._outer, attr))

        # Some views read the raw header (or hand it onward). Carry it so the
        # in-process request is not subtly less complete than the HTTP one was.
        auth = self._outer.META.get("HTTP_AUTHORIZATION")
        if auth:
            inner.META["HTTP_AUTHORIZATION"] = auth

        return inner

    def call(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
    ) -> Any:
        try:
            match = resolve(path)
        except Resolver404:
            raise ToolError(f"No CRM endpoint for {path}", 404)

        inner = self._build_request(method, path, params, json_body)

        # The middleware that normally sets this is skipped on this path, so
        # setting it here is LOAD-BEARING, not decorative: `execute_tool` runs
        # inside a StreamingHttpResponse generator, and the thread-local is
        # thread-scoped. Today that generator is consumed on the request thread
        # under sync/gthread/gevent workers, so the middleware's value is still
        # in place — but nothing guarantees that forever (ASGI, or any future
        # threadpool consumption), and this is what makes it safe when it stops
        # being true. Restored in `finally` so nothing leaks into the outer turn.
        #
        # Deliberately NOT shadowed: `set_current_request()`. Its only consumers
        # are the session-based Django-admin auth backends, which are not on the
        # DRF path. If a `get_current_request()` helper ever lands on this path
        # it would read the OUTER request — same identity, but the wrong path
        # and method — so shadow it here if that day comes.
        previous_tenant = get_current_tenant_id()
        set_current_tenant_id(getattr(self._outer, "tenant_id", None))
        try:
            response = match.func(inner, *match.args, **match.kwargs)
        except Http404:
            raise ToolError("Not found", 404)
        except PermissionDenied as exc:
            # A permission failure raised rather than returned still has to
            # read as 403 to the model, not as an internal error.
            raise ToolError(str(exc) or "Permission denied", 403)
        finally:
            set_current_tenant_id(previous_tenant)

        return self._unwrap(response)

    @staticmethod
    def _unwrap(response) -> Any:
        """Turn a view's response into the plain payload, or a ToolError."""
        status_code = getattr(response, "status_code", 500)

        # DRF hands back the deserialized object on `.data`, so the round trip
        # through JSON that the HTTP version paid for is simply skipped.
        data = getattr(response, "data", None)
        if data is None and status_code != 204:
            raw = getattr(response, "content", b"") or b""
            if raw:
                try:
                    data = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    if status_code >= 400:
                        raise ToolError(f"HTTP {status_code}", status_code)
                    return {"raw": raw.decode("utf-8", "replace")[:500]}

        if status_code >= 400:
            msg = None
            if isinstance(data, dict):
                msg = data.get("detail") or data.get("error") or data.get("message")
                if not msg:
                    # DRF field errors -> one compact line the model can relay.
                    msg = "; ".join(f"{k}: {v}" for k, v in data.items())
            raise ToolError(str(msg) if msg else f"HTTP {status_code}", status_code)

        if data is None:
            return {"ok": True}
        return data


# ─────────────────────────────────────────────────────────────────────────────
# Tool → REST plan builders
# ─────────────────────────────────────────────────────────────────────────────
# Each builder returns (method, path, params, json_body). Field names are mapped
# from the MCP tool-arg names to the REST serializer field names (verified in
# crm/meetings/tasks serializers): lead_id→lead, start_time→start_at, etc.

def _lead_id(value) -> int:
    """
    Coerce an interpolated id to an int before it becomes a URL segment.

    DRF's default detail lookup is `(?P<pk>[^/.]+)`, which excludes `/` and `.`
    but NOT `?`. So a `lead_id` of `1?assigned_to=x` resolves, and the query
    string lands on the synthetic request. It dead-ends today (the pk keeps the
    literal string and 404s), but an id is a number and there is no reason to
    let anything else reach a path. A non-numeric value raises ValueError, which
    `execute_tool` turns into a clean error dict.
    """
    return int(value)


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
        return "GET", f"/api/crm/leads/{_lead_id(a['lead_id'])}/", None, None

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
        return "PATCH", f"/api/crm/leads/{_lead_id(lead_id)}/", None, body

    if name == "update_lead_status":
        lead_id = a["lead_id"]
        body: Dict[str, Any] = {"status": a["status_id"]}  # status_id -> status FK
        if a.get("note"):
            body["notes"] = a["note"]
        return "PATCH", f"/api/crm/leads/{_lead_id(lead_id)}/", None, body

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
        return "POST", f"/api/crm/leads/{_lead_id(lead_id)}/append-note/", None, {"text": a.get("text")}

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

    lead = client.call("GET", f"/api/crm/leads/{_lead_id(lead_id)}/")

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
    if name not in EXPOSED_TOOLS:
        return {"error": f"Tool '{name}' is not available.", "status": 400}

    # Sanitize HERE, not in `_plan`. `get_lead_context` routes around `_plan`
    # entirely (composite read, below), so a filter that lived only in `_plan`
    # left one of the fourteen tools unsanitized. It happens to be harmless
    # today — that tool reads only lead_id/activity_limit — but a sanitizer
    # with a hole in it is worse than an obvious one, because the next tool
    # added next to it inherits the gap silently.
    args = {
        k: v for k, v in (args or {}).items() if k not in _FORBIDDEN_ARG_KEYS
    }

    try:
        client = RequestScopedClient(request)
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
