"""MultiInputMixer — merge mic audio from two sources into one pipeline input.

The pipeline has a single input node (transport.input()) and a single VAD/STT
chain.  When both CPAL (desktop) and WebRTC (phone/PWA) sources are active we
need to pick the dominant source each 20 ms window and emit exactly one
InputAudioRawFrame per window so the downstream VAD never sees doubled audio.

Design
------
Two asyncio Queues feed this processor: ``native_queue`` (from LocalAudioInputTransport)
and ``webrtc_queue`` (from SmallWebRTCInputTransport, pushed in from a shim).
A background task drains both queues every WINDOW_MS milliseconds, computes RMS
energy for each candidate, and emits the louder one.  If only one source has
produced audio in the window, it passes straight through.

When a source is silent (queue empty for the window) it contributes 0 energy and
loses to any active source.

The processor does NOT sit inside the pipecat Pipeline object — it is a pure
asyncio helper that the lifespan / offer handler drives.  It exposes:

    push_native(frame)  — called by LocalAudioInputTransport callback
    push_webrtc(frame)  — called by SmallWebRTCInputTransport event handler
    output_queue        — asyncio.Queue read by a small bridge that calls
                          task.queue_frame() to inject the winner into the pipeline

Usage (pseudocode in lifespan / offer)
---------------------------------------
    mixer = MultiInputMixer()
    native_transport.on_audio = mixer.push_native
    # in offer():
    webrtc_transport.on_audio = mixer.push_webrtc
    asyncio.create_task(mixer.run(task.queue_frame))
"""

from __future__ import annotations

import asyncio
import logging
import struct
from typing import Awaitable, Callable

from pipecat.frames.frames import InputAudioRawFrame

logger = logging.getLogger(__name__)

WINDOW_MS = 20  # selection window in milliseconds


def _rms(pcm_bytes: bytes) -> float:
    """Return the RMS amplitude of 16-bit LE PCM bytes.  Returns 0 for empty."""
    if not pcm_bytes:
        return 0.0
    n = len(pcm_bytes) // 2
    if n == 0:
        return 0.0
    samples = struct.unpack_from(f"{n}h", pcm_bytes)
    return (sum(s * s for s in samples) / n) ** 0.5


class MultiInputMixer:
    """Energy-based mixer for two concurrent mic sources.

    Parameters
    ----------
    window_ms:
        Selection window in milliseconds.  Each window the highest-energy
        frame wins and is forwarded; the other is discarded.
    """

    def __init__(self, window_ms: int = WINDOW_MS):
        self._window_ms = window_ms
        self._native_q: asyncio.Queue[InputAudioRawFrame] = asyncio.Queue(maxsize=20)
        self._webrtc_q: asyncio.Queue[InputAudioRawFrame] = asyncio.Queue(maxsize=20)
        self._running = False
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Push entry points (called from transport callbacks)
    # ------------------------------------------------------------------

    def push_native(self, frame: InputAudioRawFrame) -> None:
        """Non-blocking push from LocalAudioInputTransport."""
        try:
            self._native_q.put_nowait(frame)
        except asyncio.QueueFull:
            pass  # drop oldest implicitly — queue is already full

    def push_webrtc(self, frame: InputAudioRawFrame) -> None:
        """Non-blocking push from WebRTC input shim."""
        try:
            self._webrtc_q.put_nowait(frame)
        except asyncio.QueueFull:
            pass

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, emit: Callable[[InputAudioRawFrame], Awaitable[None]]) -> asyncio.Task:
        """Start the mixer loop.

        Parameters
        ----------
        emit:
            Async callable that accepts an ``InputAudioRawFrame`` and injects it
            into the pipeline (typically ``task.queue_frame``).

        Returns the background asyncio.Task.
        """
        self._running = True
        self._task = asyncio.create_task(
            self._run(emit), name="orbis-mixer"
        )
        return self._task

    def stop(self) -> None:
        """Signal the mixer loop to exit."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    async def _run(self, emit: Callable[[InputAudioRawFrame], Awaitable[None]]) -> None:
        interval = self._window_ms / 1000.0
        logger.info("[mixer] started (window=%dms)", self._window_ms)
        try:
            while self._running:
                await asyncio.sleep(interval)

                native_frame = self._drain_queue(self._native_q)
                webrtc_frame = self._drain_queue(self._webrtc_q)

                winner = self._select(native_frame, webrtc_frame)
                if winner is not None:
                    await emit(winner)
        except asyncio.CancelledError:
            pass
        finally:
            logger.info("[mixer] stopped")

    @staticmethod
    def _drain_queue(
        q: asyncio.Queue[InputAudioRawFrame],
    ) -> InputAudioRawFrame | None:
        """Pull the most-recent frame from the queue (discard older ones)."""
        last: InputAudioRawFrame | None = None
        while True:
            try:
                last = q.get_nowait()
            except asyncio.QueueEmpty:
                break
        return last

    @staticmethod
    def _select(
        a: InputAudioRawFrame | None,
        b: InputAudioRawFrame | None,
    ) -> InputAudioRawFrame | None:
        """Return the frame with higher RMS energy, or the non-None one."""
        if a is None and b is None:
            return None
        if a is None:
            return b
        if b is None:
            return a
        return a if _rms(a.audio) >= _rms(b.audio) else b
