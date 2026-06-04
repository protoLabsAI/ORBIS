"""NativeBargeInObserver — flushes the Rust CPAL playback ring on barge-in.

When the user interrupts the bot (or the pipeline is cancelled), Pipecat
broadcasts an InterruptionFrame and cancels the TTS task. Without this
observer the Rust CPAL playback ring still contains queued TTS audio and
will keep playing for up to a ring-buffer's worth of time — talking over
the user who just barged in.

This observer watches the pipeline for InterruptionFrame (a real barge-in)
and CancelFrame (pipeline teardown), then immediately sends control frame
0x0001 (CTRL_BARGE_IN) over the Unix socket so the Rust engine calls
flush_playback(). It deliberately does NOT flush on a normal
BotStoppedSpeakingFrame — that fires at the end of every turn, when the
ring is already draining naturally, so flushing there is the wrong signal.

Placement: added to the native desktop PipelineTask observers list.
"""

from __future__ import annotations

import logging
import weakref
from typing import TYPE_CHECKING

from pipecat.frames.frames import CancelFrame, InterruptionFrame
from pipecat.observers.base_observer import BaseObserver, FramePushed

if TYPE_CHECKING:
    from voice.local_transport import LocalAudioTransport

logger = logging.getLogger(__name__)


class NativeBargeInObserver(BaseObserver):
    """Pipeline observer that flushes the CPAL playback ring on interruption.

    Holds a weak reference to the LocalAudioTransport so it doesn't
    prevent GC if the transport is torn down before the observer.

    Reacts to:
      - InterruptionFrame — the user barged in; flush the ring so residual
        queued TTS stops immediately instead of talking over the user
      - CancelFrame — pipeline is being torn down; flush ring immediately
    """

    def __init__(self, transport: "LocalAudioTransport") -> None:
        super().__init__()
        # Weak ref so observer doesn't hold the transport alive.
        self._transport_ref: weakref.ref["LocalAudioTransport"] = weakref.ref(transport)

    async def on_push_frame(self, data: FramePushed) -> None:
        frame = data.frame
        if isinstance(frame, (InterruptionFrame, CancelFrame)):
            await self._flush(reason=type(frame).__name__)

    async def _flush(self, reason: str) -> None:
        transport = self._transport_ref()
        if transport is None:
            # Transport was GC'd — nothing to flush.
            return
        try:
            await transport._send_control_nowait(0x0001)  # CTRL_BARGE_IN
            logger.debug(f"[native_bargein] flushed CPAL ring (reason={reason})")
        except Exception as e:
            # Never raise from an observer — just log.
            logger.warning(f"[native_bargein] flush failed ({reason}): {e}")
