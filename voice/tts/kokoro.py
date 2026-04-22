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


def _get_pipe(lang: str = KOKORO_LANG):
    global _pipe
    if _pipe is None:
        from kokoro import KPipeline
        logger.info(f"Loading Kokoro (lang={lang})")
        t0 = time.time()
        _pipe = KPipeline(lang_code=lang)
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
        tts_span = tracing.active_trace().span(
            name="tts.kokoro",
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
