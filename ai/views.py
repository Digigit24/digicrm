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

from common.env import config
from django.http import StreamingHttpResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from rest_framework import status
from rest_framework.response import Response

from common.authentication import JWTRequestAuthentication
from common.pagination import StandardPagination
from . import tools as ai_tools
from .models import AIChatMessage, AIChatSession
from .providers import (
    TRANSCRIBE_ALLOWED_EXTENSIONS,
    TRANSCRIBE_MAX_BYTES,
    TranscriptionError,
    any_provider_configured,
    stream_agent,
    stream_chat,
    transcribe_audio,
)
from .serializers import (
    AIChatMessageBatchCreateSerializer,
    AIChatMessageSerializer,
    AIChatSessionCreateSerializer,
    AIChatSessionDetailSerializer,
    AIChatSessionListSerializer,
    AIChatSessionUpdateSerializer,
)

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
    "AI provider not configured. Set one of these in digicrm/.env.local: "
    "HERMES_API_KEY + HERMES_API_BASE_URL (any OpenAI-compatible endpoint), "
    "MOONSHOT_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, or XAI_API_KEY."
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


class AIVoiceTranscribeView(APIView):
    """POST multipart file upload -> {"text": "..."} (OpenAI Whisper).

    For the crmflutter AI Copilot's voice-input affordance: the client
    records audio locally, uploads it here, and gets back plain text to seed
    the chat composer. Non-streaming (unlike AIChatView) — record-then-upload
    doesn't benefit from SSE, and a single JSON response is what a mobile
    client naturally wants for one file.

    Same auth pattern as AIChatView. Never raises a bare 500 for a request
    problem or an OpenAI-side failure — every failure path returns a JSON
    {"error": "..."} with an appropriate status code (400 for a bad
    upload/format the client can fix, 502 for a downstream failure, 503 if
    the server has no OPENAI_API_KEY configured).
    """

    authentication_classes = [JWTRequestAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        uploaded_file = request.FILES.get("file")
        if not uploaded_file:
            return Response(
                {"error": 'No audio file provided. Send it under the "file" field.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if uploaded_file.size > TRANSCRIBE_MAX_BYTES:
            return Response(
                {
                    "error": (
                        f"Audio file too large "
                        f"({uploaded_file.size / (1024 * 1024):.1f}MB); "
                        f"the limit is {TRANSCRIBE_MAX_BYTES // (1024 * 1024)}MB."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        ext = uploaded_file.name.rsplit(".", 1)[-1].lower() if "." in uploaded_file.name else ""
        if ext not in TRANSCRIBE_ALLOWED_EXTENSIONS:
            return Response(
                {
                    "error": (
                        f"Unsupported audio format '.{ext or '?'}'. Supported: "
                        + ", ".join(sorted(TRANSCRIBE_ALLOWED_EXTENSIONS))
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        user_id = getattr(request, "user_id", None)
        tenant_id = getattr(request, "tenant_id", None)
        logger.info(
            "Voice transcription request: user=%s tenant=%s filename=%s size=%s",
            user_id, tenant_id, uploaded_file.name, uploaded_file.size,
        )

        try:
            text = transcribe_audio(
                uploaded_file, uploaded_file.name, uploaded_file.content_type
            )
        except TranscriptionError as exc:
            logger.warning(
                "Voice transcription failed; user=%s status=%s", user_id, exc.status_code
            )
            return Response({"error": exc.message}, status=exc.status_code)
        except Exception as exc:  # noqa: BLE001 — app-wide "never bare 500" convention
            logger.error(
                "Voice transcription raised unexpectedly; error_type=%s",
                exc.__class__.__name__,
            )
            return Response(
                {"error": "Voice transcription failed. Please try again."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response({"text": text})


class AIChatSessionListView(APIView):
    """
    GET /api/ai/sessions/ — list the caller's chat sessions.

    Returns paginated sessions ordered by -updated_at.
    Each item: {id, title, created_at, updated_at, message_count}
    """
    authentication_classes = [JWTRequestAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tenant_id = getattr(request, 'tenant_id', None)
        user_id = getattr(request, 'user_id', None)
        if not tenant_id or not user_id:
            return Response(
                {'error': 'Tenant or user scope missing'},
                status=status.HTTP_403_FORBIDDEN,
            )

        qs = AIChatSession.objects.filter(
            tenant_id=tenant_id,
            user_id=user_id,
        )

        # Manual pagination: `paginate_queryset`/`get_paginated_response` are
        # `GenericAPIView` methods (via its pagination mixin) — this is a
        # plain `APIView`, which doesn't have them at all. Instantiating the
        # paginator directly is the fix, not switching base classes (every
        # other view in this file is a plain APIView too; changing just this
        # one's inheritance would be its own inconsistency).
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        if page is not None:
            serializer = AIChatSessionListSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)

        serializer = AIChatSessionListSerializer(qs, many=True)
        return Response(serializer.data)

    def post(self, request):
        # Was its own `AIChatSessionCreateView`, `POST`-only, registered on
        # this exact same `sessions/` path in urls.py. Django/DRF route by
        # first URL match, not by HTTP method, so that class never received
        # a single request — every POST landed here instead and 405'd,
        # since this class only defined `get()`. Found by actually calling
        # the endpoint, not from reading the code. Merged in rather than
        # re-registered as its own path, matching the one-view-per-URL shape
        # `AIChatSessionDetailView` below now also has.
        tenant_id = getattr(request, 'tenant_id', None)
        user_id = getattr(request, 'user_id', None)
        if not tenant_id or not user_id:
            return Response(
                {'error': 'Tenant or user scope missing'},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = AIChatSessionCreateSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)

        session = AIChatSession.objects.create(
            tenant_id=tenant_id,
            user_id=user_id,
            title=serializer.validated_data.get('title', ''),
        )

        out = AIChatSessionListSerializer(session)
        return Response(out.data, status=status.HTTP_201_CREATED)


class AIChatSessionDetailView(APIView):
    """
    GET /api/ai/sessions/{id}/ — get a session with all messages (for resume).
    PATCH /api/ai/sessions/{id}/ — rename a session. Body: {title}.
    DELETE /api/ai/sessions/{id}/ — delete a session (cascades to messages).

    Returns (GET): {id, title, created_at, updated_at, messages: [{id, role, content, sequence, created_at}]}

    One view class for all three verbs on this path — was three
    (`AIChatSessionDetailView`/`Update`/`Delete`), each registered on the
    exact same `sessions/<id>/` URL. Same first-match-wins bug as the list
    endpoint above: PATCH/DELETE both 405'd against the GET-only class that
    actually owned the route, and the other two classes were unreachable
    dead code.
    """
    authentication_classes = [JWTRequestAuthentication]
    permission_classes = [IsAuthenticated]

    def _get_session(self, request, id):
        """Shared tenant/user-scoped lookup for all three verbs below.
        Returns `(session, None)` or `(None, error_response)`."""
        tenant_id = getattr(request, 'tenant_id', None)
        user_id = getattr(request, 'user_id', None)
        if not tenant_id or not user_id:
            return None, Response(
                {'error': 'Tenant or user scope missing'},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            session = AIChatSession.objects.get(
                id=id,
                tenant_id=tenant_id,
                user_id=user_id,
            )
        except AIChatSession.DoesNotExist:
            return None, Response(
                {'error': 'Session not found'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return session, None

    def get(self, request, id):
        session, error = self._get_session(request, id)
        if error:
            return error
        serializer = AIChatSessionDetailSerializer(session)
        return Response(serializer.data)

    def patch(self, request, id):
        session, error = self._get_session(request, id)
        if error:
            return error
        serializer = AIChatSessionUpdateSerializer(session, data=request.data or {}, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        out = AIChatSessionListSerializer(session)
        return Response(out.data)

    def delete(self, request, id):
        session, error = self._get_session(request, id)
        if error:
            return error
        session.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class AIChatSessionMessagesAppendView(APIView):
    """
    POST /api/ai/sessions/{id}/messages/ — append messages to a session.

    Body: {messages: [{role, content}, ...]} — batch append.
    Returns created messages with assigned sequence numbers; bumps session.updated_at.
    """
    authentication_classes = [JWTRequestAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, id):
        tenant_id = getattr(request, 'tenant_id', None)
        user_id = getattr(request, 'user_id', None)
        if not tenant_id or not user_id:
            return Response(
                {'error': 'Tenant or user scope missing'},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            session = AIChatSession.objects.get(
                id=id,
                tenant_id=tenant_id,
                user_id=user_id,
            )
        except AIChatSession.DoesNotExist:
            return Response(
                {'error': 'Session not found'},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = AIChatMessageBatchCreateSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)

        messages_data = serializer.validated_data['messages']

        # Determine next sequence number
        last_seq = session.messages.order_by('-sequence').values_list('sequence', flat=True).first() or 0

        created_messages = []
        for i, msg_data in enumerate(messages_data):
            msg = AIChatMessage.objects.create(
                session=session,
                role=msg_data['role'],
                content=msg_data['content'],
                sequence=last_seq + i + 1,
            )
            created_messages.append(msg)

        # Bump session updated_at (auto_now handles this on save)
        session.save(update_fields=['updated_at'])

        out = AIChatMessageSerializer(created_messages, many=True)
        return Response(out.data, status=status.HTTP_201_CREATED)
