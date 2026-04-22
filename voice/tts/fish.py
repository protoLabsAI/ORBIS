"""Fish Audio S2-Pro TTS — sidecar service over HTTP.

Fish runs in its own container on GPU 1 with `--half --compile` for RTF 0.40
on Blackwell. First call triggers ~2min torch.compile warmup, so prewarm()
on startup is mandatory.

API (from tools/server/views.py):
  POST   /v1/tts                  ServeTTSRequest  — streaming supported
  GET    /v1/references/list
  POST   /v1/references/add
  DELETE /v1/references/delete

Quirk: when `streaming=true, format=wav` the server emits raw int16 LE PCM
bytes (no WAV header), even though it rejects other `format` values in
streaming mode. S2-Pro's decoder emits at 44100 Hz mono. We pass those
bytes straight through as PCM.

This client:
  - Uses streaming for low TTFA (pushes PCM chunks as bytes arrive).
  - Supports reference_id (saved voice) or inline `references=[...]` (clone).
"""

import base64
import logging
import os
import time
from collections.abc import AsyncGenerator

import httpx
from pipecat.frames.frames import ErrorFrame, Frame, TTSAudioRawFrame
from pipecat.services.settings import TTSSettings
from pipecat.services.tts_service import TTSService

logger = logging.getLogger(__name__)

FISH_URL = os.environ.get("FISH_URL", "http://fish-speech:8092")
FISH_REFERENCE_ID = os.environ.get("FISH_REFERENCE_ID", "") or None
FISH_TIMEOUT = float(os.environ.get("FISH_TIMEOUT", "180"))  # cold compile can be slow
FISH_SAMPLE_RATE = int(os.environ.get("FISH_SAMPLE_RATE", "44100"))


class FishAudioTTS(TTSService):
    def __init__(
        self,
        *,
        reference_id: str | None = FISH_REFERENCE_ID,
        fish_url: str = FISH_URL,
        sample_rate: int = FISH_SAMPLE_RATE,
        temperature: float = 0.8,
        top_p: float = 0.8,
        **kwargs,
    ):
        kwargs.setdefault(
            "settings",
            TTSSettings(
                model="fish-s2-pro",
                voice=reference_id or "default",
                language=None,
            ),
        )
        super().__init__(
            sample_rate=sample_rate,
            push_stop_frames=True,
            **kwargs,
        )
        self._reference_id = reference_id
        self._url = fish_url.rstrip("/")
        self._temperature = temperature
        self._top_p = top_p
        self._client = httpx.AsyncClient(timeout=FISH_TIMEOUT)

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        if not text.strip():
            return

        # Defer tracing import — this file is deep in the hot path.
        from agent import tracing
        tts_span = tracing.active_trace().span(
            name="tts.fish",
            input={"text_len": len(text), "preview": text[:120]},
            metadata={"backend": "fish", "voice": self._reference_id},
        )

        payload: dict = {
            "text": text,
            "format": "wav",
            "streaming": True,
            "temperature": self._temperature,
            "top_p": self._top_p,
            "chunk_length": 200,
            "normalize": True,
        }
        if self._reference_id:
            payload["reference_id"] = self._reference_id

        await self.start_tts_usage_metrics(text)
        got_first = False
        carry = b""  # carries an unpaired PCM byte into the next chunk
        try:
            async with self._client.stream(
                "POST", f"{self._url}/v1/tts", json=payload
            ) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes():
                    if not chunk:
                        continue
                    # Fish streams raw int16 PCM but the HTTP transport can
                    # split on any byte boundary. Soxr (downstream resampler)
                    # rejects odd-sized buffers with "must be a multiple of
                    # element size". Glue odd bytes to the next chunk so each
                    # emitted frame is int16-aligned.
                    buf = carry + chunk
                    if len(buf) & 1:
                        carry = buf[-1:]
                        buf = buf[:-1]
                    else:
                        carry = b""
                    if not buf:
                        continue
                    if not got_first:
                        await self.stop_ttfb_metrics()
                        got_first = True
                    yield TTSAudioRawFrame(
                        audio=buf,
                        sample_rate=FISH_SAMPLE_RATE,
                        num_channels=1,
                        context_id=context_id,
                    )
            # Flush any trailing odd byte — negligible (single sample) and
            # pairing with a zero keeps alignment without audible artifacts.
            if carry:
                yield TTSAudioRawFrame(
                    audio=carry + b"\x00",
                    sample_rate=FISH_SAMPLE_RATE,
                    num_channels=1,
                    context_id=context_id,
                )
        except httpx.HTTPError as e:
            tts_span.update(level="ERROR", status_message=f"http: {e}")
            logger.exception("Fish TTS HTTP error")
            yield ErrorFrame(error=f"Fish TTS HTTP error: {e}")
        except Exception as e:
            tts_span.update(level="ERROR", status_message=str(e))
            logger.exception("Fish TTS failed")
            yield ErrorFrame(error=f"Fish TTS failed: {e}")
        finally:
            try: tts_span.end()
            except Exception: pass

    async def stop(self, frame):
        await self._client.aclose()
        await super().stop(frame)


# ---------------------------------------------------------------------------
# Reference (voice clone) management — exposed for later UI + skills
# ---------------------------------------------------------------------------

# Fish uses content-negotiation via `kui` — without an explicit Accept header
# responses come back as MsgPack, not JSON. Force JSON for control-plane calls.
_JSON_HEADERS = {"Accept": "application/json"}


def list_references(fish_url: str = FISH_URL, timeout: float = 5.0) -> list[str]:
    try:
        r = httpx.get(
            f"{fish_url.rstrip('/')}/v1/references/list",
            headers=_JSON_HEADERS,
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json().get("reference_ids", [])
    except Exception as e:
        logger.warning(f"list_references failed: {e}")
        return []


def add_reference(
    ref_id: str,
    audio_bytes: bytes,
    transcript: str,
    *,
    fish_url: str = FISH_URL,
    timeout: float = 30.0,
) -> bool:
    """Save a new voice reference on the Fish server for later reuse."""
    try:
        r = httpx.post(
            f"{fish_url.rstrip('/')}/v1/references/add",
            json={
                "id": ref_id,
                "audio": base64.b64encode(audio_bytes).decode("ascii"),
                "text": transcript,
            },
            headers=_JSON_HEADERS,
            timeout=timeout,
        )
        r.raise_for_status()
        return bool(r.json().get("success"))
    except Exception as e:
        logger.warning(f"add_reference({ref_id}) failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Prewarm — CRITICAL. Without this the first real call is ~2 min on cold compile.
# ---------------------------------------------------------------------------

def prewarm(*, fish_url: str = FISH_URL, reference_id: str | None = FISH_REFERENCE_ID) -> None:
    t0 = time.time()
    logger.info(f"Fish prewarm → {fish_url} (may take ~2min on first boot)")
    payload: dict = {"text": "Hello.", "format": "wav", "streaming": False}
    if reference_id:
        payload["reference_id"] = reference_id
    try:
        r = httpx.post(
            f"{fish_url.rstrip('/')}/v1/tts",
            json=payload,
            timeout=FISH_TIMEOUT,
        )
        r.raise_for_status()
        logger.info(f"Fish warm in {time.time() - t0:.1f}s ({len(r.content)} bytes)")
    except Exception as e:
        logger.warning(f"Fish prewarm skipped: {e}")
