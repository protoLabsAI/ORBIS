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
    # The resolver reads these at call time. conftest.py keeps the user's
    # runtime .env out of the suite entirely, so they can't leak in from a
    # developer's machine — but a *shell* env still can, and these tests
    # pin defaults, so clear them explicitly.
    for var in (
        "LLM_ROUTER_MODEL", "LLM_CONTENT_MODEL",
        "LLM_MICRO_URL", "LLM_MICRO_MODEL", "LLM_MICRO_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
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


def test_skill_none_uses_env_defaults(helper) -> None:
    cfg = helper(None)
    assert cfg["url"] == "http://env-default:8100/v1"


def test_no_llm_configured_warns_loudly(helper, monkeypatch, caplog) -> None:
    # No llm block + LLM_URL is the built-in placeholder → the orb has no
    # brain. Must WARN so the log isn't a mystery (reachable if setup was
    # marked done with no llm block).
    import logging
    import app as _app
    monkeypatch.setattr(_app, "LLM_URL", _app._LLM_URL_DEFAULT)
    with caplog.at_level(logging.WARNING):
        cfg = helper(_skill())
    assert cfg["url"] == _app._LLM_URL_DEFAULT
    assert any("no LLM configured" in r.message for r in caplog.records)


def test_configured_llm_does_not_warn(helper, monkeypatch, caplog) -> None:
    import logging
    import app as _app
    monkeypatch.setattr(_app, "LLM_URL", _app._LLM_URL_DEFAULT)
    with caplog.at_level(logging.WARNING):
        helper(_skill(url="https://gateway.example/v1"))
    assert not any("no LLM configured" in r.message for r in caplog.records)


# --- per-field overrides ---------------------------------------------------


def test_url_override(helper) -> None:
    cfg = helper(_skill(url="https://custom.example/v1"))
    assert cfg["url"] == "https://custom.example/v1"


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


def test_api_key_env_missing_falls_back_to_default_LOUDLY(
    helper, monkeypatch, caplog,
) -> None:
    # Behavior unchanged (still falls back to the placeholder), but the fall
    # back must now WARN — silently resolving api_key_env to "not-needed" on a
    # Finder launch was a 401-with-no-hint footgun.
    import logging
    monkeypatch.delenv("MISSING_KEY", raising=False)
    with caplog.at_level(logging.WARNING):
        cfg = helper(_skill(api_key_env="MISSING_KEY"))
    assert cfg["api_key"] == "env-default-key"
    assert any(
        "MISSING_KEY" in r.message and "unset" in r.message for r in caplog.records
    ), "a missing api_key_env var must be warned about, not swallowed"


def test_api_key_direct_beats_env_var(helper, monkeypatch) -> None:
    monkeypatch.setenv("PROVIDER_KEY", "sk-from-env")
    cfg = helper(_skill(api_key="sk-direct", api_key_env="PROVIDER_KEY"))
    assert cfg["api_key"] == "sk-direct"


# --- extra_body / enable_thinking is an ENDPOINT capability ----------------
#
# `chat_template_kwargs.enable_thinking=False` stops the Qwen-family models
# behind protolabs/* from streaming chain-of-thought into `content`. ORBIS
# SPEAKS `content`, so getting this wrong makes the orb narrate its own
# tool-call planning out loud. It's a property of the endpoint, so it must
# be decided by the resolved URL — never by where that URL came from.

_THINKING_OFF = {"chat_template_kwargs": {"enable_thinking": False}}
_GATEWAY = "https://api.proto-labs.ai/v1"


def test_gateway_url_from_config_suppresses_thinking(helper) -> None:
    """The shipped config/orbis.yaml names the gateway URL — that must
    still send enable_thinking=False."""
    cfg = helper(_skill(url=_GATEWAY))
    assert cfg["extra_body"] == _THINKING_OFF


def test_gateway_url_from_env_suppresses_thinking(helper, monkeypatch) -> None:
    import app as _app
    monkeypatch.setattr(_app, "LLM_URL", _GATEWAY)
    cfg = helper(_skill())
    assert cfg["extra_body"] == _THINKING_OFF


def test_provenance_does_not_change_behavior(helper, monkeypatch) -> None:
    """REGRESSION: the same URL must resolve identically whether it came
    from persona.llm.url or the LLM_URL env.

    The old rule was `using_custom_url = bool(skill_llm.get("url"))`, so
    naming the gateway in orbis.yaml silently dropped enable_thinking (and
    kept role:developer) while the identical URL in env did not. The orb
    spoke its reasoning aloud, out of the box.
    """
    from_config = helper(_skill(url=_GATEWAY))
    import app as _app
    monkeypatch.setattr(_app, "LLM_URL", _GATEWAY)
    from_env = helper(_skill())
    assert from_config["url"] == from_env["url"] == _GATEWAY
    assert from_config["extra_body"] == from_env["extra_body"] == _THINKING_OFF


def test_third_party_url_sends_no_extra_body(helper) -> None:
    """OpenAI/Anthropic/Groq/… 400 on unknown body fields — send nothing.
    True regardless of which model is named."""
    for url in (
        "https://api.openai.com/v1",
        "https://api.anthropic.com/v1",
        "https://api.groq.com/openai/v1",
    ):
        assert helper(_skill(url=url))["extra_body"] is None, url


def test_provider_vllm_suppresses_thinking_on_any_url(helper) -> None:
    """A self-hosted vLLM speaks the Qwen dialect at an arbitrary URL —
    `provider` is the escape hatch."""
    cfg = helper(_skill(url="http://192.168.1.50:8000/v1", provider="vllm"))
    assert cfg["extra_body"] == _THINKING_OFF


def test_extra_body_user_override_wins(helper) -> None:
    """An explicit persona-set extra_body beats endpoint detection."""
    cfg = helper(_skill(url=_GATEWAY, extra_body={"reasoning_effort": "high"}))
    assert cfg["extra_body"] == {"reasoning_effort": "high"}


def test_extra_body_user_override_wins_on_third_party(helper) -> None:
    """User can opt back into thinking-disable on an unrecognized URL."""
    cfg = helper(_skill(url="https://custom.example/v1", extra_body=_THINKING_OFF))
    assert cfg["extra_body"] == _THINKING_OFF


def test_extra_body_explicit_none_clears(helper) -> None:
    """Setting extra_body to None / falsy explicitly disables it, even on
    an endpoint that would otherwise get it."""
    cfg = helper(_skill(url=_GATEWAY, extra_body=None))
    assert cfg["extra_body"] is None


# --- mixed config ---------------------------------------------------------


def test_custom_url_with_default_model_and_key(helper) -> None:
    """Documented use case: 'rely on env key + env model but use a
    different URL'."""
    cfg = helper(_skill(url="https://gateway.example/v1"))
    assert cfg["url"] == "https://gateway.example/v1"
    assert cfg["model"] == "env-default-model"
    assert cfg["api_key"] == "env-default-key"
    assert cfg["extra_body"] is None


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


# --- empty / falsy edge cases ---------------------------------------------


def test_empty_string_url_falls_through_to_env(helper) -> None:
    cfg = helper(_skill(url=""))
    assert cfg["url"] == "http://env-default:8100/v1"


# --- micro-task model (orbis-3au) -----------------------------------------


def test_micro_model_defaults_to_model(helper) -> None:
    cfg = helper(_skill())
    assert cfg["micro_model"] == cfg["model"]  # default = persona model


def test_micro_model_from_persona(helper) -> None:
    cfg = helper(_skill(model="protolabs/fast", micro_model="protolabs/micro"))
    assert cfg["micro_model"] == "protolabs/micro"


def test_micro_model_from_env(helper, monkeypatch) -> None:
    monkeypatch.setenv("LLM_MICRO_MODEL", "tiny-1")
    cfg = helper(_skill())
    assert cfg["micro_model"] == "tiny-1"


def test_persona_micro_model_beats_env(helper, monkeypatch) -> None:
    monkeypatch.setenv("LLM_MICRO_MODEL", "tiny-env")
    cfg = helper(_skill(micro_model="tiny-persona"))
    assert cfg["micro_model"] == "tiny-persona"
