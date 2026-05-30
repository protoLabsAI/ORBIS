"""Tests for _resolve_fallback_llm + the LLMSwitcher failover wiring (orbis-1dd).

The fallback resolver returns None on the default path (no backup
configured) so run_bot builds the single-LLM pipeline unchanged, and a
populated dict (same shape as _resolve_skill_llm) when a backup is set
via persona.llm.fallback or the LLM_FALLBACK_* env knobs.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.fixture
def helper(monkeypatch):
    import app as _app
    monkeypatch.setattr(_app, "LLM_SERVED_NAME", "primary-model")
    monkeypatch.setattr(_app, "LLM_API_KEY", "primary-key")
    # Clear any ambient fallback env so tests are hermetic.
    for k in (
        "LLM_FALLBACK_URL", "LLM_FALLBACK_MODEL", "LLM_FALLBACK_API_KEY",
        "LLM_FALLBACK_API_KEY_ENV", "LLM_FALLBACK_PROVIDER",
    ):
        monkeypatch.delenv(k, raising=False)
    return _app._resolve_fallback_llm


def _skill(**llm) -> SimpleNamespace:
    return SimpleNamespace(llm=llm or None)


# --- not configured → None (default single-LLM path) -----------------------


def test_no_fallback_returns_none(helper) -> None:
    assert helper(_skill()) is None


def test_skill_none_returns_none(helper) -> None:
    assert helper(None) is None


def test_empty_fallback_dict_returns_none(helper) -> None:
    assert helper(_skill(fallback={})) is None


def test_fallback_without_url_returns_none(helper) -> None:
    # A fallback block with no url is meaningless — don't half-wire it.
    assert helper(_skill(fallback={"model": "x"})) is None


# --- persona.llm.fallback --------------------------------------------------


def test_persona_fallback_resolves(helper) -> None:
    cfg = helper(_skill(fallback={"url": "mlx://gemma", "model": "gemma-3n"}))
    assert cfg is not None
    assert cfg["url"] == "mlx://gemma"
    assert cfg["model"] == "gemma-3n"
    assert cfg["using_custom_url"] is True
    # custom URL → extra_body kill-switch defaults off
    assert cfg["extra_body"] is None


def test_persona_fallback_model_defaults_to_primary(helper) -> None:
    cfg = helper(_skill(fallback={"url": "mlx://gemma"}))
    assert cfg["model"] == "primary-model"


def test_persona_fallback_api_key_env(helper, monkeypatch) -> None:
    monkeypatch.setenv("MY_BACKUP_KEY", "secret-123")
    cfg = helper(_skill(fallback={"url": "https://b/v1", "api_key_env": "MY_BACKUP_KEY"}))
    assert cfg["api_key"] == "secret-123"


def test_persona_fallback_explicit_extra_body(helper) -> None:
    cfg = helper(_skill(fallback={"url": "https://b/v1", "extra_body": {"foo": 1}}))
    assert cfg["extra_body"] == {"foo": 1}


# --- LLM_FALLBACK_* env ----------------------------------------------------


def test_env_fallback_resolves(helper, monkeypatch) -> None:
    monkeypatch.setenv("LLM_FALLBACK_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "llama3")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "ollama")
    cfg = helper(_skill())
    assert cfg["url"] == "http://127.0.0.1:11434/v1"
    assert cfg["model"] == "llama3"
    assert cfg["provider"] == "ollama"


def test_persona_fallback_takes_precedence_over_env(helper, monkeypatch) -> None:
    monkeypatch.setenv("LLM_FALLBACK_URL", "http://env-backup/v1")
    cfg = helper(_skill(fallback={"url": "http://persona-backup/v1"}))
    assert cfg["url"] == "http://persona-backup/v1"


# --- failover strategy actually switches on error --------------------------


class _FakeService:
    """Minimal stand-in for an LLMService FrameProcessor — the switcher
    strategy calls ``queue_frame`` when it activates a service."""

    def __init__(self, name: str) -> None:
        self.name = name

    async def queue_frame(self, _frame) -> None:  # noqa: D401
        return None


@pytest.mark.asyncio
async def test_failover_strategy_switches_on_error() -> None:
    from pipecat.frames.frames import ErrorFrame
    from pipecat.pipeline.service_switcher import ServiceSwitcherStrategyFailover

    primary = _FakeService("primary")
    backup = _FakeService("backup")
    strat = ServiceSwitcherStrategyFailover(services=[primary, backup])
    assert strat.active_service is primary

    new_active = await strat.handle_error(ErrorFrame(error="boom", processor=primary))
    assert new_active is backup
    assert strat.active_service is backup


@pytest.mark.asyncio
async def test_failover_noop_with_single_service() -> None:
    from pipecat.frames.frames import ErrorFrame
    from pipecat.pipeline.service_switcher import ServiceSwitcherStrategyFailover

    only = _FakeService("only")
    strat = ServiceSwitcherStrategyFailover(services=[only])
    # No other service to switch to → returns None, stays put.
    assert await strat.handle_error(ErrorFrame(error="boom", processor=only)) is None
    assert strat.active_service is only
