"""Tests for voice/native_bargein.py

Verifies that NativeBargeInObserver sends CTRL_BARGE_IN (0x0001) to the
LocalAudioTransport within 50 ms of BotStoppedSpeakingFrame or CancelFrame.
"""

import asyncio
import struct
import os
import tempfile
import time
import pytest

from pipecat.frames.frames import BotStoppedSpeakingFrame, CancelFrame
from pipecat.observers.base_observer import FramePushed

from voice.local_transport import (
    CTRL_BARGE_IN,
    DIR_CONTROL,
    HEADER_FMT,
    HEADER_LEN,
    LocalAudioTransport,
    _decode_header,
)
from voice.native_bargein import NativeBargeInObserver


def _make_frame_pushed(frame) -> FramePushed:
    from pipecat.processors.frame_processor import FrameDirection
    return FramePushed(
        source=None,
        destination=None,
        frame=frame,
        direction=FrameDirection.DOWNSTREAM,
        timestamp=time.time(),
    )


class _PipeServer:
    """Minimal Unix socket server that collects all received bytes.

    Uses a done_event to terminate the handler when the test is finished,
    avoiding the `server.wait_closed()` hang caused by long-lived handlers.
    """

    def __init__(self):
        self._received = bytearray()
        self._server = None
        self._done = asyncio.Event()

    async def start(self, path: str):
        self._server = await asyncio.start_unix_server(self._handle, path=path)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            while not self._done.is_set():
                try:
                    chunk = await asyncio.wait_for(reader.read(512), timeout=0.05)
                    if not chunk:
                        break
                    self._received.extend(chunk)
                except asyncio.TimeoutError:
                    continue
        finally:
            try:
                writer.close()
            except Exception:
                pass

    def stop(self):
        """Signal handler to exit, then close server (non-blocking)."""
        self._done.set()
        if self._server:
            self._server.close()

    def received_bytes(self) -> bytes:
        return bytes(self._received)


@pytest.mark.asyncio
async def test_barge_in_on_bot_stopped_speaking():
    """BotStoppedSpeakingFrame → CTRL_BARGE_IN written within 50ms."""
    with tempfile.TemporaryDirectory() as tmp:
        sock_path = os.path.join(tmp, "bargein-t1.sock")
        srv = _PipeServer()
        await srv.start(sock_path)

        transport = LocalAudioTransport(sock_path=sock_path)
        await transport._connect()
        await asyncio.sleep(0.03)

        observer = NativeBargeInObserver(transport)
        t0 = time.monotonic()
        await observer.on_push_frame(_make_frame_pushed(BotStoppedSpeakingFrame()))
        elapsed_ms = (time.monotonic() - t0) * 1000

        await asyncio.sleep(0.1)  # let OS flush
        await transport._disconnect()
        srv.stop()
        await asyncio.sleep(0.05)  # let handler exit

        data = srv.received_bytes()
        assert len(data) >= HEADER_LEN, f"no data received (got {len(data)} bytes)"
        direction, _, _, ns = _decode_header(data[:HEADER_LEN])
        body = data[HEADER_LEN: HEADER_LEN + ns * 2]
        code = struct.unpack_from("<H", body)[0] if len(body) >= 2 else -1

        assert direction == DIR_CONTROL, f"expected DIR_CONTROL got 0x{direction:04x}"
        assert code == CTRL_BARGE_IN, f"expected CTRL_BARGE_IN got 0x{code:04x}"
        assert elapsed_ms < 50, f"took {elapsed_ms:.1f}ms — expected < 50ms"


@pytest.mark.asyncio
async def test_barge_in_on_cancel_frame():
    """CancelFrame → CTRL_BARGE_IN written to socket."""
    with tempfile.TemporaryDirectory() as tmp:
        sock_path = os.path.join(tmp, "bargein-t2.sock")
        srv = _PipeServer()
        await srv.start(sock_path)

        transport = LocalAudioTransport(sock_path=sock_path)
        await transport._connect()
        await asyncio.sleep(0.03)

        observer = NativeBargeInObserver(transport)
        await observer.on_push_frame(_make_frame_pushed(CancelFrame()))
        await asyncio.sleep(0.1)
        await transport._disconnect()
        srv.stop()
        await asyncio.sleep(0.05)

        data = srv.received_bytes()
        assert len(data) >= HEADER_LEN
        direction, _, _, ns = _decode_header(data[:HEADER_LEN])
        body = data[HEADER_LEN: HEADER_LEN + ns * 2]
        code = struct.unpack_from("<H", body)[0] if len(body) >= 2 else -1

        assert direction == DIR_CONTROL
        assert code == CTRL_BARGE_IN


@pytest.mark.asyncio
async def test_noop_when_transport_disconnected():
    """Observer does not raise when transport writer is None."""
    transport = LocalAudioTransport(sock_path="/tmp/nonexistent-orbis.sock")
    observer = NativeBargeInObserver(transport)
    # Must not raise even though _writer is None.
    await observer.on_push_frame(_make_frame_pushed(BotStoppedSpeakingFrame()))


@pytest.mark.asyncio
async def test_noop_when_transport_gc_d():
    """Observer handles GC'd transport gracefully (weak ref returns None)."""
    transport = LocalAudioTransport(sock_path="/tmp/nonexistent-orbis2.sock")
    observer = NativeBargeInObserver(transport)
    del transport  # drop strong ref
    await observer.on_push_frame(_make_frame_pushed(BotStoppedSpeakingFrame()))


@pytest.mark.asyncio
async def test_unrelated_frame_is_ignored():
    """BotStartedSpeakingFrame does not trigger a flush write."""
    from pipecat.frames.frames import BotStartedSpeakingFrame

    with tempfile.TemporaryDirectory() as tmp:
        sock_path = os.path.join(tmp, "bargein-t5.sock")
        srv = _PipeServer()
        await srv.start(sock_path)

        transport = LocalAudioTransport(sock_path=sock_path)
        await transport._connect()
        await asyncio.sleep(0.03)

        observer = NativeBargeInObserver(transport)
        await observer.on_push_frame(_make_frame_pushed(BotStartedSpeakingFrame()))
        await asyncio.sleep(0.15)  # longer than any write delay

        await transport._disconnect()
        srv.stop()
        await asyncio.sleep(0.05)

        data = srv.received_bytes()
        assert len(data) == 0, (
            f"expected no writes for BotStartedSpeakingFrame, got {len(data)} bytes"
        )
