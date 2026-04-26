"""Kokoro 82M TTS — in-process, low-latency, 54 preset voices, no cloning."""

import logging
import os
import time
from collections.abc import AsyncGenerator

import numpy as np
from pipecat.frames.frames import ErrorFrame, Frame, TTSAudioRawFrame
from pipecat.services.settings import TTSSettings
from pipecat.services.tts_service import TTSService

from agent.prosody import ProsodyTextFilter

logger = logging.getLogger(__name__)

KOKORO_VOICE = os.environ.get("KOKORO_VOICE", "af_heart")
KOKORO_LANG = os.environ.get("KOKORO_LANG", "a")
KOKORO_SR = 24000

_pipe = None


def _detect_kokoro_device() -> str | None:
    """Pick the best torch device for Kokoro. Returns ``None`` when no
    accelerator is available so KPipeline falls back to its own default
    (CPU). KPipeline accepts the standard torch device strings.

    Mirrors ``agent.hardware.detect_device`` preference order
    (cuda > mps > cpu) but doesn't raise on missing hardware — CI / tests
    install kokoro on CPU-only boxes and that's the correct path there.

    Honors the same ``ORBIS_ALLOW_CPU=1`` opt-in flag as
    ``agent.hardware.detect_device``: pytest + Docker CPU profile set it
    to force CPU even when MPS / CUDA appear available."""
    if os.environ.get("ORBIS_ALLOW_CPU") == "1":
        return None
    try:
        import torch  # noqa: PLC0415 — lazy so a missing torch doesn't 500 the import
    except ImportError:
        return None
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return None


KOKORO_DEVICE = _detect_kokoro_device()


def _get_pipe(lang: str = KOKORO_LANG):
    global _pipe
    if _pipe is None:
        from kokoro import KPipeline
        logger.info(f"Loading Kokoro (lang={lang}, device={KOKORO_DEVICE or 'cpu'})")
        t0 = time.time()
        # KPipeline accepts a torch device string. Passing None defers to
        # the library default (CPU); pass the detected accelerator
        # explicitly when we have one so KModel's weights land on it
        # instead of CPU.
        kpipe_kwargs: dict = {"lang_code": lang}
        if KOKORO_DEVICE is not None:
            kpipe_kwargs["device"] = KOKORO_DEVICE
        _pipe = KPipeline(**kpipe_kwargs)
        list(_pipe("Hello.", voice=KOKORO_VOICE, speed=1))
        logger.info(f"Kokoro ready in {time.time() - t0:.1f}s")
    return _pipe


class LocalKokoroTTS(TTSService):
    def __init__(
        self,
        *,
        voice: str = KOKORO_VOICE,
        lang: str = KOKORO_LANG,
        speed: float = 1.0,
        **kwargs,
    ):
        kwargs.setdefault(
            "settings",
            TTSSettings(model="kokoro-82m", voice=voice, language=None),
        )
        # Kokoro can't interpret Fish-style `[tags]` / SSML — filter them
        # out of the synthesis input so they aren't spoken as brackets.
        kwargs.setdefault("text_filters", [ProsodyTextFilter()])
        super().__init__(
            sample_rate=KOKORO_SR,
            push_stop_frames=True,
            **kwargs,
        )
        self._voice = voice
        self._lang = lang
        self._speed = speed

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        if not text.strip():
            return
        from agent import tracing
        tts_span = tracing.active_trace().start_observation(
            name="tts.kokoro",
            as_type="span",
            input={"text_len": len(text), "preview": text[:120]},
            metadata={"backend": "kokoro", "voice": self._voice},
        )
        try:
            await self.start_tts_usage_metrics(text)
            pipe = _get_pipe(self._lang)
            got_first = False
            for chunk in pipe(text, voice=self._voice, speed=self._speed):
                # KPipeline yields tuples; audio is at index 2 as a float32 ndarray.
                audio_f32 = chunk[2] if len(chunk) >= 3 else chunk
                if audio_f32 is None:
                    continue
                if not got_first:
                    await self.stop_ttfb_metrics()
                    got_first = True
                pcm16 = (
                    np.asarray(audio_f32, dtype=np.float32)
                    .clip(-1.0, 1.0)
                    * 32767
                ).astype(np.int16).tobytes()
                yield TTSAudioRawFrame(
                    audio=pcm16,
                    sample_rate=KOKORO_SR,
                    num_channels=1,
                    context_id=context_id,
                )
        except Exception as e:
            tts_span.update(level="ERROR", status_message=str(e))
            logger.exception("Kokoro synth failed")
            yield ErrorFrame(error=f"Kokoro TTS failed: {e}")
        finally:
            try: tts_span.end()
            except Exception: pass


def prewarm() -> None:
    _get_pipe()
