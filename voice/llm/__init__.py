"""LLM service factory.

Single entry point that picks the right pipecat LLMService subclass
based on the configured backend. New providers slot in as additional
adapters without app.py needing to know about them.

Selection precedence:

    1. Explicit ``provider`` kwarg (e.g. ``provider="ollama"``)
    2. URL-shape detection — Ollama default port (11434) or
       hostname containing ``ollama``
    3. Probe ``GET <root>/api/version`` (Ollama-specific endpoint)
       and route to OllamaLLMService if it returns 200 with a
       version string
    4. Fall back to OpenAILLMService (covers OpenAI itself, vLLM,
       LiteLLM, LM Studio, OpenRouter, anything OpenAI-compatible)

The probe is best-effort — at desktop scale a single round-trip is
cheap, but failures are non-fatal: any timeout / connection error
yields the OpenAI fallback. Result is cached per-(url) so repeated
sessions don't re-probe.
"""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urlparse

import httpx
from pipecat.services.openai.llm import OpenAILLMService

from .ollama import OllamaLLMService

logger = logging.getLogger(__name__)

# Cache of base_url → resolved provider, so repeated session creation
# doesn't re-probe. Empty-string values mean "treat as OpenAI" (the
# fallback).
_PROVIDER_CACHE: dict[str, str] = {}


def make_llm(
    *,
    base_url: str,
    model: str,
    api_key: str,
    settings: Any,
    provider: str | None = None,
    using_custom_url: bool = False,
) -> OpenAILLMService:
    """Construct the pipecat LLMService matching the configured backend.

    Args:
        base_url: OpenAI-compat URL (e.g. ``http://127.0.0.1:11434/v1``).
        model: model identifier (provider-specific).
        api_key: provider auth key. Use empty / ``"ollama"`` placeholder
            for backends that don't authenticate.
        settings: ``OpenAILLMService.Settings`` instance — passed
            through to the chosen adapter; both adapters use the same
            settings shape since OllamaLLMService extends BaseOpenAI.
        provider: optional explicit override. ``"ollama"`` /
            ``"openai"``. When unset we auto-detect.
        using_custom_url: tells the OpenAI fallback whether the URL
            is the project default (which doesn't accept the OpenAI
            ``role: developer`` field) so it can disable that field.

    Returns:
        A constructed pipecat LLMService ready to attach to a pipeline.
    """
    resolved = (provider or _detect_provider(base_url) or "openai").lower()
    if resolved == "ollama":
        logger.info(f"[llm-factory] using Ollama adapter for {base_url} model={model}")
        return OllamaLLMService(
            api_key=api_key or "ollama",
            base_url=base_url,
            model=model,
            settings=settings,
        )

    logger.info(f"[llm-factory] using OpenAI-compat adapter for {base_url} model={model}")
    svc = OpenAILLMService(
        api_key=api_key,
        base_url=base_url,
        settings=settings,
    )
    if not using_custom_url:
        # vLLM rejects OpenAI's `role: developer` field — strip it
        # for the project's default endpoint. Custom URLs are
        # assumed to be real OpenAI-compat gateways that accept it.
        svc.supports_developer_role = False
    return svc


def _detect_provider(base_url: str) -> str | None:
    """Return ``"ollama"`` if the URL looks like an Ollama instance,
    else None (caller falls back to OpenAI). Cached per-URL."""
    if not base_url:
        return None
    if base_url in _PROVIDER_CACHE:
        return _PROVIDER_CACHE[base_url] or None

    if _looks_like_ollama_url(base_url):
        _PROVIDER_CACHE[base_url] = "ollama"
        return "ollama"

    # Probe /api/version. Ollama-only endpoint; OpenAI/vLLM/LiteLLM
    # all return 404. Cheap one-shot, 1.5s ceiling.
    if os.environ.get("ORBIS_LLM_DETECT_DISABLE") == "1":
        return None
    root = base_url.rstrip("/").removesuffix("/v1")
    try:
        r = httpx.get(f"{root}/api/version", timeout=1.5)
        if r.status_code == 200 and "version" in (r.text or ""):
            _PROVIDER_CACHE[base_url] = "ollama"
            return "ollama"
    except (httpx.HTTPError, OSError):
        pass

    _PROVIDER_CACHE[base_url] = ""
    return None


def _looks_like_ollama_url(base_url: str) -> bool:
    """Heuristic check that's fast and right 99% of the time. Default
    Ollama port is 11434; people sometimes use ``ollama`` in the
    hostname for tailnet/mDNS setups."""
    try:
        parsed = urlparse(base_url)
    except ValueError:
        return False
    if parsed.port == 11434:
        return True
    if "ollama" in (parsed.hostname or "").lower():
        return True
    return False
