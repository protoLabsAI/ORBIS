"""Langfuse tracing — singleton + session/trace helpers.

One Langfuse client per process. Each WebRTC session creates a
Langfuse session; each user turn creates a trace spanning STT → LLM →
(optional tools) → TTS. Spans within the trace are labelled with the
same prefixes that appear in our log lines so grep-and-Langfuse stay
grep-correlatable.

Cross-fleet propagation: every outbound call to another agent carries
the current trace's `session_id` + `trace_id` via headers
(`Langfuse-Session-Id`, `Langfuse-Trace-Id`). Receiving agents adopt
those values when constructing their own spans; traces stitch together
across the protoLabs fleet rather than ending at our service boundary.
See `docs/reference/tracing-contract.md`.

Fail-open: if LANGFUSE_* env vars are unset, every helper here is a
no-op. Local dev without Langfuse keeps working; production gets full
tracing when the keys are present.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

_ENABLED = all(
    os.environ.get(k, "").strip()
    for k in ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")
)
_CLIENT: Any = None


def _lazy_client() -> Any:
    """Return the Langfuse client or None if not configured. Creates on
    first use to keep import-time work minimal."""
    global _CLIENT
    if not _ENABLED:
        return None
    if _CLIENT is not None:
        return _CLIENT
    try:
        from langfuse import Langfuse  # type: ignore[import-not-found]
    except Exception as e:
        logger.warning(f"[tracing] langfuse SDK import failed, disabling: {e}")
        return None
    try:
        _CLIENT = Langfuse(
            public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
            secret_key=os.environ["LANGFUSE_SECRET_KEY"],
            host=os.environ["LANGFUSE_HOST"],
        )
        logger.info(f"[tracing] langfuse client ready → {os.environ['LANGFUSE_HOST']}")
        return _CLIENT
    except Exception as e:
        logger.warning(f"[tracing] langfuse init failed, disabling: {e}")
        return None


def enabled() -> bool:
    """Public flag for callers that want to skip expensive prep when off."""
    return _ENABLED and _lazy_client() is not None


# ---------------------------------------------------------------------------
# Session / trace helpers — all no-op when disabled
# ---------------------------------------------------------------------------

class _NullSpan:
    """Stand-in object returned when tracing is off, so callers can use
    `with trace.span(...)` / `span.update(...)` / `span.end(...)` without
    `if enabled` guards everywhere."""
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def update(self, **_kwargs): return self
    def end(self, **_kwargs): return None
    def score(self, *_args, **_kwargs): return None
    def span(self, *_args, **_kwargs): return _NullSpan()
    def generation(self, *_args, **_kwargs): return _NullSpan()
    @property
    def id(self) -> str: return ""
    @property
    def trace_id(self) -> str: return ""


_NULL = _NullSpan()


def start_session(session_id: str, *, user_id: str | None = None) -> None:
    """Mark a WebRTC session — no Langfuse object is created (sessions
    are implicit via session_id on traces), but we log for correlation."""
    if not enabled():
        return
    logger.info(f"[tracing] session start id={session_id!r} user={user_id!r}")


def start_turn_trace(
    *,
    session_id: str,
    name: str = "user_turn",
    input: Any = None,
    user_id: str | None = None,
    metadata: dict | None = None,
) -> Any:
    """Open a new trace for a single user turn. Returns a trace handle
    (or a NullSpan when disabled). Caller is responsible for calling
    `.update(output=…)` and `.end()` when the turn completes."""
    client = _lazy_client()
    if client is None:
        return _NULL
    try:
        return client.trace(
            name=name,
            session_id=session_id,
            user_id=user_id,
            input=input,
            metadata=metadata or {},
        )
    except Exception as e:
        logger.warning(f"[tracing] trace() failed: {e}")
        return _NULL


def continue_trace(
    *,
    trace_id: str,
    session_id: str,
) -> Any:
    """Re-attach to a trace started elsewhere — used when we receive a
    cross-fleet call with Langfuse-Trace-Id / Langfuse-Session-Id headers
    and want our spans to nest inside the caller's trace."""
    client = _lazy_client()
    if client is None:
        return _NULL
    try:
        return client.trace(id=trace_id, session_id=session_id)
    except Exception as e:
        logger.warning(f"[tracing] continue_trace() failed: {e}")
        return _NULL


# ---------------------------------------------------------------------------
# TurnTracer — pipeline observer owning the trace lifecycle
# ---------------------------------------------------------------------------

# Deferred imports so this module stays cheap when Langfuse is off.
def _frame_types():
    from pipecat.frames.frames import (
        BotStoppedSpeakingFrame,
        FunctionCallCancelFrame,
        FunctionCallInProgressFrame,
        FunctionCallResultFrame,
        LLMFullResponseEndFrame,
        LLMFullResponseStartFrame,
        TranscriptionFrame,
        UserStoppedSpeakingFrame,
    )
    return {
        "UserStoppedSpeakingFrame": UserStoppedSpeakingFrame,
        "BotStoppedSpeakingFrame": BotStoppedSpeakingFrame,
        "TranscriptionFrame": TranscriptionFrame,
        "LLMFullResponseStartFrame": LLMFullResponseStartFrame,
        "LLMFullResponseEndFrame": LLMFullResponseEndFrame,
        "FunctionCallInProgressFrame": FunctionCallInProgressFrame,
        "FunctionCallResultFrame": FunctionCallResultFrame,
        "FunctionCallCancelFrame": FunctionCallCancelFrame,
    }


class TurnTracer:
    """Owns the per-turn trace lifecycle.

    Used as a pipecat `BaseObserver`. Subclasses `BaseObserver` when
    Langfuse is enabled; otherwise a no-op shim. We dynamically base-class
    at import time to avoid paying the observer overhead when tracing is
    off.

    Turn model:
      - User stops speaking (UserStoppedSpeakingFrame) → open a new trace.
      - LLM response end (LLMFullResponseEndFrame) + bot speaking ends
        (BotStoppedSpeakingFrame) → close the trace.
      - Spans are added by other code via `get_current_trace()` — this
        observer only bounds them.
    """

    def __init__(self, session_id: str, user_id: str | None = None) -> None:
        self.session_id = session_id
        self.user_id = user_id
        self._current_trace: Any = None
        self._last_transcript: str | None = None
        self._llm_response_closed = False
        self._bot_stopped = False
        # Span handles — keyed so we can close them on matching frames.
        self._llm_span: Any = None
        self._tool_spans: dict[str, Any] = {}  # tool_call_id → span

    def get_current_trace(self) -> Any:
        """Other code pulls this to add spans under the active turn."""
        return self._current_trace or _NULL

    async def on_push_frame(self, data: Any) -> None:
        F = _frame_types()
        frame = data.frame

        # Capture the latest transcript so the trace's input field carries
        # the user's actual message, not a placeholder.
        if isinstance(frame, F["TranscriptionFrame"]) and getattr(frame, "text", None):
            self._last_transcript = frame.text

        if isinstance(frame, F["UserStoppedSpeakingFrame"]):
            if self._current_trace is None:
                self._current_trace = start_turn_trace(
                    session_id=self.session_id,
                    user_id=self.user_id,
                    input=self._last_transcript,
                    metadata={"trigger": "user_stopped_speaking"},
                )
                self._llm_response_closed = False
                self._bot_stopped = False

        elif isinstance(frame, F["LLMFullResponseStartFrame"]):
            if self._current_trace is not None and self._llm_span is None:
                try:
                    self._llm_span = self._current_trace.span(
                        name="llm.response",
                        input=self._last_transcript,
                    )
                except Exception as e:
                    logger.warning(f"[tracing] llm span open failed: {e}")

        elif isinstance(frame, F["LLMFullResponseEndFrame"]):
            if self._llm_span is not None:
                try:
                    self._llm_span.end()
                except Exception as e:
                    logger.warning(f"[tracing] llm span end failed: {e}")
                self._llm_span = None
            self._llm_response_closed = True
            self._maybe_close_trace()

        elif isinstance(frame, F["FunctionCallInProgressFrame"]):
            call_id = getattr(frame, "tool_call_id", None) or getattr(frame, "id", "")
            name = getattr(frame, "function_name", None) or getattr(frame, "name", "tool")
            if call_id and self._current_trace is not None:
                try:
                    self._tool_spans[call_id] = self._current_trace.span(
                        name=f"tool.{name}",
                        input={"name": name, "args_preview": _preview(getattr(frame, "arguments", None))},
                    )
                except Exception as e:
                    logger.warning(f"[tracing] tool span open failed: {e}")

        elif isinstance(frame, F["FunctionCallResultFrame"]):
            call_id = getattr(frame, "tool_call_id", None) or getattr(frame, "id", "")
            span = self._tool_spans.pop(call_id, None)
            if span is not None:
                try:
                    span.end(output=_preview(getattr(frame, "result", None)))
                except Exception as e:
                    logger.warning(f"[tracing] tool span end failed: {e}")

        elif isinstance(frame, F["FunctionCallCancelFrame"]):
            call_id = getattr(frame, "tool_call_id", None) or getattr(frame, "id", "")
            span = self._tool_spans.pop(call_id, None)
            if span is not None:
                try:
                    span.update(level="WARNING", status_message="cancelled")
                    span.end()
                except Exception as e:
                    logger.warning(f"[tracing] tool span cancel failed: {e}")

        elif isinstance(frame, F["BotStoppedSpeakingFrame"]):
            self._bot_stopped = True
            self._maybe_close_trace()

    def _maybe_close_trace(self) -> None:
        # End the trace only after BOTH the LLM response has closed and
        # the bot audio has finished playing. If either fires alone, we
        # may be mid-tool-call or mid-TTS.
        if not (self._llm_response_closed and self._bot_stopped):
            return
        if self._current_trace is None:
            return
        try:
            self._current_trace.end()
        except Exception as e:
            logger.warning(f"[tracing] trace.end() failed: {e}")
        # Clear any leftover tool spans — tool handler forgot to close.
        for sp in self._tool_spans.values():
            try: sp.end()
            except Exception: pass
        self._tool_spans.clear()
        self._current_trace = None
        self._llm_span = None
        self._llm_response_closed = False
        self._bot_stopped = False


def _preview(value: Any, max_len: int = 500) -> Any:
    """Abbreviate a value for span metadata so Langfuse payloads stay lean."""
    if value is None:
        return None
    try:
        s = str(value)
    except Exception:
        return "<unrenderable>"
    return s if len(s) <= max_len else s[:max_len] + "…"


def make_turn_tracer(session_id: str, user_id: str | None = None) -> Any:
    """Create a TurnTracer that's either a real pipecat BaseObserver (when
    tracing is on) or a no-op shim. Return type is duck-typed `BaseObserver`
    in both cases — app.py can pass it to `PipelineTask(observers=[…])`
    unconditionally."""
    if not enabled():
        # Minimal no-op observer: inherit from BaseObserver so the
        # PipelineTask accepts it, but do nothing.
        from pipecat.observers.base_observer import BaseObserver
        class _NoopTracer(BaseObserver):
            def get_current_trace(self):
                return _NULL
            async def on_push_frame(self, _data):
                return
        return _NoopTracer()
    from pipecat.observers.base_observer import BaseObserver
    # Dynamically compose so TurnTracer gets the Observer mixin.
    class _ActiveTracer(TurnTracer, BaseObserver):
        pass
    return _ActiveTracer(session_id=session_id, user_id=user_id)


# ---------------------------------------------------------------------------
# Cross-fleet trace propagation
# ---------------------------------------------------------------------------

def propagation_headers(
    *,
    trace: Any | None = None,
    parent_observation_id: str | None = None,
) -> dict[str, str]:
    """Return the Langfuse-* HTTP headers that carry the current trace
    across fleet boundaries. Empty dict if tracing is off or there's no
    live trace. See docs/reference/tracing-contract.md for the spec.
    """
    if not enabled() or trace is None:
        return {}
    trace_id = getattr(trace, "id", "") or getattr(trace, "trace_id", "")
    session_id = getattr(trace, "session_id", "") or ""
    if not (trace_id and session_id):
        return {}
    headers = {
        "Langfuse-Trace-Id": str(trace_id),
        "Langfuse-Session-Id": str(session_id),
    }
    if parent_observation_id:
        headers["Langfuse-Parent-Observation-Id"] = str(parent_observation_id)
    return headers


def flush() -> None:
    """Drain queued events before shutdown so nothing is lost. Safe to
    call when disabled."""
    client = _lazy_client()
    if client is None:
        return
    try:
        client.flush()
    except Exception as e:
        logger.warning(f"[tracing] flush failed: {e}")


# ---------------------------------------------------------------------------
# Active-tracer registry — per-user dict, so concurrent sessions don't
# clobber each other's trace attribution. Callers deep in the stack that
# don't have a user_id in scope read current_user_id from the ContextVar.
# ---------------------------------------------------------------------------

from auth.context import current_user_id

_ACTIVE_BY_USER: dict[str, Any] = {}


def set_active_tracer(tracer: Any, *, user_id: str | None = None) -> None:
    """Record (or clear) the live tracer for a user. Passing tracer=None
    clears the entry."""
    uid = user_id or current_user_id.get()
    if tracer is None:
        _ACTIVE_BY_USER.pop(uid, None)
    else:
        _ACTIVE_BY_USER[uid] = tracer


def active_tracer(user_id: str | None = None) -> Any:
    uid = user_id or current_user_id.get()
    return _ACTIVE_BY_USER.get(uid)


def active_trace(user_id: str | None = None) -> Any:
    """Shorthand for the current live turn trace (or _NULL if none)."""
    t = active_tracer(user_id=user_id)
    if t is None or not hasattr(t, "get_current_trace"):
        return _NULL
    return t.get_current_trace()


@contextmanager
def span(name: str, **span_kwargs: Any):
    """Context manager for a span on the currently active turn trace.

    Usage:

        with tracing.span("stt.whisper", input={"sr": 16000}) as sp:
            result = transcribe(audio)
            sp.update(output=_preview(result))

    Every span gets `user_id` + `session_id` stamped into its metadata,
    read from the ContextVars set by the voice-session / A2A entry
    points. Lets you filter Langfuse by user or session without having
    to thread ids through every call site.

    Yields a _NullSpan when tracing is off or no trace is live; all
    `.update()` / `.end()` calls no-op. Callers never need an
    `if enabled()` guard.
    """
    from auth.context import current_session_id
    metadata = dict(span_kwargs.pop("metadata", {}) or {})
    metadata.setdefault("user_id", current_user_id.get())
    sid = current_session_id.get()
    if sid:
        metadata.setdefault("session_id", sid)
    sp = active_trace().span(name=name, metadata=metadata, **span_kwargs)
    try:
        yield sp
    finally:
        try:
            sp.end()
        except Exception as e:
            logger.warning(f"[tracing] span.end('{name}') failed: {e}")
