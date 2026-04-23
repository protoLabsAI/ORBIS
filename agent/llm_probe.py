"""LLM endpoint probing — drives the setup wizard's test / model-list /
local-detect flows.

Three concerns:

1. ``ping_endpoint(url, model, api_key)`` — real round-trip via the
   chat.completions API. Returns ``{ok, latency_ms, error}``. A minimal
   "ping" prompt exercises the actual request path rather than a
   synthetic probe; this catches broken middleware / wrong path
   prefixes / auth rejections that a HEAD request would miss.

2. ``list_models(url, api_key)`` — ``GET /models`` so the wizard can
   populate the model combobox with real options. Handles both
   OpenAI-compat (``data[].id``) and a couple of known non-compat shapes.

3. ``detect_local()`` — pings Ollama (:11434) and LM Studio (:1234) in
   parallel. Returns whichever responds. Voice-first homelab users get
   a "we noticed your local Ollama" callout in the wizard.

All paths are short-timeout + never raise — the wizard treats any
error as "server said no" and surfaces the reason.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_TEST_TIMEOUT = 15.0   # test call is a real inference; allow time
_META_TIMEOUT = 4.0    # /models + localhost probes
_LOCAL_PROBE_TIMEOUT = 1.0


def _normalize_base(url: str) -> str:
    """Trim trailing slashes. Callers pass the URL; we don't auto-fix paths."""
    return (url or "").rstrip("/")


# ---------------------------------------------------------------------------
# Test connection — real chat round-trip
# ---------------------------------------------------------------------------


async def ping_endpoint(url: str, model: str, api_key: str = "") -> dict:
    """Ping the chat endpoint with a minimal prompt and time it."""
    base = _normalize_base(url)
    if not base or not model:
        return {"ok": False, "error": "url and model are required"}

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    body = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 4,
        "temperature": 0,
        "stream": False,
    }

    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=_TEST_TIMEOUT) as client:
            r = await client.post(
                f"{base}/chat/completions", headers=headers, json=body,
            )
    except httpx.TimeoutException:
        return {"ok": False, "error": "timeout"}
    except httpx.RequestError as exc:
        return {"ok": False, "error": f"network: {exc}"}
    except Exception as exc:
        return {"ok": False, "error": f"unexpected: {exc}"}

    latency_ms = int((time.perf_counter() - t0) * 1000)

    if r.status_code == 401 or r.status_code == 403:
        return {"ok": False, "error": "auth rejected (check api key)", "latency_ms": latency_ms, "status": r.status_code}
    if r.status_code == 404:
        return {"ok": False, "error": "endpoint not found (check URL + model)", "latency_ms": latency_ms, "status": r.status_code}
    if r.status_code >= 400:
        # Try to surface the provider's error message.
        try:
            detail = r.json().get("error", {}).get("message") or r.text[:200]
        except Exception:
            detail = r.text[:200] or f"HTTP {r.status_code}"
        return {"ok": False, "error": detail, "latency_ms": latency_ms, "status": r.status_code}

    return {"ok": True, "latency_ms": latency_ms, "status": r.status_code}


# ---------------------------------------------------------------------------
# List models — /models, best-effort parsing
# ---------------------------------------------------------------------------


def _parse_models_response(data: Any) -> list[str]:
    """Pull an ID list out of the response. Handles:

    - OpenAI-compat: ``{data: [{id: ...}]}``
    - Anthropic: ``{data: [{id: ..., type: "model"}]}`` (same shape)
    - Ollama: ``{models: [{name: ...}]}``
    - LM Studio: ``{data: [{id: ...}]}`` (OpenAI-compat)
    - Google Generative: ``{models: [{name: ...}]}``

    Returns sorted unique list. Empty if nothing parses.
    """
    ids: list[str] = []
    if isinstance(data, dict):
        items = data.get("data") or data.get("models") or []
    elif isinstance(data, list):
        items = data
    else:
        items = []
    for item in items:
        if isinstance(item, dict):
            mid = item.get("id") or item.get("name") or item.get("model")
            if isinstance(mid, str) and mid.strip():
                ids.append(mid.strip())
        elif isinstance(item, str) and item.strip():
            ids.append(item.strip())
    # Dedupe + sort — UX improvement, and stable order for tests.
    return sorted(set(ids))


async def list_models(url: str, api_key: str = "") -> dict:
    """GET /models against the URL. Returns ``{ok, models[], error?}``."""
    base = _normalize_base(url)
    if not base:
        return {"ok": False, "models": [], "error": "url is required"}

    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # Try the standard /models first; Ollama's native path is /api/tags.
    candidates = [f"{base}/models"]
    if "localhost" in base or "127.0.0.1" in base:
        # Likely-Ollama extra candidate. Harmless on other local servers —
        # they just 404 and we fall through.
        candidates.append(base.replace("/v1", "") + "/api/tags")

    last_error = "no response"
    async with httpx.AsyncClient(timeout=_META_TIMEOUT) as client:
        for candidate in candidates:
            try:
                r = await client.get(candidate, headers=headers)
            except Exception as exc:
                last_error = f"network: {exc}"
                continue
            if r.status_code == 401 or r.status_code == 403:
                return {"ok": False, "models": [], "error": "auth rejected"}
            if r.status_code >= 400:
                last_error = f"HTTP {r.status_code}"
                continue
            try:
                payload = r.json()
            except Exception:
                last_error = "response was not JSON"
                continue
            models = _parse_models_response(payload)
            if models:
                return {"ok": True, "models": models}
            last_error = "response had no model ids"

    return {"ok": False, "models": [], "error": last_error}


# ---------------------------------------------------------------------------
# Local detect — Ollama + LM Studio parallel probe
# ---------------------------------------------------------------------------


_OLLAMA_URL = "http://127.0.0.1:11434"
_LM_STUDIO_URL = "http://127.0.0.1:1234"


async def _probe(url: str, path: str) -> list[str] | None:
    """Return parsed model list or None if the server isn't reachable."""
    try:
        async with httpx.AsyncClient(timeout=_LOCAL_PROBE_TIMEOUT) as client:
            r = await client.get(f"{url}{path}")
    except Exception:
        return None
    if r.status_code >= 400:
        return None
    try:
        return _parse_models_response(r.json())
    except Exception:
        return None


async def detect_local() -> dict:
    """Probe Ollama (:11434) + LM Studio (:1234) in parallel.

    Returns ``{ollama?: {url, models}, lm_studio?: {url, models}}`` —
    omits providers that didn't respond. ``models`` may be empty if the
    server responded but hasn't had any models pulled yet; callers
    should still treat non-None as "present".
    """
    ollama_task = asyncio.create_task(_probe(_OLLAMA_URL, "/api/tags"))
    lm_task = asyncio.create_task(_probe(_LM_STUDIO_URL, "/v1/models"))
    ollama_models, lm_models = await asyncio.gather(
        ollama_task, lm_task, return_exceptions=False,
    )

    found: dict[str, Any] = {}
    if ollama_models is not None:
        # Ollama's OpenAI-compat endpoint lives at /v1.
        found["ollama"] = {
            "url": f"{_OLLAMA_URL}/v1",
            "models": ollama_models,
        }
    if lm_models is not None:
        found["lm_studio"] = {
            "url": f"{_LM_STUDIO_URL}/v1",
            "models": lm_models,
        }
    return found
