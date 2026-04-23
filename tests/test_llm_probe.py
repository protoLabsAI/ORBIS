"""Tests for the LLM probe module.

HTTP is mocked via respx so tests don't depend on any real provider.
Covers:
  - ping_endpoint: auth rejection, 404, 5xx, network error, happy path
  - list_models: OpenAI shape, Ollama shape, falls back through candidate
                 paths, auth-reject short-circuits
  - detect_local: both/either/neither local services responding
  - _parse_models_response: the parsing truth table
"""

from __future__ import annotations

import asyncio

import pytest

from agent.llm_probe import (
    _parse_models_response,
    detect_local,
    list_models,
    ping_endpoint,
)


# --- parser unit tests ------------------------------------------------------


def test_parse_openai_shape():
    assert _parse_models_response({
        "data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}],
    }) == ["gpt-4o", "gpt-4o-mini"]


def test_parse_ollama_shape():
    assert _parse_models_response({
        "models": [{"name": "llama3:latest"}, {"name": "qwen2.5:7b"}],
    }) == ["llama3:latest", "qwen2.5:7b"]


def test_parse_bare_list():
    assert _parse_models_response(["a", "b", "a"]) == ["a", "b"]


def test_parse_empty_or_malformed():
    assert _parse_models_response({}) == []
    assert _parse_models_response(None) == []
    assert _parse_models_response("junk") == []


def test_parse_dedupes_and_sorts():
    result = _parse_models_response({
        "data": [{"id": "b"}, {"id": "a"}, {"id": "b"}],
    })
    assert result == ["a", "b"]


# --- ping_endpoint (with respx) -------------------------------------------


def _try_import_respx():
    try:
        import respx  # type: ignore
        return respx
    except ImportError:
        return None


respx = _try_import_respx()
requires_respx = pytest.mark.skipif(respx is None, reason="respx not installed")


@requires_respx
def test_ping_endpoint_auth_reject(respx_mock):  # type: ignore[no-untyped-def]
    respx_mock.post("https://api.example/v1/chat/completions").respond(
        status_code=401, json={"error": {"message": "bad key"}},
    )
    result = asyncio.run(ping_endpoint("https://api.example/v1", "m", "wrong"))
    assert result["ok"] is False
    assert "auth" in result["error"]


@requires_respx
def test_ping_endpoint_404(respx_mock):
    respx_mock.post("https://api.example/v1/chat/completions").respond(status_code=404)
    result = asyncio.run(ping_endpoint("https://api.example/v1", "m", "k"))
    assert result["ok"] is False
    assert "not found" in result["error"]


@requires_respx
def test_ping_endpoint_happy_path(respx_mock):
    respx_mock.post("https://api.example/v1/chat/completions").respond(
        status_code=200, json={"choices": [{"message": {"content": "pong"}}]},
    )
    result = asyncio.run(ping_endpoint("https://api.example/v1", "m", "k"))
    assert result["ok"] is True
    assert isinstance(result["latency_ms"], int)


@requires_respx
def test_ping_endpoint_5xx_surfaces_provider_message(respx_mock):
    respx_mock.post("https://api.example/v1/chat/completions").respond(
        status_code=500, json={"error": {"message": "upstream is on fire"}},
    )
    result = asyncio.run(ping_endpoint("https://api.example/v1", "m", "k"))
    assert result["ok"] is False
    assert "upstream is on fire" in result["error"]


def test_ping_endpoint_requires_url_and_model():
    r1 = asyncio.run(ping_endpoint("", "m", "k"))
    r2 = asyncio.run(ping_endpoint("http://x", "", "k"))
    assert r1["ok"] is False
    assert r2["ok"] is False


# --- list_models ------------------------------------------------------------


@requires_respx
def test_list_models_openai_shape(respx_mock):
    respx_mock.get("https://api.example/v1/models").respond(
        status_code=200,
        json={"data": [{"id": "gpt-4o-mini"}, {"id": "gpt-4o"}]},
    )
    result = asyncio.run(list_models("https://api.example/v1", "k"))
    assert result["ok"] is True
    assert "gpt-4o" in result["models"]
    assert "gpt-4o-mini" in result["models"]


@requires_respx
def test_list_models_auth_reject_short_circuits(respx_mock):
    respx_mock.get("https://api.example/v1/models").respond(status_code=401)
    result = asyncio.run(list_models("https://api.example/v1", "bad"))
    assert result["ok"] is False
    assert "auth" in result["error"]


@requires_respx
def test_list_models_ollama_fallback(respx_mock):
    # /models 404 → retry /api/tags (only for localhost URLs).
    respx_mock.get("http://127.0.0.1:11434/v1/models").respond(status_code=404)
    respx_mock.get("http://127.0.0.1:11434/api/tags").respond(
        status_code=200,
        json={"models": [{"name": "llama3:latest"}]},
    )
    result = asyncio.run(list_models("http://127.0.0.1:11434/v1", ""))
    assert result["ok"] is True
    assert "llama3:latest" in result["models"]


def test_list_models_requires_url():
    result = asyncio.run(list_models("", "k"))
    assert result["ok"] is False


# --- detect_local -----------------------------------------------------------


@requires_respx
def test_detect_local_finds_both(respx_mock):
    respx_mock.get("http://127.0.0.1:11434/api/tags").respond(
        status_code=200, json={"models": [{"name": "llama3"}]},
    )
    respx_mock.get("http://127.0.0.1:1234/v1/models").respond(
        status_code=200, json={"data": [{"id": "phi-3"}]},
    )
    result = asyncio.run(detect_local())
    assert "ollama" in result
    assert result["ollama"]["url"].endswith("/v1")
    assert "llama3" in result["ollama"]["models"]
    assert "lm_studio" in result
    assert "phi-3" in result["lm_studio"]["models"]


@requires_respx
def test_detect_local_finds_one(respx_mock):
    respx_mock.get("http://127.0.0.1:11434/api/tags").respond(
        status_code=200, json={"models": [{"name": "llama3"}]},
    )
    # LM Studio port not mocked → httpx raises, probe returns None.
    result = asyncio.run(detect_local())
    assert "ollama" in result
    assert "lm_studio" not in result


@requires_respx
def test_detect_local_finds_nothing(respx_mock):
    # Neither mocked; both probes fail (ConnectError) and return None.
    result = asyncio.run(detect_local())
    assert result == {}


@requires_respx
def test_detect_local_handles_empty_model_list(respx_mock):
    """Ollama with no pulled models returns {models: []}; still counts
    as present so the wizard can surface it."""
    respx_mock.get("http://127.0.0.1:11434/api/tags").respond(
        status_code=200, json={"models": []},
    )
    result = asyncio.run(detect_local())
    assert "ollama" in result
    assert result["ollama"]["models"] == []
