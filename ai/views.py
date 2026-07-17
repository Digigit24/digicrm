"""
AI copilot chat endpoint (Phase 1) — POST /api/ai/chat/

Streams Server-Sent Events (text/event-stream):
    data: {"type":"text-delta","delta":"..."}   (repeated)
    data: [DONE]

Guarantees:
  * Never returns 500 for the streaming path — any error is emitted as a single
    error/text delta inside the stream, then [DONE].
  * Auth is the same JWT Bearer used by the other CRM endpoints; tenant/user are
    read from the request attributes set by JWTAuthenticationMiddleware.
"""

import json
import logging
import re

from decouple import config
from django.http import StreamingHttpResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from common.authentication import JWTRequestAuthentication
from . import tools as ai_tools
from .providers import any_provider_configured, stream_agent, stream_chat

logger = logging.getLogger(__name__)


def _env_clean(key: str, default: str) -> str:
    """Read an env var and strip any inline ``# comment``.

    python-decouple does NOT strip inline comments, so a .env line like
    ``AI_TOOLS_ENABLED=true   # note`` yields the whole string as the value and
    breaks bool/int casts (a real boot-crash we hit). Strip a comment only when
    it follows whitespace, so genuine ``#`` in a value (e.g. a URL fragment) is
    preserved.
    """
    raw = config(key, default=default)
    if isinstance(raw, str):
        raw = re.split(r"\s#", raw, maxsplit=1)[0].strip()
    return raw


def _env_bool(key: str, default: bool) -> bool:
    return _env_clean(key, "true" if default else "false").lower() in ("1", "true", "yes", "on")


def _env_int(key: str, default: int) -> int:
    try:
        return int(_env_clean(key, str(default)))
    except (TypeError, ValueError):
        return default


AI_TOOLS_ENABLED = _env_bool("AI_TOOLS_ENABLED", True)
AI_MAX_TOOL_ITERS = _env_int("AI_MAX_TOOL_ITERS", 5)

NOT_CONFIGURED_MESSAGE = (
    "AI provider not configured — add MOONSHOT_API_KEY to digicrm/.env"
)


def _sse(delta_type: str, **fields) -> str:
    """Serialize one SSE data frame."""
    payload = {"type": delta_type, **fields}
    return f"data: {json.dumps(payload)}\n\n"


def _done() -> str:
    return "data: [DONE]\n\n"


class AIChatView(APIView):
    """POST { messages:[{role,content}], tool?, context? } -> SSE text stream."""

    authentication_classes = [JWTRequestAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        # Parse the body defensively — a bad body must still yield a clean SSE
        # stream (never a 500).
        try:
            body = request.data if isinstance(request.data, dict) else {}
        except Exception:  # noqa: BLE001
            body = {}

        messages = body.get("messages") or []
        # The client-supplied `tool` / `context` are UNTRUSTED and ADVISORY ONLY:
        # they steer the system prompt but NEVER authorize an action. Real
        # authorization happens server-side — the model's tool calls are gated by
        # ai_tools.EXPOSED_TOOLS (allow-list) + arg sanitization, and every tool
        # executes as the caller's JWT so HasDigiPermission + tenant scoping
        # apply. Sanitize the hint so it can't be abused for prompt bloat.
        tool = body.get("tool")
        if not isinstance(tool, str) or len(tool) > 64:
            tool = None
        context = body.get("context")
        if not isinstance(context, dict):
            context = None
        # Confirm-before-write handshake (see AI_COPILOT_PHASE2_PLAN.md §1.5):
        # on approval/rejection the frontend re-POSTs echoing the proposed calls
        # plus the per-call decision.
        pending_tool_calls = body.get("pending_tool_calls") or None
        confirmations = body.get("confirmations") or None

        user_id = getattr(request, "user_id", None)
        tenant_id = getattr(request, "tenant_id", None)
        logger.info(
            "AI chat request: user=%s tenant=%s tool=%s msgs=%s pending=%s",
            user_id, tenant_id, tool,
            len(messages) if isinstance(messages, list) else 0,
            len(pending_tool_calls) if isinstance(pending_tool_calls, list) else 0,
        )

        def event_stream():
            try:
                if not any_provider_configured():
                    yield _sse("text-delta", delta=NOT_CONFIGURED_MESSAGE)
                    yield _done()
                    return

                tool_schemas = ai_tools.get_tool_schemas() if AI_TOOLS_ENABLED else []

                # Text-only path when tool-calling is disabled or no tools are
                # exposed (keeps the Phase-1 contract working).
                if not tool_schemas and not pending_tool_calls:
                    for chunk in stream_chat(messages, tool=tool, context=context):
                        if chunk:
                            yield _sse("text-delta", delta=chunk)
                    yield _done()
                    return

                def _execute(name, args):
                    return ai_tools.execute_tool(request, name, args)

                for evt in stream_agent(
                    messages,
                    tool_schemas,
                    _execute,
                    tool=tool,
                    context=context,
                    pending_tool_calls=pending_tool_calls,
                    confirmations=confirmations,
                    max_iters=AI_MAX_TOOL_ITERS,
                ):
                    evt_type = evt.pop("type", "text-delta")
                    yield _sse(evt_type, **evt)

                yield _done()
            except Exception as exc:  # noqa: BLE001 — never crash the stream
                logger.error("AI chat stream failed; error_type=%s", exc.__class__.__name__)
                yield _sse(
                    "text-delta",
                    delta="The AI assistant hit an error. Please try again.",
                )
                yield _done()

        response = StreamingHttpResponse(
            event_stream(), content_type="text/event-stream"
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"  # disable proxy buffering (nginx)
        # NOTE: do NOT set a `Connection` header here — it is a hop-by-hop header
        # that the WSGI server manages itself (Django's dev server rejects it).
        return response
