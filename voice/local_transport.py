"""LocalAudioTransport — native desktop audio transport for ORBIS.

Connects to the Rust native audio engine via a Unix socket. The only voice
transport since DECISIONS.md amendment 2026-04-28 dropped the
WebRTC / browser path; src-tauri/src/audio/socket.rs is the other
half of the protocol.

Wire protocol (8-byte header, little-endian):
  [0..2]  u16  direction  0x0001=mic→python  0x0002=python→speaker  0x0010=control
  [2..4]  u16  sample_rate
  [4..6]  u16  channels
  [6..8]  u16  num_samples
  body:   num_samples × 2 bytes i16 LE (PCM) or control payload

The transport keeps Pipecat's event_handler / input() / output() shape so the
pipeline wiring stays conventional without restoring the removed WebRTC
runtime.
"""

import asyncio
import logging
import os
import struct

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    InputAudioRawFrame,
    OutputAudioRawFrame,
    StartFrame,
)
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.transports.base_transport import BaseTransport

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Wire protocol constants (must match src-tauri/src/audio/socket.rs)
# ---------------------------------------------------------------------------
DIR_MIC_TO_PYTHON: int = 0x0001
DIR_PYTHON_TO_SPEAKER: int = 0x0002
DIR_CONTROL: int = 0x0010

CTRL_BARGE_IN: int = 0x0001
CTRL_TTS_END: int = 0x0002
# Python → Rust: user said a cancel/dismiss phrase — close the listening window.
CTRL_STOP_LISTENING: int = 0x0003

HEADER_FMT = "<4H"   # 4 × u16 LE = 8 bytes
HEADER_LEN = struct.calcsize(HEADER_FMT)  # 8

# num_samples is a u16 in the wire header (max 65535). A single TTS
# utterance from Kokoro can exceed that, so output PCM is split into
# chunks under the cap. Mic input frames are tiny (320 samples) and
# never hit this.
_MAX_FRAME_SAMPLES = 32_000

TTS_SAMPLE_RATE = 24_000
MIC_SAMPLE_RATE = 16_000

# Software gain applied to incoming mic samples before they reach the
# pipeline. The legacy CPAL input path has no AGC, so it keeps the old
# boost. The macOS production path uses AVAudioEngine voice processing
# with Apple's AGC and defaults to unity gain to avoid clipping.
# Override via MIC_GAIN env var if a specific mic profile needs tuning.
_AUDIO_INPUT_MODE = os.environ.get("ORBIS_AUDIO_INPUT_MODE", "cpal").lower()


def _resolve_mic_gain(audio_input_mode: str, explicit_gain: str | None) -> float:
    if explicit_gain is not None:
        return float(explicit_gain)
    return 1.0 if audio_input_mode == "voice_processing" else 16.0


_MIC_GAIN = _resolve_mic_gain(_AUDIO_INPUT_MODE, os.environ.get("MIC_GAIN"))


def audio_runtime_info() -> dict[str, float | str]:
    """Runtime audio config surfaced by /healthz and validation scripts."""
    return {
        "input_mode": _AUDIO_INPUT_MODE,
        "mic_gain": _MIC_GAIN,
    }


def _apply_gain_i16(audio: bytes, gain: float) -> bytes:
    """Multiply i16 LE PCM samples by `gain`, clipping to int16 range.
    Vectorized; numpy is already loaded by the STT path."""
    import numpy as np
    arr = np.frombuffer(audio, dtype=np.int16)
    boosted = np.clip(arr.astype(np.int32) * gain, -32768, 32767).astype(np.int16)
    return boosted.tobytes()


def _encode_pcm_frame(samples_bytes: bytes, sample_rate: int) -> bytes:
    """Encode a PCM payload into the wire-protocol frame."""
    num_samples = len(samples_bytes) // 2
    header = struct.pack(HEADER_FMT, DIR_PYTHON_TO_SPEAKER, sample_rate, 1, num_samples)
    return header + samples_bytes


def _encode_control(code: int) -> bytes:
    """Encode a control frame (6 bytes body: code + 4 reserved bytes)."""
    body = struct.pack("<H4x", code)  # u16 + 4 zero bytes
    header = struct.pack(HEADER_FMT, DIR_CONTROL, 0, 0, len(body) // 2)
    return header + body


def _decode_header(raw: bytes) -> tuple[int, int, int, int]:
    """Unpack an 8-byte wire header → (direction, sample_rate, channels, num_samples)."""
    return struct.unpack(HEADER_FMT, raw)


# ---------------------------------------------------------------------------
# Input processor — reads from socket, emits InputAudioRawFrame
# ---------------------------------------------------------------------------

class LocalAudioInputTransport(FrameProcessor):
    """Reads mic PCM frames from the Rust socket and pushes them downstream."""

    def __init__(self, transport: "LocalAudioTransport", **kwargs):
        super().__init__(**kwargs)
        self._transport = transport

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        # StartFrame triggers the socket connection + reader loop.
        if isinstance(frame, StartFrame):
            await self.push_frame(frame, direction)
            await self._transport._connect()
        elif isinstance(frame, (EndFrame, CancelFrame)):
            await self._transport._disconnect()
            await self.push_frame(frame, direction)
        else:
            await self.push_frame(frame, direction)


# ---------------------------------------------------------------------------
# Output processor — receives OutputAudioRawFrame, writes to socket
# ---------------------------------------------------------------------------

class LocalAudioOutputTransport(FrameProcessor):
    """Receives TTS PCM frames and writes them to the Rust socket."""

    def __init__(self, transport: "LocalAudioTransport", **kwargs):
        super().__init__(**kwargs)
        self._transport = transport

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, OutputAudioRawFrame):
            await self._transport._send_pcm(frame.audio, frame.sample_rate)
        elif isinstance(frame, (EndFrame, CancelFrame)):
            # Signal TTS end to Rust before propagating.
            await self._transport._send_control(CTRL_TTS_END)
            await self.push_frame(frame, direction)
            return
        await self.push_frame(frame, direction)


# ---------------------------------------------------------------------------
# LocalAudioTransport — the public API
# ---------------------------------------------------------------------------

class LocalAudioTransport(BaseTransport):
    """Native desktop audio transport over Unix socket.

    Usage in app.py:

        transport = LocalAudioTransport(sock_path=os.environ["ORBIS_AUDIO_SOCK"])
        pipeline = Pipeline([transport.input(), ..., transport.output()])

    Events fired:
        on_client_connected(transport)
        on_client_disconnected(transport)
    """

    def __init__(self, sock_path: str, **kwargs):
        super().__init__(**kwargs)
        self._sock_path = sock_path
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task | None = None
        self._input_proc = LocalAudioInputTransport(self, name="LocalAudioInput")
        self._output_proc = LocalAudioOutputTransport(self, name="LocalAudioOutput")
        self._connected = False
        self._mic_frames_received = 0
        self._speaker_frames_sent = 0
        logger.info(
            "[local_transport] audio_input_mode=%s mic_gain=%.2f",
            _AUDIO_INPUT_MODE,
            _MIC_GAIN,
        )

        # Register the connection event names app.py listens for.
        self._register_event_handler("on_client_connected")
        self._register_event_handler("on_client_disconnected")

    # --- BaseTransport interface ---

    def input(self) -> FrameProcessor:
        return self._input_proc

    def output(self) -> FrameProcessor:
        return self._output_proc

    @property
    def connected(self) -> bool:
        """Whether the Python transport is currently connected to Rust."""
        return self._connected

    @property
    def mic_frames_received(self) -> int:
        """Mic frames received from Rust during this transport session."""
        return self._mic_frames_received

    @property
    def speaker_frames_sent(self) -> int:
        """Speaker frames successfully written to Rust during this session."""
        return self._speaker_frames_sent

    # --- Socket lifecycle ---

    async def _connect(self) -> None:
        """Open Unix socket connection to the Rust native audio engine."""
        if self._connected:
            return
        logger.info(f"[local_transport] connecting to {self._sock_path}")
        try:
            self._reader, self._writer = await asyncio.open_unix_connection(
                self._sock_path
            )
            self._connected = True
            logger.info("[local_transport] connected")
            self._reader_task = asyncio.create_task(
                self._reader_loop(), name="orbis-local-audio-reader"
            )
            await self._call_event_handler("on_client_connected", self)
        except Exception as e:
            logger.error(f"[local_transport] connect failed: {e}")
            await self._call_event_handler("on_client_disconnected", self)

    async def _disconnect(self) -> None:
        """Close the socket connection."""
        if not self._connected:
            return
        self._connected = False
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
        self._reader = None
        logger.info("[local_transport] disconnected")
        await self._call_event_handler("on_client_disconnected", self)

    # --- Reader loop ---

    async def _reader_loop(self) -> None:
        """Read PCM frames from the Rust socket and emit InputAudioRawFrame."""
        assert self._reader is not None
        try:
            while self._connected:
                raw_header = await self._reader.readexactly(HEADER_LEN)
                direction, sample_rate, channels, num_samples = _decode_header(raw_header)
                body_len = num_samples * 2
                body = await self._reader.readexactly(body_len) if body_len > 0 else b""

                if direction == DIR_MIC_TO_PYTHON:
                    # Mic audio from Rust CPAL — push into the pipeline.
                    self._mic_frames_received += 1
                    if self._mic_frames_received == 1:
                        logger.info(
                            "[local_transport] first mic frame: sample_rate=%s channels=%s samples=%s",
                            sample_rate,
                            channels,
                            num_samples,
                        )
                    if _MIC_GAIN != 1.0:
                        body = _apply_gain_i16(body, _MIC_GAIN)
                    frame = InputAudioRawFrame(
                        audio=body,
                        sample_rate=int(sample_rate),
                        num_channels=int(channels),
                    )
                    await self._input_proc.push_frame(frame)
                elif direction == DIR_CONTROL:
                    code = struct.unpack_from("<H", body)[0] if len(body) >= 2 else 0
                    logger.debug(f"[local_transport] control frame code=0x{code:04x}")
                    # Control frames from Rust are informational here.
                else:
                    logger.warning(
                        f"[local_transport] unexpected direction 0x{direction:04x} — skipping"
                    )
        except asyncio.IncompleteReadError:
            logger.info("[local_transport] socket closed by peer")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[local_transport] reader error: {e}")
        finally:
            if self._connected:
                await self._disconnect()

    # --- Write helpers ---

    async def _send_pcm(self, audio_bytes: bytes, sample_rate: int) -> None:
        """Write TTS PCM to the socket (direction=0x0002).

        Split into chunks whose sample count fits the u16 wire header
        (<= 65535). A single Kokoro utterance routinely exceeds that and
        would otherwise raise ``ushort format requires 0 <= number <=
        65535`` in struct.pack — dropping the whole frame and leaving the
        bot silent.
        """
        if not self._writer:
            return
        max_bytes = _MAX_FRAME_SAMPLES * 2  # 2 bytes per i16 sample
        try:
            for off in range(0, len(audio_bytes), max_bytes):
                chunk = audio_bytes[off:off + max_bytes]
                if not chunk:
                    continue
                self._writer.write(_encode_pcm_frame(chunk, sample_rate))
                await self._writer.drain()
                self._speaker_frames_sent += 1
                if self._speaker_frames_sent == 1:
                    logger.info(
                        "[local_transport] first speaker frame: sample_rate=%s samples=%s",
                        sample_rate,
                        len(chunk) // 2,
                    )
        except Exception as e:
            logger.error(f"[local_transport] write error: {e}")

    async def _send_control(self, code: int) -> None:
        """Write a control frame to the socket."""
        if not self._writer:
            return
        frame = _encode_control(code)
        try:
            self._writer.write(frame)
            await self._writer.drain()
        except Exception as e:
            logger.error(f"[local_transport] control write error: {e}")

    async def _send_control_nowait(self, code: int) -> None:
        """Write a control frame without awaiting drain — safe to call from
        an observer where blocking the pipeline event loop must be avoided.
        Best-effort: if the writer is gone or errors, silently returns."""
        if not self._writer:
            return
        frame = _encode_control(code)
        try:
            self._writer.write(frame)
            # Don't await drain — let the OS buffer absorb the small control frame.
        except Exception as e:
            logger.debug(f"[local_transport] control_nowait write error: {e}")
