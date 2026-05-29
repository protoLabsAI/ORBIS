"""Langfuse tracing — singleton + session/trace helpers.

One Langfuse client per process. Each native voice session creates a
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

SDK notes (v4): the v2 client.trace() / trace.span() / trace.update()
surface was replaced in v3+ by an OpenTelemetry-style model. A "trace"
is now just the first span in a tree; child spans open via
span.start_observation(...). session_id / user_id are OTEL attributes
set at span-open time via the propagate_attributes() context manager.
Public helper signatures in this module stayed stable across the
migration — callers don't need to change.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

# Accept either LANGFUSE_HOST or LANGFUSE_BASE_URL — the SDK docs use
# HOST; some deployments (and older .env files) set BASE_URL instead.
_LANGFUSE_HOST: str = (
    os.environ.get("LANGFUSE_HOST", "").strip()
    or os.environ.get("LANGFUSE_BASE_URL", "").strip()
)
_ENABLED = bool(
    _LANGFUSE_HOST
    and os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip()
    and os.environ.get("LANGFUSE_SECRET_KEY", "").strip()
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
            host=_LANGFUSE_HOST,
        )
        logger.info(f"[tracing] langfuse client ready → {_LANGFUSE_HOST}")
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
    `with trace.start_as_current_observation(...)` / `span.update(...)` /
    `span.end(...)` without `if enabled` guards everywhere.

    Also carries the legacy v2 method names (`.span()` / `.generation()`)
    as aliases onto `.start_observation()`, so any caller that wasn't
    updated during the v4 migration still silently no-ops instead of
    raising AttributeError.
    """

    def __enter__(self): return self
    def __exit__(self, *_): return False
    def update(self, **_kwargs): return self
    def end(self, **_kwargs): return None
    def score(self, *_args, **_kwargs): return None
    def create_event(self, *_args, **_kwargs): return None
    def start_observation(self, *_args, **_kwargs): return _NullSpan()
    # Context-manager variant used by `tracing.span(...)`.
    @contextmanager
    def start_as_current_observation(self, *_args, **_kwargs):
        yield _NullSpan()
    # v2-compat aliases (kept so stragglers don't crash).
    def span(self, *args, **kwargs): return self.start_observation(*args, **kwargs)
    def generation(self, *args, **kwargs): return self.start_observation(*args, **kwargs)

    @property
    def id(self) -> str: return ""
    @property
    def trace_id(self) -> str: return ""
    @property
    def session_id(self) -> str: return ""


_NULL = _NullSpan()


def start_session(session_id: str, *, user_id: str | None = None) -> None:
    """Mark a native voice session — no Langfuse object is created (sessions
    are implicit via the session.id OTEL attribute on traces), but we log
    for correlation."""
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
    """Open a new trace for a single user turn. Returns a span handle
    (or a _NullSpan when disabled). Caller is responsible for calling
    `.update(output=…)` and `.end()` when the turn completes.

    In v4 the "trace" is just the root observation. session_id + user_id
    are set as OTEL trace-level attributes via propagate_attributes so
    they stick on this span and any children opened within the brief
    context.
    """
    client = _lazy_client()
    if client is None:
        return _NULL
    try:
        from langfuse import propagate_attributes  # type: ignore[import-not-found]
        with propagate_attributes(session_id=session_id, user_id=user_id):
            span = client.start_observation(
                name=name,
                as_type="span",
                input=input,
                metadata=metadata or {},
            )
        # Stash session_id/user_id on the handle so downstream helpers
        # (propagation_headers, delegate calls) can read them without
        # re-entering the OTEL context.
        try:
            setattr(span, "_orbis_session_id", session_id)
            setattr(span, "_orbis_user_id", user_id or "")
        except Exception:
            pass
        return span
    except Exception as e:
        logger.warning(f"[tracing] start_turn_trace failed: {e}")
        return _NULL


def continue_trace(
    *,
    trace_id: str,
    session_id: str,
    parent_span_id: str | None = None,
) -> Any:
    """Re-attach to a trace started elsewhere — used when we receive a
    cross-fleet call with Langfuse-Trace-Id / Langfuse-Session-Id headers
    and want our spans to nest inside the caller's trace.

    v4 uses a TraceContext to graft a new root-level span onto an
    existing trace tree. parent_span_id is optional but recommended when
    Langfuse-Parent-Observation-Id is present — it makes the inbound
    span a sibling of whatever the caller was doing, not a new root.
    """
    client = _lazy_client()
    if client is None:
        return _NULL
    try:
        from langfuse import propagate_attributes  # type: ignore[import-not-found]
        from langfuse.types import TraceContext  # type: ignore[import-not-found]
        tc_kwargs: dict[str, Any] = {"trace_id": trace_id}
        if parent_span_id:
            tc_kwargs["parent_span_id"] = parent_span_id
        tc = TraceContext(**tc_kwargs)
        with propagate_attributes(session_id=session_id):
            span = client.start_observation(
                name="a2a_inbound",
                as_type="span",
                trace_context=tc,
            )
        try:
            setattr(span, "_orbis_session_id", session_id)
        except Exception:
            pass
        return span
    except Exception as e:
        logger.warning(f"[tracing] continue_trace failed: {e}")
        return _NULL


# ---------------------------------------------------------------------------
# TurnTracer — pipeline observer owning the trace lifecycle
# ---------------------------------------------------------------------------

# Deferred imports so this module stays cheap when Langfuse is off.
def _frame_types():
    from pipecat.frames.frames import (
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
        FunctionCallCancelFrame,
        FunctionCallInProgressFrame,
        FunctionCallResultFrame,
        LLMFullResponseEndFrame,
        LLMFullResponseStartFrame,
        LLMTextFrame,
        TranscriptionFrame,
        UserStoppedSpeakingFrame,
    )
    return {
        "UserStoppedSpeakingFrame": UserStoppedSpeakingFrame,
        "BotStartedSpeakingFrame": BotStartedSpeakingFrame,
        "BotStoppedSpeakingFrame": BotStoppedSpeakingFrame,
        "TranscriptionFrame": TranscriptionFrame,
        "LLMFullResponseStartFrame": LLMFullResponseStartFrame,
        "LLMFullResponseEndFrame": LLMFullResponseEndFrame,
        "LLMTextFrame": LLMTextFrame,
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
      - User stops speaking (UserStoppedSpeakingFrame) → open trace + stt span.
      - TranscriptionFrame → close stt span (STT latency captured).
      - LLMFullResponseStartFrame → open llm.response generation span.
      - LLMTextFrame (first) → mark TTFT on llm span + open tts.synthesis span.
      - BotStartedSpeakingFrame → close tts.synthesis span + record total latency.
      - LLMFullResponseEndFrame + BotStoppedSpeakingFrame → close trace.
    """

    def __init__(
        self,
        session_id: str,
        user_id: str | None = None,
        *,
        llm_model: str | None = None,
        stt_backend: str | None = None,
        tts_backend: str | None = None,
    ) -> None:
        # Forward into BaseObserver's __init__ when composed as
        # _ActiveTracer(TurnTracer, BaseObserver). Without this the mixin
        # base never initializes (notably `_name`), and pipecat crashes
        # when it stringifies this observer in its proxy setup.
        super().__init__()
        self.session_id = session_id
        self.user_id = user_id
        self._llm_model = llm_model
        self._stt_backend = stt_backend
        self._tts_backend = tts_backend
        self._current_trace: Any = None
        self._last_transcript: str | None = None
        self._llm_response_closed = False
        self._bot_stopped = False
        # Span handles — keyed so we can close them on matching frames.
        self._stt_span: Any = None
        self._llm_span: Any = None
        self._tts_span: Any = None
        self._tool_spans: dict[str, Any] = {}  # tool_call_id → span
        # Streaming-visibility markers (first text / first audio of this
        # turn). Populated once per turn; cleared on close.
        self._llm_first_text_seen: bool = False
        self._tts_first_text_seen: bool = False
        self._bot_first_audio_seen: bool = False
        # Wall-clock turn start — used to compute end-to-end latency.
        self._turn_start: Any = None  # datetime | None

    def get_current_trace(self) -> Any:
        """Other code pulls this to add spans under the active turn."""
        return self._current_trace or _NULL

    async def on_push_frame(self, data: Any) -> None:
        from datetime import datetime, timezone
        F = _frame_types()
        frame = data.frame

        if isinstance(frame, F["UserStoppedSpeakingFrame"]):
            if self._current_trace is None:
                self._turn_start = datetime.now(timezone.utc)
                self._current_trace = start_turn_trace(
                    session_id=self.session_id,
                    user_id=self.user_id,
                    input=self._last_transcript,
                    metadata={
                        "trigger": "user_stopped_speaking",
                        "llm_model": self._llm_model,
                        "stt_backend": self._stt_backend,
                        "tts_backend": self._tts_backend,
                    },
                )
                self._llm_response_closed = False
                self._bot_stopped = False
                # STT span — measures from VAD stop to transcript available.
                try:
                    self._stt_span = self._current_trace.start_observation(
                        name="stt",
                        as_type="span",
                        metadata={"backend": self._stt_backend},
                    )
                except Exception as e:
                    logger.warning(f"[tracing] stt span open failed: {e}")

        elif isinstance(frame, F["TranscriptionFrame"]):
            # Capture transcript for the LLM span + trace input.
            text = getattr(frame, "text", None)
            if text:
                self._last_transcript = text
            # Close the STT span — duration = STT latency.
            if self._stt_span is not None:
                try:
                    self._stt_span.update(output={"transcript": text or ""})
                    self._stt_span.end()
                except Exception as e:
                    logger.warning(f"[tracing] stt span end failed: {e}")
                self._stt_span = None

        elif isinstance(frame, F["LLMFullResponseStartFrame"]):
            if self._current_trace is not None and self._llm_span is None:
                try:
                    self._llm_span = self._current_trace.start_observation(
                        name="llm.response",
                        as_type="generation",
                        input=self._last_transcript,
                        metadata={"model": self._llm_model},
                    )
                    self._llm_first_text_seen = False
                    self._tts_first_text_seen = False
                except Exception as e:
                    logger.warning(f"[tracing] llm span open failed: {e}")

        elif isinstance(frame, F["LLMTextFrame"]):
            text = getattr(frame, "text", "")
            if text:
                # First token → stamp TTFT on the generation span.
                if self._llm_span is not None and not self._llm_first_text_seen:
                    self._llm_first_text_seen = True
                    try:
                        self._llm_span.update(
                            completion_start_time=datetime.now(timezone.utc)
                        )
                    except Exception as e:
                        logger.warning(f"[tracing] llm first-token mark failed: {e}")
                # First token also opens the TTS span — measures from first
                # text available to first audio out. The sentence splitter
                # and TTS synthesis both fall inside this window.
                if self._current_trace is not None and not self._tts_first_text_seen:
                    self._tts_first_text_seen = True
                    try:
                        self._tts_span = self._current_trace.start_observation(
                            name="tts.synthesis",
                            as_type="span",
                            input={"first_token": text[:80]},
                            metadata={"backend": self._tts_backend},
                        )
                    except Exception as e:
                        logger.warning(f"[tracing] tts span open failed: {e}")

        elif isinstance(frame, F["BotStartedSpeakingFrame"]):
            if self._current_trace is not None and not self._bot_first_audio_seen:
                self._bot_first_audio_seen = True
                now = datetime.now(timezone.utc)
                # Close TTS span — duration = first-text-to-first-audio latency.
                if self._tts_span is not None:
                    try:
                        self._tts_span.end()
                    except Exception as e:
                        logger.warning(f"[tracing] tts span end failed: {e}")
                    self._tts_span = None
                # Record total end-to-end latency on the trace as an event
                # so it's visible at a glance without opening the span tree.
                total_ms = (
                    int((now - self._turn_start).total_seconds() * 1000)
                    if self._turn_start is not None
                    else None
                )
                try:
                    self._current_trace.create_event(
                        name="tts.first_audio",
                        metadata={
                            "at": now.isoformat(),
                            "total_turn_latency_ms": total_ms,
                        },
                    )
                except Exception as e:
                    logger.warning(f"[tracing] bot first-audio mark failed: {e}")

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
                    self._tool_spans[call_id] = self._current_trace.start_observation(
                        name=f"tool.{name}",
                        as_type="tool",
                        input={"name": name, "args_preview": _preview(getattr(frame, "arguments", None))},
                    )
                except Exception as e:
                    logger.warning(f"[tracing] tool span open failed: {e}")

        elif isinstance(frame, F["FunctionCallResultFrame"]):
            call_id = getattr(frame, "tool_call_id", None) or getattr(frame, "id", "")
            span = self._tool_spans.pop(call_id, None)
            if span is not None:
                try:
                    span.update(output=_preview(getattr(frame, "result", None)))
                    span.end()
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
        # Safety-net close for any spans that didn't close via their normal
        # frame path (e.g. barge-in, error, or out-of-order frames).
        for sp in [self._stt_span, self._tts_span, self._llm_span]:
            if sp is not None:
                try: sp.end()
                except Exception: pass
        for sp in self._tool_spans.values():
            try: sp.end()
            except Exception: pass
        self._tool_spans.clear()
        self._current_trace = None
        self._stt_span = None
        self._llm_span = None
        self._tts_span = None
        self._llm_response_closed = False
        self._bot_stopped = False
        self._llm_first_text_seen = False
        self._tts_first_text_seen = False
        self._bot_first_audio_seen = False
        self._turn_start = None


def _preview(value: Any, max_len: int = 500) -> Any:
    """Abbreviate a value for span metadata so Langfuse payloads stay lean."""
    if value is None:
        return None
    try:
        s = str(value)
    except Exception:
        return "<unrenderable>"
    return s if len(s) <= max_len else s[:max_len] + "…"


def make_turn_tracer(
    session_id: str,
    user_id: str | None = None,
    *,
    llm_model: str | None = None,
    stt_backend: str | None = None,
    tts_backend: str | None = None,
) -> Any:
    """Create a TurnTracer that's either a real pipecat BaseObserver (when
    tracing is on) or a no-op shim. Return type is duck-typed `BaseObserver`
    in both cases — app.py can pass it to `PipelineTask(observers=[…])`
    unconditionally."""
    if not enabled():
        from pipecat.observers.base_observer import BaseObserver
        class _NoopTracer(BaseObserver):
            def get_current_trace(self):
                return _NULL
            async def on_push_frame(self, _data):
                return
        return _NoopTracer()
    from pipecat.observers.base_observer import BaseObserver
    class _ActiveTracer(TurnTracer, BaseObserver):
        pass
    return _ActiveTracer(
        session_id=session_id,
        user_id=user_id,
        llm_model=llm_model,
        stt_backend=stt_backend,
        tts_backend=tts_backend,
    )


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

    Reads session_id off the ORBIS-stamped attribute we set at span
    creation — v4 spans don't expose session_id as a direct property.
    """
    if not enabled() or trace is None:
        return {}
    trace_id = getattr(trace, "trace_id", "") or getattr(trace, "id", "")
    session_id = (
        getattr(trace, "_orbis_session_id", "")
        or getattr(trace, "session_id", "")
        or ""
    )
    if not (trace_id and session_id):
        return {}
    headers = {
        "Langfuse-Trace-Id": str(trace_id),
        "Langfuse-Session-Id": str(session_id),
    }
    # Parent observation points at this span so the callee nests under us.
    obs_id = parent_observation_id or getattr(trace, "id", "")
    if obs_id:
        headers["Langfuse-Parent-Observation-Id"] = str(obs_id)
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

    v4 note: the `as_type` kwarg selects the observation type
    ('span' by default, 'generation' / 'tool' / etc. for specialized
    visualizations). Passes through to start_observation.
    """
    from auth.context import current_session_id
    metadata = dict(span_kwargs.pop("metadata", {}) or {})
    metadata.setdefault("user_id", current_user_id.get())
    sid = current_session_id.get()
    if sid:
        metadata.setdefault("session_id", sid)
    as_type = span_kwargs.pop("as_type", "span")
    trace = active_trace()
    try:
        sp = trace.start_observation(
            name=name, as_type=as_type, metadata=metadata, **span_kwargs
        )
    except Exception as e:
        logger.warning(f"[tracing] span.start('{name}') failed: {e}")
        sp = _NULL
    try:
        yield sp
    finally:
        try:
            sp.end()
        except Exception as e:
            logger.warning(f"[tracing] span.end('{name}') failed: {e}")
