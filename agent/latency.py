"""LatencyObserver — surface the per-stage response-latency breakdown.

Answers the question the streaming-STT bench raised: where does the ~1s
first-audio latency actually go? Batch STT is ~40x real-time (not the
bottleneck), so the wait is dominated by LLM time-to-first-token and TTS
time-to-first-audio — but until now nothing logged it.

Pipecat emits ``TTFBMetricsData`` (time-to-first-byte) per service and, on
TTS services that support it (pipecat >=1.5), ``TTFAMetricsData`` (time-to-
first-AUDIO = ttfb + any leading silence the voice pads on). ``enable_metrics``
is already True on the PipelineTask; this observer just logs those under a
greppable ``[latency]`` tag instead of letting them fall on the floor.

Read-only — it never mutates or drops frames. Grep the sidecar log for
``[latency]`` to see, per turn, the STT / LLM / TTS split.
"""
from __future__ import annotations

import logging

from pipecat.frames.frames import ErrorFrame, MetricsFrame
from pipecat.metrics.metrics import TTFAMetricsData, TTFBMetricsData
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.services.llm_service import LLMService

from agent.llm_status import llm_status

logger = logging.getLogger(__name__)


class LatencyObserver(BaseObserver):
    """Log TTFB / TTFA metrics per processor. A MetricsFrame is pushed across
    several pipeline edges, so we dedupe by frame id to log each measurement
    once."""

    # Bound the dedupe set so a long-running session doesn't leak.
    _SEEN_CAP = 4096

    def __init__(self) -> None:
        super().__init__()
        self._seen: set[int] = set()

    async def on_push_frame(self, data: FramePushed) -> None:
        frame = data.frame
        if isinstance(frame, ErrorFrame):
            await self._on_error(frame)
            return
        if not isinstance(frame, MetricsFrame):
            return
        fid = getattr(frame, "id", id(frame))
        if fid in self._seen:
            return
        if len(self._seen) >= self._SEEN_CAP:
            self._seen.clear()
        self._seen.add(fid)

        for m in frame.data:
            # TTFA is a superset of TTFB (ttfb + leading silence) — log it
            # instead of the bare TTFB when present, so we don't double-count.
            if isinstance(m, TTFAMetricsData):
                logger.info(
                    "[latency] %s ttfa=%.3fs (ttfb=%.3fs silence=%.3fs)",
                    m.processor, m.ttfa, m.ttfb, m.leading_silence,
                )
            elif isinstance(m, TTFBMetricsData):
                logger.info("[latency] %s ttfb=%.3fs", m.processor, m.value)
                if "LLM" in str(m.processor).upper():
                    llm_status.record_ttfb(m.value)

    async def _on_error(self, frame: ErrorFrame) -> None:
        """Drop the open ttfb measurement when an LLM request fails.

        Pipecat arms the LLM ttfb clock before the HTTP call and only stops it
        on the first output token. A request that errors (e.g. a 400) never
        produces one, so the clock stays armed and the next output — possibly
        30s later, from a retry or failover — is reported as this request's
        ttfb. An errored request has no ttfb.
        """
        proc = frame.processor
        if not isinstance(proc, LLMService):
            return
        cancel = getattr(proc, "cancel_ttfb_metrics", None)
        if cancel is not None:
            try:
                await cancel()
            except Exception:  # noqa: BLE001 — observer must never break the pipeline
                logger.debug("[latency] cancel_ttfb_metrics failed", exc_info=True)
