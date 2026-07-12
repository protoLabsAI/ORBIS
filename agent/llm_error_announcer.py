"""LLM error announcer (#576).

A dead LLM endpoint (bad API key -> 401, wrong URL -> connection refused,
DNS/timeout) used to leave the orb in **"thinking" forever, silently**: the
StallWatchdog disarms on ``LLMFullResponseStartFrame`` — which the service
pushes *before* the HTTP call — and the resulting ``ErrorFrame`` flows
**upstream** (``FrameProcessor.push_error``), where nothing re-arms or
reacts. Worst first-run failure now that every user wires their own LLM.

Why an observer and not a processor: ``LLMSwitcher`` re-propagates the
``ErrorFrame`` upstream *even when failover succeeds* ("so that
application-level error handlers can observe it" — pipecat
``service_switcher.py``), so a processor upstream of the LLM would announce
an outage the switcher already recovered from. An observer sees every frame
in both directions regardless of position (same family as SseBusObserver /
EchoGuardObserver), so it can instead wait out a short debounce for signs of
recovery.

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
    # LLMSwitcher failover does NOT retry the failed generation — it only
    # routes subsequent turns to the backup (pipecat
    # ServiceSwitcherStrategyFailover.handle_error switches and returns).
    # The erroring turn still dies unanswered and deserves an announcement,
    # but "check settings" is the wrong advice when a backup just took
    # over: the useful action is simply to ask again.
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

        The FIRST failover of an incident reclassifies a pending
        announcement to the "switched to backup — ask again" line (pipecat
        does not retry the failed generation, so the turn died either way).
        A SECOND failover inside the same window means the backup errored
        too — the member list wrapped, everything is down — so the streak
        keeps the original error-class line ("check settings")."""
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
            # A single failover since (just before) the debounce armed means
            # a live backup took over — small grace because the switcher and
            # the observer see the same ErrorFrame in the same instant. A
            # streak ≥ 2 means the backup died too: keep the class line.
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
