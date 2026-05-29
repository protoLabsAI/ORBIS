"""Tests for voice/local_transport.py

Uses a real Unix socket pair (socketpair) to verify:
- 10 synthetic mic frames received → 10 InputAudioRawFrame objects emitted
- TTS OutputAudioRawFrame → wire-encoded correctly (direction=0x0002)
- encode/decode roundtrip for control frames
- socket_path and wire protocol helpers
"""

import asyncio
import os
import struct
import tempfile
import pytest

from voice.local_transport import (
    CTRL_BARGE_IN,
    CTRL_TTS_END,
    DIR_CONTROL,
    DIR_MIC_TO_PYTHON,
    DIR_PYTHON_TO_SPEAKER,
    HEADER_FMT,
    HEADER_LEN,
    MIC_SAMPLE_RATE,
    TTS_SAMPLE_RATE,
    LocalAudioTransport,
    _decode_header,
    _encode_control,
    _encode_pcm_frame,
    _resolve_mic_gain,
    audio_runtime_info,
)


# ---------------------------------------------------------------------------
# Wire protocol unit tests (no I/O needed)
# ---------------------------------------------------------------------------

def test_encode_decode_pcm_roundtrip():
    samples_i16 = [100, -200, 300, -400, 32767, -32768]
    audio_bytes = struct.pack(f"<{len(samples_i16)}h", *samples_i16)
    encoded = _encode_pcm_frame(audio_bytes, MIC_SAMPLE_RATE)

    assert len(encoded) == HEADER_LEN + len(audio_bytes)

    direction, sr, ch, ns = _decode_header(encoded[:HEADER_LEN])
    assert direction == DIR_PYTHON_TO_SPEAKER
    assert sr == MIC_SAMPLE_RATE
    assert ch == 1
    assert ns == len(samples_i16)

    decoded = struct.unpack_from(f"<{ns}h", encoded, HEADER_LEN)
    assert list(decoded) == samples_i16


def test_encode_control_barge_in():
    encoded = _encode_control(CTRL_BARGE_IN)
    direction, _, _, _ = _decode_header(encoded[:HEADER_LEN])
    assert direction == DIR_CONTROL
    code = struct.unpack_from("<H", encoded, HEADER_LEN)[0]
    assert code == CTRL_BARGE_IN


def test_encode_control_tts_end():
    encoded = _encode_control(CTRL_TTS_END)
    code = struct.unpack_from("<H", encoded, HEADER_LEN)[0]
    assert code == CTRL_TTS_END


def test_decode_header_little_endian():
    raw = struct.pack("<4H", 0x0001, 16000, 1, 320)
    direction, sr, ch, ns = _decode_header(raw)
    assert direction == DIR_MIC_TO_PYTHON
    assert sr == 16000
    assert ch == 1
    assert ns == 320


def test_voice_processing_defaults_to_unity_gain():
    assert _resolve_mic_gain("voice_processing", None) == 1.0


def test_cpal_defaults_to_legacy_gain():
    assert _resolve_mic_gain("cpal", None) == 16.0


def test_explicit_mic_gain_overrides_input_mode():
    assert _resolve_mic_gain("voice_processing", "4.0") == 4.0


def test_audio_runtime_info_exposes_input_mode_and_gain():
    info = audio_runtime_info()
    assert info["input_mode"] in {"cpal", "voice_processing"}
    assert isinstance(info["mic_gain"], float)


def test_transport_connected_property_defaults_false():
    transport = LocalAudioTransport(sock_path="/tmp/orbis-audio-test.sock")
    assert transport.connected is False


@pytest.mark.asyncio
async def test_send_pcm_counts_speaker_frames():
    class _Writer:
        def __init__(self):
            self.data = bytearray()

        def write(self, data: bytes):
            self.data.extend(data)

        async def drain(self):
            return None

    writer = _Writer()
    transport = LocalAudioTransport(sock_path="/tmp/orbis-audio-test.sock")
    transport._writer = writer

    samples = struct.pack("<4h", 100, -100, 200, -200)
    await transport._send_pcm(samples, TTS_SAMPLE_RATE)

    assert transport.speaker_frames_sent == 1
    direction, sample_rate, channels, num_samples = _decode_header(
        bytes(writer.data[:HEADER_LEN])
    )
    assert direction == DIR_PYTHON_TO_SPEAKER
    assert sample_rate == TTS_SAMPLE_RATE
    assert channels == 1
    assert num_samples == 4


# ---------------------------------------------------------------------------
# Integration test: mock socket sends 10 mic frames → 10 InputAudioRawFrame
# ---------------------------------------------------------------------------

def _make_mic_frame(n_samples: int = 320) -> bytes:
    """Build a valid wire-protocol mic frame with sine-ish samples."""
    samples = [int(32000 * (i % 2 == 0) - 16000) for i in range(n_samples)]
    body = struct.pack(f"<{n_samples}h", *samples)
    header = struct.pack(HEADER_FMT, DIR_MIC_TO_PYTHON, MIC_SAMPLE_RATE, 1, n_samples)
    return header + body


@pytest.mark.asyncio
async def test_10_mic_frames_produce_10_input_audio_frames():
    """Send 10 synthetic mic frames over a real Unix socket pair,
    assert LocalAudioTransport emits 10 InputAudioRawFrame objects."""
    from pipecat.frames.frames import InputAudioRawFrame

    received: list[InputAudioRawFrame] = []
    connected = asyncio.Event()
    disconnected = asyncio.Event()

    with tempfile.TemporaryDirectory() as tmp:
        sock_path = os.path.join(tmp, "orbis-audio-test.sock")

        # --- Server side (mimics Rust CPAL engine) ---
        server = await asyncio.start_unix_server(
            lambda r, w: asyncio.ensure_future(_server_handler(r, w)),
            path=sock_path,
        )

        async def _server_handler(reader, writer):
            for _ in range(10):
                writer.write(_make_mic_frame())
            await writer.drain()
            # Keep connection open briefly so the reader loop processes all frames.
            await asyncio.sleep(0.2)
            writer.close()

        transport = LocalAudioTransport(sock_path=sock_path)

        # Capture events.
        @transport.event_handler("on_client_connected")
        async def _on_connect(_t, _c=None):
            connected.set()

        @transport.event_handler("on_client_disconnected")
        async def _on_disconnect(_t, _c=None):
            disconnected.set()

        # Monkey-patch the input processor's push_frame to capture frames.
        original_push = transport._input_proc.push_frame
        async def _capture(frame, direction=None):
            if isinstance(frame, InputAudioRawFrame):
                received.append(frame)
            if original_push and direction is not None:
                await original_push(frame, direction)
        transport._input_proc.push_frame = _capture

        # Trigger connection (normally triggered by StartFrame in the pipeline).
        await transport._connect()

        # Wait for disconnect (server closes connection after sending frames).
        await asyncio.wait_for(disconnected.wait(), timeout=3.0)
        server.close()
        await server.wait_closed()

    assert len(received) == 10, f"expected 10 frames, got {len(received)}"
    assert transport.mic_frames_received == 10
    for frame in received:
        assert isinstance(frame, InputAudioRawFrame)
        assert frame.sample_rate == MIC_SAMPLE_RATE
        assert frame.num_channels == 1
        assert len(frame.audio) == 320 * 2  # 320 samples × 2 bytes


@pytest.mark.asyncio
async def test_on_client_connected_fires():
    """on_client_connected event fires when socket is accepted."""
    connected = asyncio.Event()

    with tempfile.TemporaryDirectory() as tmp:
        sock_path = os.path.join(tmp, "orbis-audio-test2.sock")
        server = await asyncio.start_unix_server(
            lambda r, w: asyncio.ensure_future(_noop_handler(r, w)),
            path=sock_path,
        )

        async def _noop_handler(reader, writer):
            await asyncio.sleep(0.1)
            writer.close()

        transport = LocalAudioTransport(sock_path=sock_path)

        @transport.event_handler("on_client_connected")
        async def _on_connect(_t, _c=None):
            connected.set()

        await transport._connect()
        await asyncio.wait_for(connected.wait(), timeout=2.0)
        await transport._disconnect()
        server.close()
        await server.wait_closed()

    assert connected.is_set()


@pytest.mark.asyncio
async def test_on_client_disconnected_fires():
    """on_client_disconnected fires when server closes the connection."""
    disconnected = asyncio.Event()

    with tempfile.TemporaryDirectory() as tmp:
        sock_path = os.path.join(tmp, "orbis-audio-test3.sock")
        server = await asyncio.start_unix_server(
            lambda r, w: asyncio.ensure_future(_close_immediately(r, w)),
            path=sock_path,
        )

        async def _close_immediately(reader, writer):
            writer.close()

        transport = LocalAudioTransport(sock_path=sock_path)

        @transport.event_handler("on_client_disconnected")
        async def _on_disconnect(_t, _c=None):
            disconnected.set()

        await transport._connect()
        await asyncio.wait_for(disconnected.wait(), timeout=2.0)
        server.close()
        await server.wait_closed()

    assert disconnected.is_set()
