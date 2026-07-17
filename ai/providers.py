"""
Multi-provider AI text-streaming router for the Celiyo copilot (Phase 1).

Phase 1 scope: TEXT streaming only. No tool-calling (Phase 2).

Providers (tried in this order, using whichever API keys are configured):
    1. Kimi / Moonshot   — OpenAI-compatible  (MOONSHOT_API_KEY)  [PRIMARY]
    2. OpenAI            — OpenAI-compatible   (OPENAI_API_KEY)
    3. Gemini            — Google REST         (GEMINI_API_KEY)   [thin adapter]
    4. Grok / xAI        — OpenAI-compatible   (XAI_API_KEY)

We deliberately use `requests` (already a dependency) instead of the `openai`
SDK: the OpenAI-compatible providers all expose the same
`{base_url}/chat/completions` SSE contract, so a small shared wrapper is enough
and avoids adding/managing an extra dependency. Every provider function is a
generator that yields plain text chunks; the view converts those to SSE frames.
"""

from __future__ import annotations

import json
import logging
from typing import Dict, Generator, List, Optional

import requests
from decouple import config

logger = logging.getLogger(__name__)

# Connect timeout (s), read timeout (s). Streaming reads can be long.
_TIMEOUT = (10, 180)

# Generic, client-safe failure text. Keep provider internals out of responses.
_GENERIC_PROVIDER_ERROR = "The AI assistant could not reach any configured provider right now."
_GENERIC_INTERRUPTED_ERROR = "The AI assistant response was interrupted. Please try again."


# ─────────────────────────────────────────────────────────────────────────────
# Provider configuration
# ─────────────────────────────────────────────────────────────────────────────

def _provider_configs() -> List[dict]:
    """Return the ordered list of OpenAI-compatible providers that have a key."""
    configs = []

    moonshot_key = config("MOONSHOT_API_KEY", default="")
    if moonshot_key:
        configs.append({
            "name": "kimi",
            "base_url": config("MOONSHOT_BASE_URL", default="https://api.moonshot.ai/v1"),
            "api_key": moonshot_key,
            "model": config("MOONSHOT_MODEL", default="kimi-k2-0905-preview"),
        })

    openai_key = config("OPENAI_API_KEY", default="")
    if openai_key:
        configs.append({
            "name": "openai",
            "base_url": config("OPENAI_BASE_URL", default="https://api.openai.com/v1"),
            "api_key": openai_key,
            "model": config("OPENAI_MODEL", default="gpt-4o-mini"),
        })

    return configs


def _grok_config() -> Optional[dict]:
    key = config("XAI_API_KEY", default="")
    if not key:
        return None
    return {
        "name": "grok",
        "base_url": config("XAI_BASE_URL", default="https://api.x.ai/v1"),
        "api_key": key,
        "model": config("XAI_MODEL", default="grok-2-latest"),
    }


def _gemini_config() -> Optional[dict]:
    key = config("GEMINI_API_KEY", default="")
    if not key:
        return None
    return {
        "name": "gemini",
        "api_key": key,
        "model": config("GEMINI_MODEL", default="gemini-1.5-flash"),
    }


def any_provider_configured() -> bool:
    return bool(
        config("MOONSHOT_API_KEY", default="")
        or config("OPENAI_API_KEY", default="")
        or config("GEMINI_API_KEY", default="")
        or config("XAI_API_KEY", default="")
    )


# ─────────────────────────────────────────────────────────────────────────────
# Message normalization
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_messages(messages: List[dict]) -> List[dict]:
    """Coerce incoming messages to [{role, content:str}] with valid roles."""
    out = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role", "user")).lower()
        if role not in ("system", "user", "assistant"):
            role = "user"
        content = m.get("content", "")
        if not isinstance(content, str):
            content = json.dumps(content)
        out.append({"role": role, "content": content})
    if not out:
        out = [{"role": "user", "content": ""}]
    return out


def _system_preamble(tool: Optional[str], context: Optional[dict]) -> Optional[str]:
    """Build an optional system message from the selected tool + context."""
    if not tool and not context:
        return None
    parts = ["You are the Celiyo CRM AI copilot. Be concise and helpful."]
    if tool:
        parts.append(f"The user invoked the tool/action: '{tool}'.")
    if context:
        try:
            parts.append("Context: " + json.dumps(context)[:2000])
        except (TypeError, ValueError):
            pass
    return " ".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# OpenAI-compatible streaming (Kimi / OpenAI / Grok)
# ─────────────────────────────────────────────────────────────────────────────

def _stream_openai_compatible(cfg: dict, messages: List[dict]) -> Generator[str, None, None]:
    url = f"{cfg['base_url'].rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }
    payload = {"model": cfg["model"], "messages": messages, "stream": True}

    with requests.post(url, headers=headers, json=payload, stream=True, timeout=_TIMEOUT) as resp:
        resp.raise_for_status()
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw:
                continue
            line = raw.strip()
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                return
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            try:
                delta = obj["choices"][0].get("delta", {}).get("content")
            except (KeyError, IndexError, TypeError):
                delta = None
            if delta:
                yield delta


# ─────────────────────────────────────────────────────────────────────────────
# Gemini streaming (thin REST adapter)
# ─────────────────────────────────────────────────────────────────────────────

def _stream_gemini(cfg: dict, messages: List[dict]) -> Generator[str, None, None]:
    model = cfg["model"]
    # Send the key via the x-goog-api-key HEADER, never in the URL, so it can't
    # leak into request exceptions / logs (which include the URL but not headers).
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:streamGenerateContent?alt=sse"
    )
    # Gemini uses roles user/model, and a separate system_instruction.
    contents = []
    system_instruction = None
    for m in messages:
        if m["role"] == "system":
            system_instruction = {"parts": [{"text": m["content"]}]}
            continue
        role = "model" if m["role"] == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": m["content"]}]})

    payload: Dict = {"contents": contents}
    if system_instruction:
        payload["system_instruction"] = system_instruction

    with requests.post(url, json=payload, stream=True, timeout=_TIMEOUT,
                       headers={"Content-Type": "application/json",
                                "x-goog-api-key": cfg["api_key"]}) as resp:
        resp.raise_for_status()
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw:
                continue
            line = raw.strip()
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                return
            try:
                obj = json.loads(data)
                parts = obj["candidates"][0]["content"]["parts"]
                for p in parts:
                    text = p.get("text")
                    if text:
                        yield text
            except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                continue


# ─────────────────────────────────────────────────────────────────────────────
# Public entrypoint: fallback chain → yields text chunks
# ─────────────────────────────────────────────────────────────────────────────

def stream_chat(
    messages: List[dict],
    tool: Optional[str] = None,
    context: Optional[dict] = None,
) -> Generator[str, None, None]:
    """
    Yield text chunks from the first working provider (Kimi→OpenAI→Gemini→Grok).

    Caller is responsible for the "no provider configured" case (see
    `any_provider_configured`). If providers exist but all fail, a single error
    sentence is yielded so the user sees something rather than silence.
    """
    norm = _normalize_messages(messages)
    preamble = _system_preamble(tool, context)
    if preamble:
        norm = [{"role": "system", "content": preamble}] + norm

    # Build the ordered provider chain.
    chain = list(_provider_configs())  # kimi, openai
    gem = _gemini_config()
    if gem:
        chain.append(gem)
    grok = _grok_config()
    if grok:
        chain.append(grok)

    produced_any = False

    for cfg in chain:
        try:
            if cfg["name"] == "gemini":
                gen = _stream_gemini(cfg, norm)
            else:
                gen = _stream_openai_compatible(cfg, norm)

            for chunk in gen:
                produced_any = True
                yield chunk

            if produced_any:
                return  # success — stop after the first provider that produced output
        except Exception as exc:  # noqa: BLE001 — never let a provider error crash the stream
            logger.warning(
                "AI provider failed; provider=%s error_type=%s produced_any=%s",
                cfg["name"],
                exc.__class__.__name__,
                produced_any,
            )
            if produced_any:
                # A provider already streamed partial output — do NOT fall through
                # to the next provider and splice a second provider's text into the
                # same assistant message. Finish with a generic interruption note.
                yield f"\n\n{_GENERIC_INTERRUPTED_ERROR}"
                return
            # Nothing produced yet — safe to try the next provider.
            continue

    if not produced_any:
        yield _GENERIC_PROVIDER_ERROR


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2a — tool-calling agent loop (OpenAI-compatible providers only)
# ─────────────────────────────────────────────────────────────────────────────
#
# stream_agent yields STRUCTURED events (dicts), which the view serializes as
# SSE frames. It layers tool-call / tool-result on top of the Phase-1 text-delta
# contract, so a Phase-1 client that ignores unknown types still renders text.
#
#   {"type":"text-delta","delta": "..."}
#   {"type":"tool-call","id","name","args","requires_confirmation","status"}
#   {"type":"tool-result","id","name","result","is_error"[,"declined"]}
#
# Confirm-before-write: READ tools auto-run and the loop continues; when the
# model proposes any WRITE tool the loop emits its tool-call with
# requires_confirmation=true / status="awaiting_confirmation" and STOPS (the view
# then emits [DONE]). The frontend renders Approve/Reject and re-POSTs with
# `pending_tool_calls` + `confirmations`; stream_agent replays those first.

_MAX_TOOL_ITERS_DEFAULT = 5


def _tool_capable_chain() -> List[dict]:
    """Ordered tool-capable providers with a key (Kimi → OpenAI → Grok).

    Gemini is intentionally excluded — its tool-calling wire format differs, so
    it stays a text-only fallback (see stream_chat).
    """
    chain = list(_provider_configs())  # kimi, openai
    grok = _grok_config()
    if grok:
        chain.append(grok)
    return chain


def _agent_provider_call(chain: List[dict], convo: List[dict], tools: List[dict]):
    """Stream one assistant turn, falling back across providers.

    Yields (kind, val, cfg) from the first provider that starts producing. Falls
    back to the next provider ONLY when the current one fails BEFORE producing
    any output — once text/tool-calls have streamed we must NOT switch providers
    mid-turn (that would splice two providers' output into one message). Raises
    the last error if every provider fails, or re-raises a mid-stream failure.
    """
    last_exc: Optional[Exception] = None
    for cfg in chain:
        produced = False
        try:
            for kind, val in _openai_stream_once(cfg, convo, tools):
                produced = True
                yield kind, val, cfg
            return  # provider finished cleanly
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.warning(
                "stream_agent provider failed; provider=%s error_type=%s produced=%s",
                cfg["name"], exc.__class__.__name__, produced,
            )
            if produced:
                # Already streamed to the client — cannot switch (anti-concat).
                raise
            continue  # nothing streamed yet — try the next provider
    if last_exc is not None:
        raise last_exc


def _agent_system(tool: Optional[str], context: Optional[dict]) -> str:
    parts = [
        "You are the Celiyo CRM AI copilot. You help sales users manage leads, "
        "tasks, meetings and activities. You can call CRM tools to read and write "
        "data. Prefer reading first to resolve ids: use get_lead_context to ground "
        "yourself on a specific lead (its properties, notes, recent activities and "
        "open tasks), and list_leads / list_lead_statuses / list_users otherwise. "
        "Write actions (create/update/append) are shown to the user for confirmation "
        "before they run, so propose them freely when appropriate. Be concise.",
        # Transcript / client-notes workflow (no dedicated endpoint — you orchestrate):
        "When the user gives you a call/meeting transcript or pasted client notes for "
        "a lead: (1) call get_lead_context for that lead to ground; (2) then propose "
        "the appropriate writes — create_lead_activity(type=NOTE/CALL/MEETING) for "
        "each interaction, append_note for a durable summary on the lead's notes, "
        "create_task for each action item (always include lead_id — tasks require a "
        "lead), and create_meeting when a follow-up is scheduled. Each write is "
        "confirmed by the user before it runs; batch related writes and briefly recap "
        "what you're about to log.",
        "Notes model: append_note adds to the lead's freeform 'page body' (Lead.notes) "
        "without overwriting; create_lead_activity(type=NOTE) records a discrete, "
        "timestamped timeline note. Use append_note for summaries, NOTE activities for "
        "logged events.",
    ]
    if tool:
        parts.append(f"The user launched the '{tool}' action — steer toward it.")
    if context:
        try:
            parts.append("Context: " + json.dumps(context)[:2000])
        except (TypeError, ValueError):
            pass
    return " ".join(parts)


def _openai_stream_once(cfg: dict, messages: List[dict], tools: List[dict]):
    """Stream one OpenAI-compatible completion with tools.

    Yields ("text", delta) for content, then a final ("final", {tool_calls,
    finish_reason}). tool_calls is a list of {"id","name","args"} (args parsed).
    """
    url = f"{cfg['base_url'].rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }
    payload = {"model": cfg["model"], "messages": messages, "stream": True}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    # index -> {id, name, arguments(str)}
    acc: Dict[int, dict] = {}
    finish_reason: Optional[str] = None

    with requests.post(url, headers=headers, json=payload, stream=True, timeout=_TIMEOUT) as resp:
        resp.raise_for_status()
        for raw in resp.iter_lines(decode_unicode=True):
            if not raw:
                continue
            line = raw.strip()
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            try:
                choice = obj["choices"][0]
            except (KeyError, IndexError, TypeError):
                continue

            delta = choice.get("delta") or {}
            content = delta.get("content")
            if content:
                yield ("text", content)

            for tc in (delta.get("tool_calls") or []):
                idx = tc.get("index", 0)
                slot = acc.setdefault(idx, {"id": None, "name": None, "arguments": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["arguments"] += fn["arguments"]

            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]

    tool_calls = []
    for idx in sorted(acc.keys()):
        slot = acc[idx]
        if not slot.get("name"):
            continue
        try:
            args = json.loads(slot["arguments"]) if slot["arguments"] else {}
        except json.JSONDecodeError:
            args = {}
        tool_calls.append({
            "id": slot.get("id") or f"call_{idx}",
            "name": slot["name"],
            "args": args,
        })

    yield ("final", {"tool_calls": tool_calls, "finish_reason": finish_reason})


def _assistant_tool_calls_msg(tool_calls: List[dict]) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": tc["id"],
                "type": "function",
                "function": {"name": tc["name"], "arguments": json.dumps(tc.get("args") or {})},
            }
            for tc in tool_calls
        ],
    }


def stream_agent(
    messages: List[dict],
    tools: List[dict],
    execute,
    tool: Optional[str] = None,
    context: Optional[dict] = None,
    pending_tool_calls: Optional[List[dict]] = None,
    confirmations: Optional[dict] = None,
    max_iters: int = _MAX_TOOL_ITERS_DEFAULT,
):
    """Drive the tool-calling loop, yielding structured SSE events.

    ``execute(name, args) -> dict`` runs a tool as the caller (never raises).
    ``tools`` are OpenAI function-tool schemas (empty → behaves like text chat).
    """
    from .tools import requires_confirmation

    chain = _tool_capable_chain()

    # No tool-capable provider (e.g. only Gemini configured) → text-only path.
    if not chain or not tools:
        for chunk in stream_chat(messages, tool=tool, context=context):
            if chunk:
                yield {"type": "text-delta", "delta": chunk}
        return

    convo: List[dict] = [{"role": "system", "content": _agent_system(tool, context)}]
    convo += _normalize_messages(messages)

    # ── Confirm-resume: replay the previously-proposed tool calls first ──
    if pending_tool_calls:
        confirmations = confirmations or {}
        convo.append(_assistant_tool_calls_msg(pending_tool_calls))
        for p in pending_tool_calls:
            call_id = p.get("id")
            name = p.get("name")
            args = p.get("args") or {}
            decision = confirmations.get(call_id) or {}
            approved = bool(decision.get("approved"))

            if requires_confirmation(name) and not approved:
                result = {"declined_by_user": True}
                if decision.get("reason"):
                    result["reason"] = decision["reason"]
                yield {"type": "tool-result", "id": call_id, "name": name,
                       "result": result, "is_error": False, "declined": True}
            else:
                result = execute(name, args)
                is_err = isinstance(result, dict) and "error" in result
                yield {"type": "tool-result", "id": call_id, "name": name,
                       "result": result, "is_error": is_err}
            convo.append({"role": "tool", "tool_call_id": call_id,
                          "content": json.dumps(result, default=str)})

    # ── Main loop ──
    # Fall back across providers only until one starts answering; then LOCK to it
    # for the rest of the turn/conversation so we never mix providers mid-stream.
    locked_cfg: Optional[dict] = None
    for _ in range(max(1, max_iters)):
        tool_calls: List[dict] = []
        active_chain = [locked_cfg] if locked_cfg is not None else chain
        try:
            for kind, val, used_cfg in _agent_provider_call(active_chain, convo, tools):
                if kind == "text" and val:
                    yield {"type": "text-delta", "delta": val}
                elif kind == "final":
                    locked_cfg = used_cfg  # lock to the provider that answered
                    tool_calls = val.get("tool_calls") or []
        except Exception:  # noqa: BLE001 — surface generically, don't crash/leak
            # _agent_provider_call already logged the redacted provider/error_type.
            yield {"type": "error", "message": _GENERIC_PROVIDER_ERROR}
            return

        if not tool_calls:
            return  # normal completion

        has_write = any(requires_confirmation(tc["name"]) for tc in tool_calls)

        for tc in tool_calls:
            needs = requires_confirmation(tc["name"])
            yield {
                "type": "tool-call",
                "id": tc["id"],
                "name": tc["name"],
                "args": tc["args"],
                "requires_confirmation": needs,
                "status": "awaiting_confirmation" if needs else "running",
            }

        if has_write:
            # Cannot proceed without the user's decision — pause the stream.
            return

        # All reads → execute inline and continue the loop.
        convo.append(_assistant_tool_calls_msg(tool_calls))
        for tc in tool_calls:
            result = execute(tc["name"], tc["args"])
            is_err = isinstance(result, dict) and "error" in result
            yield {"type": "tool-result", "id": tc["id"], "name": tc["name"],
                   "result": result, "is_error": is_err}
            convo.append({"role": "tool", "tool_call_id": tc["id"],
                          "content": json.dumps(result, default=str)})

    yield {"type": "text-delta", "delta": "\n\n_(Reached the tool-call limit for this turn.)_"}


# ─────────────────────────────────────────────────────────────────────────────
# Phase-2 multimodal stubs (OpenAI image + Whisper). Not wired in Phase 1.
# ─────────────────────────────────────────────────────────────────────────────

def transcribe_audio(*_args, **_kwargs):  # pragma: no cover - Phase 2
    raise NotImplementedError("Audio transcription (Whisper) lands in Phase 2.")


def analyze_image(*_args, **_kwargs):  # pragma: no cover - Phase 2
    raise NotImplementedError("Image analysis lands in Phase 2.")
