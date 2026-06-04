"""CancelGate — voice "stop listening" / dismiss.

Sits right after STT: when the user's transcript is *only* a cancel phrase
("cancel", "never mind", "stop listening"), it swallows the frame (so no LLM
turn starts) and tells the Rust audio engine to close the listening window
(``CTRL_STOP_LISTENING``). In wake-word mode the detector then re-arms back to
waiting for the phrase; in push-to-talk / open-mic the mic simply mutes.

A no-op for any normal utterance — only an *exact* cancel phrase triggers it, so
"stop the timer" / "cancel my 3pm" still reach the LLM untouched.
"""

from __future__ import annotations

import logging

from pipecat.frames.frames import Frame, InterruptionFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from voice.local_transport import CTRL_STOP_LISTENING

logger = logging.getLogger(__name__)

# Exact (normalized) phrases that dismiss the listening window. Deliberately
# tight — a request that merely *contains* "stop"/"cancel" still goes through.
_CANCEL_PHRASES = {
    "cancel",
    "never mind",
    "nevermind",
    "stop",
    "stop listening",
    "stop it",
    "forget it",
    "that's all",
    "thats all",
    "nothing",
    "dismiss",
    "go away",
    "be quiet",
    "quiet",
}


def _normalize(text: str) -> str:
    t = text.strip().lower().strip(".,!?;:\"' ")
    # Drop a leading/trailing address to the assistant ("cancel, orbis").
    for w in ("hey orbis", "orbis"):
        if t.endswith(w):
            t = t[: -len(w)].strip(" ,.")
        if t.startswith(w):
            t = t[len(w) :].strip(" ,.")
    return t


def is_cancel_phrase(text: str) -> bool:
    """True if ``text`` is exactly a dismiss phrase (not just contains one)."""
    return _normalize(text) in _CANCEL_PHRASES


class CancelGate(FrameProcessor):
    """Swallow a cancel utterance and close the listening window."""

    def __init__(self, transport, **kwargs):
        super().__init__(**kwargs)
        self._transport = transport

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if (
            isinstance(frame, TranscriptionFrame)
            and direction == FrameDirection.DOWNSTREAM
            and is_cancel_phrase(frame.text or "")
        ):
            logger.info(
                "[cancel-gate] %r → stop listening", (frame.text or "").strip()
            )
            try:
                await self._transport._send_control(CTRL_STOP_LISTENING)
            except Exception as e:  # noqa: BLE001
                logger.warning("[cancel-gate] send_control failed: %s", e)
            # Tell downstream turn-watchers (stall watchdog / micro-ack) the turn
            # was aborted, so they don't fire a "still working" filler into the
            # now-empty turn.
            await self.push_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
            return  # swallow the transcript — do not start an LLM turn

        await self.push_frame(frame, direction)
