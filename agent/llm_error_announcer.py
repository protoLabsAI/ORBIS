"""LLM error announcer (#576).

A dead LLM endpoint (bad API key -> 401, wrong URL -> connection refused,
DNS/timeout) used to leave the orb in **"thinking" forever, silently**: the
StallWatchdog disarms on ``LLMFullResponseStartFrame`` — which the service
pushes *before* the HTTP call — and the resulting ``ErrorFrame`` flows
**upstream** (``FrameProcessor.push_error``), where nothing re-arms or
reacts. Worst first-run failure now that every user wires their own LLM.

Why an observer and not a processor: ``LLMSwitcher`` absorbs an error after a
successful switch, while an observer still sees the member LLM push it into
the switcher. That lets the announcer arm before failover and then wait out a
short debounce for signs of recovery.

Flow: on a non-fatal ``ErrorFrame`` whose ``.processor`` is an LLM service,
arm the debounce; cancel it on any sign of life (a new completion attempt,
LLM text, a tool call, bot audio — a retry or failover recovered; or the
user speaking — the next turn will re-error if the LLM is truly dead). If it
fires, speak ONE canned line classified auth / unreachable / generic via an
out-of-band ``TTSSpeakFrame`` — TTS only, no LLM round-trip, because the LLM
is exactly what's broken. A hard throttle keeps a flapping endpoint from
spamming. The spoken line also knocks the UI out of the stuck "thinking"
state through the normal bot-speaking SSE path.

The emit path is ``task.queue_frame``, wired by app.py post-task-creation
(same contract as DeliveryController / BackchannelController): observers
aren't pipeline nodes, so queueing at the task is the only safe injection.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Awaitable, Callable

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    ErrorFrame,
    Frame,
    FunctionCallsStartedFrame,
    InterruptionFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.services.llm_service import LLMService

logger = logging.getLogger(__name__)

# One user-actionable line per error class. Canned (no LLM call) and short —
# real words, Kokoro-safe; Fish gets the [softly] prefix at emit.
_LINES: dict[str, str] = {
    "auth": (
        "I can't authenticate with my language model — "
        "check the API key in settings."
    ),
    "unreachable": (
        "I can't reach my language model right now — "
        "check the endpoint in settings."
    ),
    "generic": (
        "My language model just hit an error — "
        "if this keeps happening, check the model settings."
    ),
    # Kept as a safety net if a switch succeeds but its same-turn retry never
    # produces output before the debounce expires.
    "failover": (
        "My main language model isn't responding, so I've switched to my "
        "backup — ask me that again."
    ),
}

_AUTH_MARKERS = (
    "401",
    "403",
    "unauthorized",
    "forbidden",
    "invalid_api_key",
    "invalid api key",
    "incorrect api key",
    "authentication",
    "permission denied",
)
_UNREACHABLE_MARKERS = (
    "connection",
    "connect",
    "refused",
    "timeout",
    "timed out",
    "name resolution",
    "getaddrinfo",
    "nodename",
    "unreachable",
    "dns",
)

# Signs the agent recovered (retry / failover produced work) or that the turn
# moved on — either way the pending announcement no longer describes reality.
_CANCEL_FRAMES = (
    LLMFullResponseStartFrame,
    LLMTextFrame,
    FunctionCallsStartedFrame,
    BotStartedSpeakingFrame,
    UserStartedSpeakingFrame,
    InterruptionFrame,
)


def classify_llm_error(error: str, exception: Exception | None = None) -> str:
    """Bucket an LLM ErrorFrame into auth / unreachable / generic.

    Prefers the exception (openai SDK types carry a status_code) and falls
    back to marker words in the error text — local endpoints (Ollama,
    LM Studio, MLX) surface plain connection strings, not SDK types.
    """
    status = getattr(exception, "status_code", None)
    if status in (401, 403):
        return "auth"
    if exception is not None:
        name = type(exception).__name__
        if name in ("AuthenticationError", "PermissionDeniedError"):
            return "auth"
        # APITimeoutError subclasses APIConnectionError in the openai SDK.
        if name in ("APIConnectionError", "APITimeoutError"):
            return "unreachable"
    text = error.lower()
    if any(m in text for m in _AUTH_MARKERS):
        return "auth"
    if any(m in text for m in _UNREACHABLE_MARKERS):
        return "unreachable"
    return "generic"


class LLMErrorAnnouncer(BaseObserver):
    """Speaks one classified, throttled line when the LLM errors with no recovery."""

    def __init__(
        self,
        *,
        debounce_secs: float = 2.5,
        throttle_secs: float = 20.0,
        enabled: bool = True,
        tts_backend: str = "kokoro",
    ) -> None:
        super().__init__()
        self._debounce_secs = debounce_secs
        self._throttle_secs = throttle_secs
        self._enabled = enabled
        self._tts_backend = tts_backend
        self._emit: Callable[[Frame], Awaitable[None]] | None = None
        self._timer: asyncio.Task | None = None
        self._pending_class: str | None = None
        self._last_spoke_at = float("-inf")
        self._last_failover_at = float("-inf")
        self._failover_streak = 0
        # The SAME ErrorFrame is observed once per upstream hop (transport →
        # … → LLM is many pushes); announce per frame, not per hop. Bounded so
        # a long session can't grow it.
        self._seen_ids: deque[int] = deque(maxlen=64)

    def set_emitter(self, emit: Callable[[Frame], Awaitable[None]]) -> None:
        """Wired by app.py post-construction (task.queue_frame)."""
        self._emit = emit

    def note_failover(self) -> None:
        """Called from app.py's on_service_switched handler.

        One switch reclassifies a pending announcement to the backup line.
        If that backup errors, ``_on_error`` advances the streak so the real
        error class wins instead; the strategy never wraps within an incident.
        """
        now = time.monotonic()
        if now - self._last_failover_at > self._debounce_secs + 5.0:
            self._failover_streak = 0
        self._failover_streak += 1
        self._last_failover_at = now

    async def on_push_frame(self, data: FramePushed) -> None:
        frame = data.frame
        if isinstance(frame, ErrorFrame):
            self._on_error(frame)
        elif isinstance(frame, _CANCEL_FRAMES):
            self._cancel()

    def _on_error(self, frame: ErrorFrame) -> None:
        if not self._enabled or frame.fatal:
            return
        if not isinstance(frame.processor, LLMService):
            return
        if frame.id in self._seen_ids:
            return
        self._seen_ids.append(frame.id)
        # Tool execution failed, not the LLM: the error result flows back into
        # the context and the (alive) LLM narrates the failure itself.
        if "executing function call" in frame.error.lower():
            return
        # A distinct LLM error after a recent switch means the attempted
        # backup also failed. Do not claim that failover recovered the incident.
        if (
            self._failover_streak == 1
            and time.monotonic() - self._last_failover_at < self._debounce_secs + 5.0
        ):
            self._failover_streak = 2
        error_class = classify_llm_error(frame.error, frame.exception)
        logger.warning(
            f"[llm-error] {error_class}: {frame.error!r} — "
            f"announcing in {self._debounce_secs}s unless output follows"
        )
        self._pending_class = error_class
        self._cancel()
        self._timer = asyncio.create_task(self._fire_after_delay())

    def _cancel(self) -> None:
        if self._timer and not self._timer.done():
            self._timer.cancel()
        self._timer = None

    async def _fire_after_delay(self) -> None:
        try:
            await asyncio.sleep(self._debounce_secs)
            if self._emit is None:
                return
            now = time.monotonic()
            if now - self._last_spoke_at < self._throttle_secs:
                logger.info("[llm-error] throttled — spoke recently")
                return
            self._last_spoke_at = now
            error_class = self._pending_class or "generic"
            # One recent failover means the retry has not produced output yet.
            # A streak ≥ 2 means the attempted backup also errored.
            if (
                now - self._last_failover_at < self._debounce_secs + 5.0
                and self._failover_streak == 1
            ):
                error_class = "failover"
            line = _LINES[error_class]
            if self._tts_backend == "fish":
                line = f"[softly] {line}"
            logger.warning(f"[llm-error] announcing: {line!r}")
            await self._emit(TTSSpeakFrame(line, append_to_context=False))
        except asyncio.CancelledError:
            pass
