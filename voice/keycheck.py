"""Loud diagnostics for audio backends configured to silently 401.

The OpenAI-compatible STT and TTS backends default their api_key to the
``"not-needed"`` placeholder. That's correct for a *local* endpoint (a
LAN Whisper server, LocalAI, vllm-omni) that doesn't authenticate — but
against a hosted endpoint that requires a key it produces a 401 with no
hint, and the shipped default URL for both is ``https://api.openai.com/v1``.

This mirrors ``app.py::_resolve_api_key`` for the LLM path (#648): don't
let a placeholder key against a hosted endpoint fail silently — name it.
Only a hosted host that's known to require auth triggers the warning, so
legitimately-keyless local endpoints stay quiet.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Keys that mean "no real credential was supplied."
_PLACEHOLDER_KEYS = {"", "not-needed"}

# Hosts that require auth, so a placeholder key there is a guaranteed 401.
# Kept to the shipped default rather than "any public host" — a plain LAN
# hostname can legitimately be keyless, and this is a warning, not a gate.
_HOSTED_KEYED_HOSTS = {"api.openai.com"}


def warn_if_placeholder_key(kind: str, url: str, api_key: str | None) -> None:
    """WARN if an openai-compat ``kind`` (``"stt"``/``"tts"``) backend points
    at a hosted, auth-requiring endpoint with only the placeholder key."""
    if (api_key or "").strip() not in _PLACEHOLDER_KEYS:
        return
    host = (urlparse(url or "").hostname or "").lower()
    if host in _HOSTED_KEYED_HOSTS:
        logger.warning(
            "[%s] backend=openai points at %s with no API key — it will 401 "
            "(the key resolved to the 'not-needed' placeholder). Set the key "
            "in Settings, or point at a local endpoint that needs no auth.",
            kind, host,
        )
