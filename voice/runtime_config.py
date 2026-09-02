"""Shared resolution of the speech configuration used by native voice."""

from __future__ import annotations

from typing import Any

from voice.stt import STT_BACKEND, resolve_stt_backend
from voice.tts import TTS_BACKEND


def resolve_speech_config(persona: Any) -> tuple[dict, dict]:
    """Return the exact STT/TTS kwargs consumed by pipeline and warmup."""
    stt_kwargs = dict(persona.stt or {})
    stt_kwargs["backend"] = resolve_stt_backend(
        stt_kwargs.get("backend") or STT_BACKEND
    )

    tts_backend = (persona.tts_backend or TTS_BACKEND).lower()
    tts_kwargs: dict = {"backend": tts_backend}
    if persona.voice:
        if tts_backend == "kokoro":
            tts_kwargs["voice"] = persona.voice
            lang = getattr(persona, "lang", None)
            if lang:
                tts_kwargs["lang"] = lang
        elif tts_backend == "fish":
            tts_kwargs["reference_id"] = persona.voice
        elif tts_backend == "openai":
            tts_kwargs["voice"] = persona.voice
    if tts_backend == "openai":
        if persona.tts_url:
            tts_kwargs["url"] = persona.tts_url
        if persona.tts_model:
            tts_kwargs["model"] = persona.tts_model
        if persona.tts_api_key:
            tts_kwargs["api_key"] = persona.tts_api_key
    return stt_kwargs, tts_kwargs
