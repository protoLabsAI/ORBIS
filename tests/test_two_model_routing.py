"""Tests for two-model LLM routing (orbis-3it).

Smart model for the tool-decision turn, fast model for the post-tool
narration turn — detected by a role:tool message in context. The split
is opt-in via persona.llm.router_model/content_model or
LLM_ROUTER_MODEL/LLM_CONTENT_MODEL; unset = single-model behavior.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from pipecat.services.openai.llm import OpenAILLMService
from voice.llm import make_llm
from voice.llm.two_model import TwoModelOpenAILLMService, _has_tool_result


def _settings(model="base-model"):
    return OpenAILLMService.Settings(model=model)


# --- _has_tool_result ------------------------------------------------------


def test_has_tool_result_detects_dict_tool_message() -> None:
    assert _has_tool_result([{"role": "user", "content": "hi"},
                             {"role": "tool", "content": "{}"}]) is True


def test_has_tool_result_false_without_tool() -> None:
    assert _has_tool_result([{"role": "system", "content": "x"},
                             {"role": "user", "content": "hi"},
                             {"role": "assistant", "content": "yo"}]) is False


def test_has_tool_result_handles_objects_and_empty() -> None:
    assert _has_tool_result([SimpleNamespace(role="tool")]) is True
    assert _has_tool_result([SimpleNamespace(role="user")]) is False
    assert _has_tool_result([]) is False
    assert _has_tool_result(None) is False


# --- make_llm dispatch -----------------------------------------------------


def test_make_llm_single_model_when_no_split() -> None:
    svc = make_llm(base_url="https://gw/v1", model="m", api_key="k",
                   settings=_settings("m"), using_custom_url=True)
    assert type(svc) is OpenAILLMService


def test_make_llm_single_model_when_router_equals_content() -> None:
    # Both equal to base → no real split → plain service.
    svc = make_llm(base_url="https://gw/v1", model="m", api_key="k",
                   settings=_settings("m"), using_custom_url=True,
                   router_model="m", content_model="m")
    assert type(svc) is OpenAILLMService


def test_make_llm_two_model_when_split() -> None:
    svc = make_llm(base_url="https://gw/v1", model="protolabs/smart", api_key="k",
                   settings=_settings("protolabs/smart"), using_custom_url=True,
                   router_model="protolabs/smart", content_model="protolabs/fast")
    assert isinstance(svc, TwoModelOpenAILLMService)
    assert svc._router_model == "protolabs/smart"
    assert svc._content_model == "protolabs/fast"


def test_make_llm_split_defaults_unset_side_to_base_model() -> None:
    # Only content_model set → router defaults to base `model`; they
    # differ → two-model.
    svc = make_llm(base_url="https://gw/v1", model="smart", api_key="k",
                   settings=_settings("smart"), using_custom_url=True,
                   content_model="fast")
    assert isinstance(svc, TwoModelOpenAILLMService)
    assert svc._router_model == "smart"
    assert svc._content_model == "fast"


def test_make_llm_mlx_url_ignores_split() -> None:
    # MLX is a single local model — the split doesn't apply. Falls through
    # to the MLX/Ollama branch, never the TwoModel one. (mlx unavailable
    # in CI degrades to Ollama, still not TwoModel.)
    svc = make_llm(base_url="mlx://some/model", model="x", api_key="k",
                   settings=_settings("x"), router_model="a", content_model="b")
    assert not isinstance(svc, TwoModelOpenAILLMService)


# --- model selection per call ----------------------------------------------


def test_build_params_uses_content_model_on_tool_turn() -> None:
    svc = TwoModelOpenAILLMService(
        api_key="k", base_url="https://gw/v1", settings=_settings("base"),
        router_model="protolabs/smart", content_model="protolabs/fast",
    )
    params = svc.build_chat_completion_params({
        "messages": [{"role": "user", "content": "weather?"},
                     {"role": "assistant", "content": ""},
                     {"role": "tool", "content": "72F"}],
        "tools": [],
        "tool_choice": "auto",
    })
    assert params["model"] == "protolabs/fast"


def test_build_params_uses_router_model_on_plain_turn() -> None:
    svc = TwoModelOpenAILLMService(
        api_key="k", base_url="https://gw/v1", settings=_settings("base"),
        router_model="protolabs/smart", content_model="protolabs/fast",
    )
    params = svc.build_chat_completion_params({
        "messages": [{"role": "system", "content": "you are orb"},
                     {"role": "user", "content": "hey"}],
        "tools": [],
        "tool_choice": "auto",
    })
    assert params["model"] == "protolabs/smart"


# --- resolver wiring -------------------------------------------------------


@pytest.fixture
def resolve(monkeypatch):
    import app as _app
    for k in ("LLM_ROUTER_MODEL", "LLM_CONTENT_MODEL"):
        monkeypatch.delenv(k, raising=False)
    return _app._resolve_skill_llm


def _skill(**llm):
    return SimpleNamespace(
        llm=llm or None, temperature=0.7, max_tokens=256,
    )


def test_resolver_no_split_returns_none(resolve) -> None:
    cfg = resolve(_skill())
    assert cfg["router_model"] is None
    assert cfg["content_model"] is None


def test_resolver_persona_split(resolve) -> None:
    cfg = resolve(_skill(router_model="protolabs/smart", content_model="protolabs/fast"))
    assert cfg["router_model"] == "protolabs/smart"
    assert cfg["content_model"] == "protolabs/fast"


def test_resolver_env_split(resolve, monkeypatch) -> None:
    monkeypatch.setenv("LLM_ROUTER_MODEL", "smart-env")
    monkeypatch.setenv("LLM_CONTENT_MODEL", "fast-env")
    cfg = resolve(_skill())
    assert cfg["router_model"] == "smart-env"
    assert cfg["content_model"] == "fast-env"


def test_resolver_persona_beats_env(resolve, monkeypatch) -> None:
    monkeypatch.setenv("LLM_ROUTER_MODEL", "smart-env")
    cfg = resolve(_skill(router_model="smart-persona"))
    assert cfg["router_model"] == "smart-persona"
