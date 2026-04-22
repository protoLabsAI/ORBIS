"""TTS backend selection.

Three backends:
  - fish    : Fish Audio S2-Pro sidecar — voice cloning, prosody tags
  - kokoro  : Local Kokoro 82M, in-process — preset voices, low-latency
  - openai  : Any OpenAI-compatible /v1/audio/speech endpoint (LocalAI,
              OpenRouter, vllm-omni, OpenAI itself)

Choose via env: TTS_BACKEND={fish|kokoro|openai}
"""

import logging
import os

from pipecat.services.tts_service import TTSService

logger = logging.getLogger(__name__)

TTS_BACKEND = os.environ.get("TTS_BACKEND", "fish").lower()


def make_tts(**overrides) -> TTSService:
    backend = overrides.pop("backend", TTS_BACKEND)
    if backend == "kokoro":
        from .kokoro import LocalKokoroTTS
        return LocalKokoroTTS(**overrides)
    if backend == "fish":
        from .fish import FishAudioTTS
        return FishAudioTTS(**overrides)
    if backend == "openai":
        from . import openai as openai_tts
        return openai_tts.make(**overrides)
    raise ValueError(f"Unknown TTS backend: {backend!r}")


def prewarm() -> None:
    if TTS_BACKEND == "kokoro":
        from .kokoro import prewarm as _prewarm
    elif TTS_BACKEND == "fish":
        from .fish import prewarm as _prewarm
    elif TTS_BACKEND == "openai":
        from .openai import prewarm as _prewarm
    else:
        logger.warning(f"No prewarm for backend {TTS_BACKEND!r}")
        return
    _prewarm()
