"""TeeOutputProcessor — fan one stream of TTS audio to N sinks.

Each sink is an object that exposes ``write_audio_frame(OutputAudioRawFrame)``
(the interface implemented by SmallWebRTCOutputTransport and LocalAudioOutputSink).
The processor sits in the pipeline **instead of** a single transport.output() node;
the CPAL transport is registered as a permanent sink at construction time, and
WebRTC transports come and go as clients connect / disconnect.

Frame flow
----------
OutputAudioRawFrame   → write_audio_frame() on every registered sink, then
                        pushed downstream so the rest of the pipeline
                        (ProsodyTagStripper, assistant_agg) still sees it.
EndFrame / CancelFrame → frame propagates downstream; sinks are not called
                         (each sink manages its own lifecycle).
All other frames       → pass through unchanged.

Public helpers
--------------
LocalAudioOutputSink  — wraps a LocalAudioTransport so it satisfies AudioSink.
WebRTCOutputSink      — wraps a SmallWebRTCOutputTransport; calls write_audio_frame
                        directly (the transport must have been started externally).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Protocol, Sequence

from pipecat.frames.frames import CancelFrame, EndFrame, OutputAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

if TYPE_CHECKING:
    from voice.local_transport import LocalAudioTransport

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sink protocol
# ---------------------------------------------------------------------------

class AudioSink(Protocol):
    """Structural protocol satisfied by any object that can receive PCM audio."""

    async def write_audio_frame(self, frame: OutputAudioRawFrame) -> bool: ...


# ---------------------------------------------------------------------------
# Concrete sink adapters
# ---------------------------------------------------------------------------

class LocalAudioOutputSink:
    """Adapts LocalAudioTransport._send_pcm to the AudioSink protocol."""

    def __init__(self, transport: "LocalAudioTransport"):
        self._transport = transport

    async def write_audio_frame(self, frame: OutputAudioRawFrame) -> bool:
        await self._transport._send_pcm(frame.audio, frame.sample_rate)
        return True

    def __repr__(self) -> str:
        return f"LocalAudioOutputSink({self._transport!r})"


class WebRTCOutputSink:
    """Adapts SmallWebRTCOutputTransport to the AudioSink protocol.

    ``write_audio_frame`` is called directly on the transport — this is safe
    because SmallWebRTCOutputTransport.write_audio_frame delegates straight to
    the underlying WebRTC client without needing the pipecat frame queue.
    """

    def __init__(self, output_transport):  # SmallWebRTCOutputTransport
        self._out = output_transport

    async def write_audio_frame(self, frame: OutputAudioRawFrame) -> bool:
        return await self._out.write_audio_frame(frame)

    def __repr__(self) -> str:
        return f"WebRTCOutputSink({self._out!r})"


# ---------------------------------------------------------------------------
# TeeOutputProcessor
# ---------------------------------------------------------------------------

class TeeOutputProcessor(FrameProcessor):
    """Pipeline node that fans each OutputAudioRawFrame to all registered sinks.

    Parameters
    ----------
    sinks:
        Initial list of sinks (e.g. the CPAL LocalAudioOutputSink).  Additional
        sinks can be added dynamically with ``add_sink()`` / ``remove_sink()``.
    """

    def __init__(self, sinks: Sequence[AudioSink] | None = None, **kwargs):
        super().__init__(**kwargs)
        # Lock guards the list so add/remove from connection callbacks is safe.
        self._lock = asyncio.Lock()
        self._sinks: list[AudioSink] = list(sinks) if sinks else []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def add_sink(self, sink: AudioSink) -> None:
        """Register a new sink to receive subsequent audio frames."""
        async with self._lock:
            if sink not in self._sinks:
                self._sinks.append(sink)
                logger.debug("[tee] sink added: %s (total=%d)", sink, len(self._sinks))

    async def remove_sink(self, sink: AudioSink) -> None:
        """Deregister a sink.  No-op if the sink is not registered."""
        async with self._lock:
            try:
                self._sinks.remove(sink)
                logger.debug("[tee] sink removed: %s (total=%d)", sink, len(self._sinks))
            except ValueError:
                pass

    async def sink_count(self) -> int:
        async with self._lock:
            return len(self._sinks)

    # ------------------------------------------------------------------
    # Frame processing
    # ------------------------------------------------------------------

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        if isinstance(frame, OutputAudioRawFrame):
            await self._fan_audio(frame)
            # Still push downstream so ProsodyTagStripper / assistant_agg see it.
            await self.push_frame(frame, direction)

        elif isinstance(frame, (EndFrame, CancelFrame)):
            # Propagate so downstream processors start shutting down.
            # Each sink manages its own lifecycle independently.
            await self.push_frame(frame, direction)

        else:
            await self.push_frame(frame, direction)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _fan_audio(self, frame: OutputAudioRawFrame) -> None:
        """Deliver the audio frame to every registered sink concurrently."""
        async with self._lock:
            sinks = list(self._sinks)

        if not sinks:
            return

        results = await asyncio.gather(
            *(sink.write_audio_frame(frame) for sink in sinks),
            return_exceptions=True,
        )
        for sink, result in zip(sinks, results):
            if isinstance(result, Exception):
                logger.warning("[tee] sink %s write_audio_frame failed: %s", sink, result)
