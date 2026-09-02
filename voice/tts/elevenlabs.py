"""ElevenLabs TTS adapter — uses pipecat's native WebSocket service.

ElevenLabs also exposes an OpenAI-compatible endpoint that works with
``TTS_BACKEND=openai``, but the native service has better latency and
streaming characteristics for voice-first UX. Pick this one when
you're willing to spend the API dollars for the quality bump.

Env::

    ELEVENLABS_API_KEY=...
    ELEVENLABS_VOICE_ID=<voice_id>          # default 21m00Tcm4TlvDq8ikWAM (Rachel)
    ELEVENLABS_MODEL=eleven_turbo_v2_5      # low-latency model
    ELEVENLABS_SAMPLE_RATE=24000
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
ELEVENLABS_MODEL = os.environ.get("ELEVENLABS_MODEL", "eleven_turbo_v2_5")
ELEVENLABS_SAMPLE_RATE = int(os.environ.get("ELEVENLABS_SAMPLE_RATE", "24000"))


def make(*, voice: str | None = None, **_unused):
    """Build an ElevenLabsTTSService. Voice override supported via the
    ``voice`` kwarg (comes from the persona config)."""
    if not ELEVENLABS_API_KEY:
        raise RuntimeError(
            "ELEVENLABS_API_KEY is not set. Set it in .env or the "
            "deployment environment."
        )
    try:
        from pipecat.services.elevenlabs.tts import (
            ElevenLabsTTSService,
            ElevenLabsTTSSettings,
        )
    except ImportError as exc:
        raise RuntimeError(
            "pipecat's elevenlabs service isn't installed. "
            "`pip install pipecat-ai[elevenlabs]`."
        ) from exc

    chosen_voice = voice or ELEVENLABS_VOICE_ID
    logger.info(
        f"TTS backend: elevenlabs voice={chosen_voice} model={ELEVENLABS_MODEL}"
    )
    # Strip Fish-style prosody tags out of the text stream before TTS —
    # ElevenLabs doesn't understand them and would speak the brackets.
    from agent.prosody import ProsodyTextFilter
    return ElevenLabsTTSService(
        api_key=ELEVENLABS_API_KEY,
        voice_id=chosen_voice,
        sample_rate=ELEVENLABS_SAMPLE_RATE,
        settings=ElevenLabsTTSSettings(model=ELEVENLABS_MODEL),
        text_filters=[ProsodyTextFilter()],
    )


def prewarm(**_overrides) -> None:
    """Best-effort warmup — ElevenLabs's WebSocket service is primarily
    latency-on-first-byte; we don't synthesize anything here because
    the websocket session is established at pipeline-start time."""
    if not ELEVENLABS_API_KEY:
        logger.info("[elevenlabs] prewarm skipped — ELEVENLABS_API_KEY unset")
        return
    logger.info("[elevenlabs] prewarm no-op (websocket warms at pipeline start)")
