"""Spoken-text logger — the single observability chokepoint for everything
ORBIS says aloud.

Every utterance the orb speaks passes through the TTS service, whatever
produced it:

  - streamed LLM narration (``LLMTextFrame``), and
  - out-of-band ``TTSSpeakFrame``s from fillers, opening acks, the
    ``DeliveryController`` (async tool results / inbox pushes), backchannels,
    and the stall watchdog.

Individually most of those paths already log, but there was no *guarantee*:
a new speech source could ship with no log trace at all. That is exactly how
a bug where the orb "spoke the tool call / result" stayed invisible in the
logs — the audio played but nothing recorded what was said. Per the rule
that there must be no path where the agent can talk without being logged,
this processor sits immediately BEFORE the TTS service and records the text
of every frame headed into synthesis, so the sidecar log is an authoritative
transcript of what the orb actually said.

Streamed LLM tokens are accumulated and logged as one line per response;
explicit ``TTSSpeakFrame``s (and any bare ``TextFrame``) are logged whole.
"""

from __future__ import annotations

import logging

from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
    TextFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

logger = logging.getLogger(__name__)


class SpokenTextLogger(FrameProcessor):
    """Log every utterance headed into TTS, whatever path emitted it.

    Place immediately BEFORE the TTS service in the pipeline so both streamed
    LLM narration and out-of-band ``TTSSpeakFrame``s pass through. Emits one
    INFO line per utterance under the ``[speak]`` tag — the authoritative
    record of what the orb actually said, closing the gap where out-of-band
    speech could play with no log trace.
    """

    def __init__(self) -> None:
        super().__init__()
        self._llm_buf: list[str] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        # Only text heading DOWNSTREAM reaches the synthesizer; upstream
        # frames (transcriptions, control) are not speech.
        if direction == FrameDirection.DOWNSTREAM:
            if isinstance(frame, TTSSpeakFrame):
                # Out-of-band: fillers, opening acks, DeliveryController
                # (async tool results / inbox), backchannel, stall recovery.
                text = (getattr(frame, "text", "") or "").strip()
                if text:
                    logger.info(f"[speak] out-of-band → {text!r}")
            elif isinstance(frame, LLMTextFrame):
                # Streamed LLM narration token — accumulate; flush at end.
                if frame.text:
                    self._llm_buf.append(frame.text)
            elif isinstance(frame, LLMFullResponseEndFrame):
                spoken = "".join(self._llm_buf).strip()
                self._llm_buf = []
                if spoken:
                    logger.info(f"[speak] llm → {spoken!r}")
            elif isinstance(frame, TextFrame):
                # Bare non-LLM text bound for TTS (rare). Catch-all so no
                # speech-bound text escapes the log.
                text = (frame.text or "").strip()
                if text:
                    logger.info(f"[speak] text → {text!r}")
        await self.push_frame(frame, direction)
