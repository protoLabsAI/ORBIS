"""Per-turn correlation + one-line turn summary.

A bad voice session used to be diagnosable only by hand-correlating timestamps
across ``[latency]``, ``[speak]``, ``[llm-error]`` lines. This module gives
every log line a ``turn`` id and emits ONE ``[turn]`` summary per turn::

    [turn] n=7 stt=0.98s(22ch) llm_ttfb=0.8s llm_total=2.1s tts_ttfa=0.4s
           first_audio=2.3s stop=end_turn out_chars=131 tools=[] model=m
           member=primary host=api.example.com

* ``TurnLogFilter`` stamps ``record.turn`` / ``record.session`` (``-`` when
  unknown) so the sidecar log format can carry them.
* ``TurnObserver`` mints ``turn=N`` on UserStartedSpeakingFrame and assembles
  the summary from frames the pipeline already emits. Read-only; unknown
  fields print ``-``. A turn id is "sticky": it stays on until the next user
  speech starts, so proactive speech between turns is attributed to the
  previous turn rather than to nothing.

``stop=`` is inferred (error / interrupted / tool_use / end_turn). When a
provider adapter knows the real finish reason it can override via
``note_stop_reason()``.
"""

from __future__ import annotations

import logging
import os
import time
from collections import deque
from typing import Callable

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    ErrorFrame,
    FunctionCallResultFrame,
    FunctionCallsStartedFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    MetricsFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.metrics.metrics import TTFAMetricsData, TTFBMetricsData
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.services.llm_service import LLMService

logger = logging.getLogger(__name__)

# --- log correlation ------------------------------------------------------

_current_turn: int | None = None
_current_session: str | None = None


def set_session_id(session_id: str | None) -> None:
    global _current_session
    _current_session = (session_id or "")[:8] or None


class TurnLogFilter(logging.Filter):
    """Inject ``turn`` and ``session`` into every record ('-' when unknown)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.turn = _current_turn if _current_turn is not None else "-"
        record.session = _current_session or "-"
        return True


def install_turn_log_filter() -> None:
    """Attach the filter to the root logger's handlers (idempotent). Filters on
    a *logger* don't apply to child loggers' records; handler filters do."""
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, TurnLogFilter) for f in handler.filters):
            handler.addFilter(TurnLogFilter())


# --- turn summary ---------------------------------------------------------


def _secs(v: float | None) -> str:
    return "-" if v is None else f"{v:.2f}s"


class _Turn:
    def __init__(self, n: int, started: float) -> None:
        self.n = n
        self.started = started
        self.user_stop: float | None = None
        self.stt_at: float | None = None
        self.stt_chars = 0
        self.llm_start: float | None = None
        self.llm_end: float | None = None
        self.llm_ttfb: float | None = None
        self.tts_ttfa: float | None = None
        self.llm_active = 0
        self.tools_pending = 0
        self.tools: list[str] = []
        self.out_chars = 0
        self.bot_start: float | None = None
        self.bot_speaking = False
        self.error_pending = False
        self.stop_override: str | None = None


class TurnObserver(BaseObserver):
    _SEEN_CAP = 256

    def __init__(
        self,
        *,
        session_id: str | None = None,
        brief: Callable[[], dict] | None = None,
        warn_first_audio_secs: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__()
        set_session_id(session_id)
        self._brief = brief
        self._warn = (
            warn_first_audio_secs
            if warn_first_audio_secs is not None
            else float(os.environ.get("TURN_FIRST_AUDIO_WARN_SECS", "3.0"))
        )
        self._clock = clock
        self._n = 0
        self._cur: _Turn | None = None
        self._seen: deque[int] = deque(maxlen=self._SEEN_CAP)

    def note_stop_reason(self, reason: str) -> None:
        if self._cur is not None:
            self._cur.stop_override = reason

    async def on_push_frame(self, data: FramePushed) -> None:
        frame = data.frame
        # Audio frames dominate the stream; bail before any bookkeeping.
        if not isinstance(frame, _HANDLED):
            return
        # Observers see a frame once per pipeline hop; handle each frame once.
        if frame.id in self._seen:
            return
        self._seen.append(frame.id)
        now = self._clock()
        t = self._cur

        if isinstance(frame, UserStartedSpeakingFrame):
            self._on_user_started(now)
        elif t is None:
            return
        elif isinstance(frame, UserStoppedSpeakingFrame):
            t.user_stop = now
        elif isinstance(frame, TranscriptionFrame):
            if t.stt_at is None:
                t.stt_at = now
            t.stt_chars += len(frame.text or "")
        elif isinstance(frame, LLMFullResponseStartFrame):
            t.llm_active += 1
            if t.llm_start is None:
                t.llm_start = now
        elif isinstance(frame, LLMFullResponseEndFrame):
            t.llm_active = max(t.llm_active - 1, 0)
            t.llm_end = now
        elif isinstance(frame, LLMTextFrame):
            t.out_chars += len(frame.text or "")
            t.error_pending = False
        elif isinstance(frame, FunctionCallsStartedFrame):
            t.error_pending = False
            for call in frame.function_calls:
                t.tools_pending += 1
                t.tools.append(str(getattr(call, "function_name", "?")))
        elif isinstance(frame, FunctionCallResultFrame):
            t.tools_pending = max(t.tools_pending - 1, 0)
        elif isinstance(frame, BotStartedSpeakingFrame):
            t.bot_speaking = True
            if t.bot_start is None:
                t.bot_start = now
        elif isinstance(frame, BotStoppedSpeakingFrame):
            t.bot_speaking = False
            if t.bot_start is not None and not t.llm_active and not t.tools_pending:
                self._finalize("")
        elif isinstance(frame, ErrorFrame):
            err_proc = frame.processor
            if (
                isinstance(err_proc, LLMService)
                and not frame.fatal
                and "executing function call" not in frame.error.lower()
            ):
                t.error_pending = True
        elif isinstance(frame, MetricsFrame):
            self._on_metrics(t, frame)

    def _on_user_started(self, now: float) -> None:
        t = self._cur
        if t is not None:
            # VAD flicker / continued speech before the bot reacted: same turn.
            if t.llm_start is None and t.bot_start is None:
                return
            self._finalize("interrupted" if (t.llm_active or t.bot_speaking) else "")
        global _current_turn
        self._n += 1
        _current_turn = self._n
        self._cur = _Turn(self._n, now)

    @staticmethod
    def _on_metrics(t: _Turn, frame: MetricsFrame) -> None:
        for m in frame.data:
            proc = str(getattr(m, "processor", "")).upper()
            if isinstance(m, TTFAMetricsData) and "TTS" in proc:
                t.tts_ttfa = m.ttfa
            elif isinstance(m, TTFBMetricsData):
                if "LLM" in proc and t.llm_ttfb is None:
                    t.llm_ttfb = m.value
                elif "TTS" in proc and t.tts_ttfa is None:
                    t.tts_ttfa = m.value

    def _finalize(self, forced_stop: str) -> None:
        t, self._cur = self._cur, None
        if t is None:
            return
        if forced_stop:
            stop = forced_stop
        elif t.stop_override:
            stop = t.stop_override
        elif t.error_pending:
            stop = "error"
        elif t.tools and not t.out_chars:
            stop = "tool_use"
        elif t.llm_start is not None:
            stop = "end_turn"
        else:
            stop = "-"
        if forced_stop == "interrupted" and t.error_pending:
            stop = "error"

        stt = (
            None if t.user_stop is None or t.stt_at is None else t.stt_at - t.user_stop
        )
        llm_total = (
            None
            if t.llm_start is None or t.llm_end is None
            else t.llm_end - t.llm_start
        )
        first_audio = (
            None
            if t.user_stop is None or t.bot_start is None
            else t.bot_start - t.user_stop
        )
        info = {}
        if self._brief is not None:
            try:
                info = self._brief() or {}
            except Exception:  # noqa: BLE001 — summary must never break the pipeline
                info = {}

        def _s(key: str) -> str:
            return str(info.get(key) or "-")

        line = (
            f"[turn] n={t.n} stt={_secs(stt)}({t.stt_chars}ch) "
            f"llm_ttfb={_secs(t.llm_ttfb)} llm_total={_secs(llm_total)} "
            f"tts_ttfa={_secs(t.tts_ttfa)} first_audio={_secs(first_audio)} "
            f"stop={stop} out_chars={t.out_chars} tools={t.tools} "
            f"model={_s('model')} member={_s('member')} host={_s('host')}"
        )
        if first_audio is not None and first_audio > self._warn:
            logger.warning("%s — first audio over %.1fs", line, self._warn)
        else:
            logger.info(line)


_HANDLED = (
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    TranscriptionFrame,
    LLMFullResponseStartFrame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
    FunctionCallsStartedFrame,
    FunctionCallResultFrame,
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    ErrorFrame,
    MetricsFrame,
)
