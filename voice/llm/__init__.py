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
    5. Fall back to OpenAILLMService (covers OpenAI itself, vLLM,
       LiteLLM, LM Studio, OpenRouter, anything OpenAI-compatible).

We deliberately don't perform a network probe. The previous version
did a synchronous ``httpx.get(<root>/api/version, timeout=1.5)`` to
catch the rare "Ollama on a non-default port and a non-Ollama
hostname" config, but ``make_llm`` is called from inside the
asyncio event loop during session setup, and a sync HTTP call there
pins the loop for up to 1.5s on every cold-cache URL. Users with
unusual Ollama deployments can set ``persona.llm.provider: ollama``
explicitly — that's a one-line config edit, much cheaper than the
loop-stall on every other user's hot path.
"""

from __future__ import annotations

import logging
import platform
import sys
from typing import Any
from urllib.parse import urlparse

from pipecat.services.openai.llm import OpenAILLMService

from .ollama import OllamaLLMService
from .protolabs import ProtoLabsLLMService

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
        # Pipecat stores persona overrides under `settings.extra`. The
        # OpenAI flow forwards `extra["extra_body"]` straight into the
        # SDK call. Ollama's native `/api/chat` doesn't take an
        # `extra_body` envelope — it has its own top-level `think`
        # field — so the factory translates here. Recognized shapes:
        #
        #   extra_body: { think: true }                    # native
        #   extra_body: { chat_template_kwargs: { enable_thinking: ... } }
        #
        # The second form is what vLLM/Qwen3 personas already use, so
        # accepting it lets the same `extra_body` config drive both
        # backends. Default stays `False` (suppress reasoning preamble)
        # since silent TTS during thinking is the primary failure mode
        # the native adapter is meant to fix.
        think = _resolve_ollama_think(settings)
        logger.info(
            f"[llm-factory] using Ollama adapter for {base_url} "
            f"model={model} think={think}"
        )
        return OllamaLLMService(
            api_key=api_key or "ollama",
            base_url=base_url,
            model=model,
            think=think,
            settings=settings,
        )

    # ProtoLabs gateway: reasoning model returns text in delta.reasoning_content
    # instead of delta.content — use the remapping subclass.
    if base_url and "proto-labs.ai" in base_url:
        logger.info(f"[llm-factory] using ProtoLabs adapter for {base_url} model={model}")
        return ProtoLabsLLMService(
            api_key=api_key,
            base_url=base_url,
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


def _resolve_ollama_think(settings: Any) -> bool:
    """Pull a ``think`` override out of ``settings.extra["extra_body"]``,
    accepting either the native Ollama field name (``think``) or the
    vLLM/Qwen3 chat-template convention (``chat_template_kwargs.enable_thinking``).

    Returns the explicit value if either is set, else ``False`` (the
    Ollama adapter's safer default — emitting reasoning content jams
    pipecat's sentence aggregator and produces silent TTS).
    """
    extra = getattr(settings, "extra", None) or {}
    extra_body = extra.get("extra_body") if isinstance(extra, dict) else None
    if not isinstance(extra_body, dict):
        return False
    if "think" in extra_body:
        return bool(extra_body["think"])
    ct_kw = extra_body.get("chat_template_kwargs")
    if isinstance(ct_kw, dict) and "enable_thinking" in ct_kw:
        return bool(ct_kw["enable_thinking"])
    return False


def is_apple_silicon() -> bool:
    """Public probe — used by the wizard to decide whether to surface
    the MLX preset / install-helper UI. Module-level constant cached
    at import time; safe to call repeatedly."""
    return _IS_APPLE_SILICON


def _detect_provider(base_url: str) -> str | None:
    """Return ``"ollama"`` if the URL looks like an Ollama instance,
    else ``None`` (caller falls back to OpenAI-compat). Pure URL-shape
    inspection — no network I/O. Users with non-standard Ollama
    deployments should set ``persona.llm.provider: ollama`` instead
    of relying on auto-detection. Result is cached per-URL since the
    cost is trivial but lets the call stay free."""
    if not base_url:
        return None
    if base_url in _PROVIDER_CACHE:
        return _PROVIDER_CACHE[base_url] or None

    resolved = "ollama" if _looks_like_ollama_url(base_url) else ""
    _PROVIDER_CACHE[base_url] = resolved
    return resolved or None


def _looks_like_ollama_url(base_url: str) -> bool:
    """Heuristic check that's fast and right 99% of the time. Default
    Ollama port is 11434; people sometimes use ``ollama`` in the
    hostname for tailnet/mDNS setups."""
    try:
        parsed = urlparse(base_url)
    except ValueError:
        return False
    # `parsed.port` is a property that re-parses the netloc and raises
    # ValueError on inputs like ``http://localhost:abc/v1`` — the
    # wizard accepts arbitrary URLs so we have to assume it can be fed
    # garbage. Catch the parse error and treat it as "no port info."
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port == 11434:
        return True
    if "ollama" in (parsed.hostname or "").lower():
        return True
    return False
