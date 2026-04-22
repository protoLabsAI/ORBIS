"""Data model for a voice persona / skill."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Skill:
    """A voice persona — system prompt + TTS voice + (optionally) LLM overrides.

    `slug` is the machine id (URL-safe). `name` is the human-readable label
    for the UI.
    """

    slug: str
    name: str
    system_prompt: str
    tts_backend: str = "fish"        # "fish" or "kokoro"
    voice: str | None = None         # Kokoro voice id OR Fish reference_id
    lang: str | None = None
    temperature: float = 0.7
    max_tokens: int = 150
    description: str = ""
    filler_verbosity: str | None = None  # per-skill override; None = keep current
    tools: list[str] = field(default_factory=list)  # names to restrict to; empty = all
    # Per-skill pipeline-behavior overrides. Each controller reads its own
    # sub-key at session-open; absent keys fall back to env / module defaults.
    # Shape (all optional):
    #   { backchannel: false|true|{first_ms, interval_ms, enabled},
    #     micro_ack:   false|true|{first_ms, enabled},
    #     bargein:     false|true|{grace_ms, enabled} }
    behavior: dict[str, Any] = field(default_factory=dict)
    # Per-skill LLM endpoint override. When absent, uses the env defaults
    # (LLM_URL / LLM_SERVED_NAME / LLM_API_KEY). When present, routes this
    # skill's chat completions to a different endpoint — e.g. a LiteLLM
    # gateway, OpenAI directly, Anthropic via a proxy, etc.
    # Shape: { url, model, api_key_env, extra_body }
    llm: dict[str, Any] = field(default_factory=dict)
    # Per-skill delegate filter. None/empty = expose all registered delegates.
    # Non-empty list = expose only the named ones through `delegate_to()`.
    delegates: list[str] = field(default_factory=list)
    # Optional dedicated orb visualizer for this skill. When set, the client
    # auto-applies it on skill switch: first setVariant(variant), then
    # applyPreset(palette), then applyParam(k, v) for each entry in params.
    # Unknown variant names are logged + ignored on the client (registry
    # lookup fails gracefully).
    # Shape: { variant?: str, palette?: str, params?: dict[str, any] }
    viz: dict[str, Any] = field(default_factory=dict)


DEFAULT_SOUL_SLUG = "default"
