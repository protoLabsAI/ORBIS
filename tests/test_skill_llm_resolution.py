"""Tests for _resolve_skill_llm — the shared LLM routing helper that
both run_bot (voice) and text_agent (A2A inbound) consume.

Closes R14: text_agent used to hard-code env LLM_URL/LLM_API_KEY,
ignoring persona.llm.{url,model,api_key} overrides. Now both paths
route through a single resolver.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.fixture
def helper(monkeypatch):
    """Import the helper inside the test scope so env-var patches take
    effect before the module-level constants are read on first import.
    Resolves the helper through the already-imported module."""
    monkeypatch.setenv("LLM_URL", "http://env-default:8100/v1")
    monkeypatch.setenv("LLM_SERVED_NAME", "env-default-model")
    monkeypatch.setenv("LLM_API_KEY", "env-default-key")
    # The module-level constants in app.py snapshot env at import time.
    # We patch them directly so the helper uses our test values.
    import app as _app
    monkeypatch.setattr(_app, "LLM_URL", "http://env-default:8100/v1")
    monkeypatch.setattr(_app, "LLM_SERVED_NAME", "env-default-model")
    monkeypatch.setattr(_app, "LLM_API_KEY", "env-default-key")
    return _app._resolve_skill_llm


def _skill(**llm_overrides) -> SimpleNamespace:
    return SimpleNamespace(
        llm=llm_overrides if llm_overrides else None,
    )


# --- defaults --------------------------------------------------------------


def test_no_overrides_uses_env_defaults(helper) -> None:
    cfg = helper(_skill())
    assert cfg["url"] == "http://env-default:8100/v1"
    assert cfg["model"] == "env-default-model"
    assert cfg["api_key"] == "env-default-key"
    assert cfg["using_custom_url"] is False
    assert cfg["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_skill_none_uses_env_defaults(helper) -> None:
    cfg = helper(None)
    assert cfg["url"] == "http://env-default:8100/v1"


# --- per-field overrides ---------------------------------------------------


def test_url_override_marks_custom(helper) -> None:
    cfg = helper(_skill(url="https://custom.example/v1"))
    assert cfg["url"] == "https://custom.example/v1"
    assert cfg["using_custom_url"] is True
    # enable_thinking=False is sent to all endpoints including custom URLs.
    # Operators who need to suppress it can set extra_body: null explicitly.
    assert cfg["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_model_override(helper) -> None:
    cfg = helper(_skill(model="claude-sonnet-4-6"))
    assert cfg["model"] == "claude-sonnet-4-6"


def test_direct_api_key_wins(helper) -> None:
    cfg = helper(_skill(api_key="sk-from-config"))
    assert cfg["api_key"] == "sk-from-config"


def test_api_key_env_resolves_through_environ(helper, monkeypatch) -> None:
    monkeypatch.setenv("MY_PROVIDER_KEY", "sk-from-env-var")
    cfg = helper(_skill(api_key_env="MY_PROVIDER_KEY"))
    assert cfg["api_key"] == "sk-from-env-var"


def test_api_key_env_missing_falls_back_to_default(helper, monkeypatch) -> None:
    monkeypatch.delenv("MISSING_KEY", raising=False)
    cfg = helper(_skill(api_key_env="MISSING_KEY"))
    assert cfg["api_key"] == "env-default-key"


def test_api_key_direct_beats_env_var(helper, monkeypatch) -> None:
    monkeypatch.setenv("PROVIDER_KEY", "sk-from-env")
    cfg = helper(_skill(api_key="sk-direct", api_key_env="PROVIDER_KEY"))
    assert cfg["api_key"] == "sk-direct"


# --- extra_body kill-switch ------------------------------------------------


def test_extra_body_user_override_wins(helper) -> None:
    """Even with a default URL, persona-set extra_body overrides."""
    cfg = helper(_skill(extra_body={"reasoning_effort": "high"}))
    assert cfg["extra_body"] == {"reasoning_effort": "high"}


def test_extra_body_user_override_wins_with_custom_url(helper) -> None:
    """User can opt back into thinking-disable on a custom URL."""
    cfg = helper(_skill(
        url="https://custom.example/v1",
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    ))
    assert cfg["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
    assert cfg["using_custom_url"] is True


def test_extra_body_explicit_none_clears(helper) -> None:
    """Setting extra_body to None / falsy explicitly disables it."""
    cfg = helper(_skill(extra_body=None))
    assert cfg["extra_body"] is None


def test_default_endpoint_injects_thinking_false(helper) -> None:
    """No URL override + no extra_body override → safe to send the
    bundled-vLLM convention."""
    cfg = helper(_skill())
    assert cfg["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


# --- mixed config ---------------------------------------------------------


def test_custom_url_with_default_model_and_key(helper) -> None:
    """Documented use case: 'rely on env key + env model but use a
    different URL'."""
    cfg = helper(_skill(url="https://gateway.example/v1"))
    assert cfg["url"] == "https://gateway.example/v1"
    assert cfg["model"] == "env-default-model"
    assert cfg["api_key"] == "env-default-key"
    assert cfg["using_custom_url"] is True
    assert cfg["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_full_persona_override(helper) -> None:
    cfg = helper(_skill(
        url="https://anthropic.example/v1",
        model="claude-opus-4-7",
        api_key="sk-anthropic",
        extra_body={"max_tokens_to_sample": 4096},
    ))
    assert cfg["url"] == "https://anthropic.example/v1"
    assert cfg["model"] == "claude-opus-4-7"
    assert cfg["api_key"] == "sk-anthropic"
    assert cfg["extra_body"] == {"max_tokens_to_sample": 4096}
    assert cfg["using_custom_url"] is True


# --- empty / falsy edge cases ---------------------------------------------


def test_empty_string_url_does_not_count_as_custom(helper) -> None:
    """An explicit empty string in config shouldn't flip on the kill-switch."""
    cfg = helper(_skill(url=""))
    # Falsy URL → falls through to env default
    assert cfg["url"] == "http://env-default:8100/v1"
    assert cfg["using_custom_url"] is False
    assert cfg["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
