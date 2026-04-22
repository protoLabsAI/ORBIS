"""OpenAI-compatible TTS — works against OpenAI proper, LocalAI,
OpenRouter, vllm-omni's serving_speech, and anything else that exposes
`POST /v1/audio/speech`.

For LocalAI specifically you typically want:
  TTS_BACKEND=openai
  TTS_OPENAI_URL=http://localai:8080/v1
  TTS_OPENAI_MODEL=kokoro       (or whatever you've registered)
  TTS_OPENAI_VOICE=af_heart     (the model's voice id)
"""

from __future__ import annotations

import logging
import os

import httpx
from pipecat.services.openai.tts import OpenAITTSService
from pipecat.services.settings import TTSSettings

logger = logging.getLogger(__name__)

OPENAI_TTS_URL = os.environ.get("TTS_OPENAI_URL", "https://api.openai.com/v1")
OPENAI_TTS_MODEL = os.environ.get("TTS_OPENAI_MODEL", "tts-1")
OPENAI_TTS_VOICE = os.environ.get("TTS_OPENAI_VOICE", "alloy")
OPENAI_TTS_API_KEY = os.environ.get("TTS_OPENAI_API_KEY", "not-needed")
OPENAI_TTS_SAMPLE_RATE = int(os.environ.get("TTS_OPENAI_SAMPLE_RATE", "24000"))


def make(*, voice: str | None = None, **_unused) -> OpenAITTSService:
    """Build an OpenAITTSService against the configured endpoint."""
    chosen_voice = voice or OPENAI_TTS_VOICE
    logger.info(
        f"TTS backend: openai @ {OPENAI_TTS_URL} model={OPENAI_TTS_MODEL} "
        f"voice={chosen_voice}"
    )
    # OpenAI TTS takes plain text — filter out any Fish-style prosody
    # tags the LLM emitted so they aren't spoken as brackets.
    from agent.prosody import ProsodyTextFilter
    return OpenAITTSService(
        api_key=OPENAI_TTS_API_KEY,
        base_url=OPENAI_TTS_URL,
        sample_rate=OPENAI_TTS_SAMPLE_RATE,
        settings=TTSSettings(
            model=OPENAI_TTS_MODEL,
            voice=chosen_voice,
            language=None,
        ),
        text_filters=[ProsodyTextFilter()],
    )


def prewarm() -> None:
    """One-shot synth call to absorb cold-start. Best-effort."""
    headers = {}
    if OPENAI_TTS_API_KEY and OPENAI_TTS_API_KEY != "not-needed":
        headers["Authorization"] = f"Bearer {OPENAI_TTS_API_KEY}"
    try:
        r = httpx.post(
            f"{OPENAI_TTS_URL.rstrip('/')}/audio/speech",
            headers=headers,
            json={"model": OPENAI_TTS_MODEL, "input": "Hello.", "voice": OPENAI_TTS_VOICE},
            timeout=30.0,
        )
        r.raise_for_status()
        logger.info(f"OpenAI-TTS warm ({len(r.content)} bytes)")
    except Exception as e:
        logger.warning(f"OpenAI-TTS prewarm skipped: {e}")
