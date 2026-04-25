"""LLM service factory.

Single entry point that picks the right pipecat LLMService subclass
based on the configured backend. New providers slot in as additional
adapters without app.py needing to know about them.

Selection precedence:

    1. Explicit ``provider`` kwarg (``"mlx"``, ``"ollama"``, ``"openai"``).
    2. ``mlx://`` URL scheme is the explicit "use the in-process MLX
       adapter" signal. Model id follows the scheme:
       ``mlx://mlx-community/gemma-3n-E2B-it-4bit``.
    3. Apple-Silicon auto-prefer: if we're on macOS arm64 + ``mlx_lm``
       is importable + the URL points at the project default Ollama
       endpoint (``http://127.0.0.1:11434/v1``), prefer MLX with the
       configured model id translated through the ``mlx-community/``
       org. Lets users keep their existing Ollama config and silently
       upgrade to native inference. Disable with ``ORBIS_PREFER_MLX=0``.
    4. URL-shape detection — Ollama default port (11434) or hostname
       containing ``ollama``.
    5. Probe ``GET <root>/api/version`` (Ollama-specific endpoint)
       and route to OllamaLLMService if it returns 200 with a
       version string.
    6. Fall back to OpenAILLMService (covers OpenAI itself, vLLM,
       LiteLLM, LM Studio, OpenRouter, anything OpenAI-compatible).

The probe is best-effort — at desktop scale a single round-trip is
cheap, but failures are non-fatal: any timeout / connection error
yields the OpenAI fallback. Result is cached per-(url) so repeated
sessions don't re-probe.
"""

from __future__ import annotations

import logging
import os
import platform
import sys
from typing import Any
from urllib.parse import urlparse

import httpx
from pipecat.services.openai.llm import OpenAILLMService

from .ollama import OllamaLLMService

logger = logging.getLogger(__name__)

# Cached at module level — checking platform + import availability
# is essentially free but doing it once is cleaner.
_IS_APPLE_SILICON = (
    sys.platform == "darwin" and platform.machine() == "arm64"
)


def _import_mlx_service():
    """Lazy MLX import — module imports `mlx_lm` at top, which is
    Mac-arm64-only. Only call when we're sure we're on the right
    platform. Returns the MLXLLMService class or None on failure."""
    try:
        from .mlx import MLXLLMService
        return MLXLLMService
    except Exception as e:
        logger.warning(f"[llm-factory] mlx adapter unavailable: {e}")
        return None

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
    # Explicit mlx:// scheme always wins.
    if base_url and base_url.startswith("mlx://"):
        provider = provider or "mlx"
        # Model is encoded in the URL after the scheme; strip and use
        # the value directly, ignoring whatever was in `model`.
        model = base_url[len("mlx://"):] or model

    resolved = (provider or _detect_provider(base_url) or "openai").lower()

    # MLX is opt-in only — picked explicitly via the wizard's "Built-in
    # (MLX)" preset (which sets a `mlx://...` URL) or via
    # `provider="mlx"` in the persona config. We deliberately don't
    # auto-upgrade Ollama users — pulling a different model under
    # them, with a multi-GB first-run download, is the kind of
    # surprise side-effect that erodes trust.
    if resolved == "mlx":
        mlx_cls = _import_mlx_service()
        if mlx_cls is not None:
            logger.info(f"[llm-factory] using MLX adapter model={model}")
            return mlx_cls(model=model, settings=settings)
        logger.warning("[llm-factory] mlx requested but unavailable; falling through to Ollama")
        resolved = "ollama"

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


def is_apple_silicon() -> bool:
    """Public probe — used by the wizard to decide whether to surface
    the MLX preset / install-helper UI. Module-level constant cached
    at import time; safe to call repeatedly."""
    return _IS_APPLE_SILICON


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
