"""TTS backend selection.

Backends:
  - kokoro       : Local Kokoro 82M, CPU-friendly, in-process. ORBIS default.
  - openai       : Any OpenAI-compatible `/v1/audio/speech` endpoint
                   (LocalAI, OpenRouter, ElevenLabs-OpenAI-compat, etc.)
  - elevenlabs   : Pipecat's native ElevenLabs WebSocket service (pay-API,
                   best quality).
  - fish         : Fish Audio S2-Pro sidecar — opt-in BYO reference voices
                   and prosody tags. Requires the fish-speech compose profile.

Selection: ``TTS_BACKEND`` env var (default ``kokoro``) or via
``config/orbis.yaml``'s ``voice.tts_backend`` (the persona loader
sets TTS_BACKEND if the YAML value is present, provided the env
isn't already set).
"""

import logging
import os

from pipecat.services.tts_service import TTSService

logger = logging.getLogger(__name__)

TTS_BACKEND = os.environ.get("TTS_BACKEND", "kokoro").lower()


def make_tts(**overrides) -> TTSService:
    backend = (overrides.pop("backend", TTS_BACKEND) or "kokoro").lower()
    if backend == "kokoro":
        from .kokoro import LocalKokoroTTS
        return LocalKokoroTTS(**overrides)
    if backend == "openai":
        from . import openai as openai_tts
        return openai_tts.make(**overrides)
    if backend == "elevenlabs":
        from . import elevenlabs as elevenlabs_tts
        return elevenlabs_tts.make(**overrides)
    if backend == "fish":
        from .fish import FishAudioTTS
        return FishAudioTTS(**overrides)
    raise ValueError(f"Unknown TTS backend: {backend!r}")


def prewarm(**overrides) -> None:
    backend = (overrides.pop("backend", TTS_BACKEND) or "kokoro").lower()
    if backend == "kokoro":
        from .kokoro import prewarm as _prewarm
    elif backend == "openai":
        from .openai import prewarm as _prewarm
    elif backend == "elevenlabs":
        from .elevenlabs import prewarm as _prewarm
    elif backend == "fish":
        from .fish import prewarm as _prewarm
    else:
        logger.warning(f"No prewarm for backend {backend!r}")
        return
    _prewarm(**overrides)
