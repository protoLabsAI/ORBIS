"""Tests for _resolve_fallback_llm + the LLMSwitcher failover wiring (orbis-1dd).

The fallback resolver returns None on the default path (no backup
configured) so run_bot builds the single-LLM pipeline unchanged, and a
populated dict (same shape as _resolve_skill_llm) when a backup is set
via persona.llm.fallback or the LLM_FALLBACK_* env knobs.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from pipecat.processors.frame_processor import FrameProcessor


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
    # mlx:// isn't the Qwen-dialect gateway → no extra_body. (The MLX
    # adapter resolves `think` itself — see _resolve_ollama_think.)
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


# --- failover strategy + real LLMSwitcher error handling -------------------


class _FakeService(FrameProcessor):
    """FrameProcessor-compatible service without starting worker tasks."""

    def __init__(self, name: str, *, is_usable: bool = True) -> None:
        super().__init__(name=name, enable_direct_mode=True)
        self._is_usable = is_usable
        self.queued = []

    async def queue_frame(self, frame, *args, **kwargs) -> None:
        self.queued.append(frame)

    def _sync_registered_tool_handlers(self, _tools) -> None:
        return None


class _FrameSink(FrameProcessor):
    def __init__(self) -> None:
        super().__init__(enable_direct_mode=True)
        self.frames = []

    async def queue_frame(self, frame, *args, **kwargs) -> None:
        self.frames.append(frame)


def _error(service, category):
    from pipecat.frames.frames import ErrorFrame

    return ErrorFrame(error="boom", processor=service, category=category)


def _arm_retry(strat):
    retries = []

    async def retry() -> None:
        retries.append(True)

    strat.set_retry_callback(retry)
    return retries


@pytest.mark.asyncio
async def test_failover_strategy_switches_on_recoverable_error() -> None:
    from pipecat.utils.errors import ErrorCategory
    from voice.llm.failover import OrbisLLMFailoverStrategy

    primary = _FakeService("primary")
    backup = _FakeService("backup")
    strat = OrbisLLMFailoverStrategy(services=[primary, backup])
    retries = _arm_retry(strat)
    assert strat.active_service is primary

    new_active = await strat.handle_error(_error(primary, ErrorCategory.CONNECTIVITY))
    assert new_active is backup
    assert strat.active_service is backup
    assert retries == [True]


@pytest.mark.asyncio
async def test_failover_noop_with_single_service() -> None:
    from pipecat.utils.errors import ErrorCategory
    from voice.llm.failover import OrbisLLMFailoverStrategy

    only = _FakeService("only")
    strat = OrbisLLMFailoverStrategy(services=[only])
    retries = _arm_retry(strat)
    # No other service to switch to → returns None, stays put.
    assert await strat.handle_error(_error(only, ErrorCategory.CONNECTIVITY)) is None
    assert strat.active_service is only
    assert retries == []


@pytest.mark.asyncio
async def test_failover_skips_unusable_backup() -> None:
    from pipecat.utils.errors import ErrorCategory
    from voice.llm.failover import OrbisLLMFailoverStrategy

    primary = _FakeService("primary")
    dead_backup = _FakeService("dead-backup", is_usable=False)
    healthy_backup = _FakeService("healthy-backup")
    strat = OrbisLLMFailoverStrategy(
        services=[primary, dead_backup, healthy_backup]
    )
    _arm_retry(strat)

    new_active = await strat.handle_error(_error(primary, ErrorCategory.SERVER))
    assert new_active is healthy_backup
    assert strat.active_service is healthy_backup


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "category_name",
    ["APPLICATION", "INVALID_REQUEST", "UNKNOWN"],
)
async def test_still_usable_nonavailability_errors_do_not_fail_over(
    category_name,
) -> None:
    from pipecat.utils.errors import ErrorCategory
    from voice.llm.failover import OrbisLLMFailoverStrategy

    primary = _FakeService("primary")
    backup = _FakeService("backup")
    strat = OrbisLLMFailoverStrategy(services=[primary, backup])
    retries = _arm_retry(strat)

    switched = await strat.handle_error(_error(primary, ErrorCategory[category_name]))
    assert switched is None
    assert strat.active_service is primary
    assert retries == []


@pytest.mark.asyncio
@pytest.mark.parametrize("category_name", ["RATE_LIMIT", "QUOTA"])
async def test_capacity_errors_fail_over(category_name) -> None:
    from pipecat.utils.errors import ErrorCategory
    from voice.llm.failover import OrbisLLMFailoverStrategy

    primary = _FakeService("primary")
    backup = _FakeService("backup")
    strat = OrbisLLMFailoverStrategy(services=[primary, backup])
    retries = _arm_retry(strat)

    switched = await strat.handle_error(_error(primary, ErrorCategory[category_name]))
    assert switched is backup
    assert retries == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("category_name", ["AUTHENTICATION", "AUTHORIZATION"])
async def test_permanently_unusable_service_uses_pipecat_failover(
    category_name,
) -> None:
    from pipecat.utils.errors import ErrorCategory
    from voice.llm.failover import OrbisLLMFailoverStrategy

    primary = _FakeService("primary", is_usable=False)
    backup = _FakeService("backup")
    strat = OrbisLLMFailoverStrategy(services=[primary, backup])
    retries = _arm_retry(strat)

    switched = await strat.handle_error(_error(primary, ErrorCategory[category_name]))
    assert switched is backup
    assert strat.active_service is backup
    assert retries == [True]


@pytest.mark.asyncio
async def test_strategy_does_not_switch_without_retry_queue() -> None:
    from pipecat.utils.errors import ErrorCategory
    from voice.llm.failover import OrbisLLMFailoverStrategy

    primary = _FakeService("primary")
    backup = _FakeService("backup")
    strat = OrbisLLMFailoverStrategy(services=[primary, backup])

    switched = await strat.handle_error(_error(primary, ErrorCategory.CONNECTIVITY))
    assert switched is None
    assert strat.active_service is primary


@pytest.mark.asyncio
async def test_real_switcher_absorbs_one_failover_then_propagates_second_error() -> None:
    from pipecat.frames.frames import LLMContextFrame, LLMRunFrame
    from pipecat.processors.frame_processor import FrameDirection
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.utils.errors import ErrorCategory
    from voice.llm.failover import (
        OrbisLLMFailoverStrategy,
        make_orbis_llm_switcher,
        queue_failover_retry,
    )

    primary = _FakeService("primary")
    backup = _FakeService("backup")
    switcher = make_orbis_llm_switcher([primary, backup])
    assert isinstance(switcher.strategy, OrbisLLMFailoverStrategy)
    sink = _FrameSink()
    sink.link(switcher)
    retries = []
    failovers = []
    announcer_notes = []

    async def queue_retry(frame) -> None:
        retries.append(frame)

    async def retry() -> None:
        await queue_failover_retry(
            note_failover=lambda: announcer_notes.append(switcher.active_llm),
            queue_frame=queue_retry,
        )

    switcher.strategy.set_retry_callback(retry)

    @switcher.strategy.event_handler("on_service_switched")
    async def on_switched(_strategy, service) -> None:
        failovers.append(service)

    first = _error(primary, ErrorCategory.CONNECTIVITY)
    await switcher.push_frame(first, FrameDirection.UPSTREAM)
    await asyncio.sleep(0)  # Pipecat dispatches event handlers as tasks.

    assert switcher.active_llm is backup
    assert failovers == [backup]
    assert announcer_notes == [backup]
    assert len(retries) == 1
    assert isinstance(retries[0], LLMRunFrame)
    assert sink.frames == []  # recovered errors are absorbed

    # The user aggregator consumes LLMRunFrame and emits the same context.
    # That automatic retry context must not reset the current incident.
    context = LLMContext(messages=[])
    await switcher.process_frame(LLMContextFrame(context), FrameDirection.DOWNSTREAM)

    second = _error(backup, ErrorCategory.SERVER)
    await switcher.push_frame(second, FrameDirection.UPSTREAM)

    assert switcher.active_llm is backup  # no wrap back to the failed primary
    assert failovers == [backup]
    assert announcer_notes == [backup]
    assert len(retries) == 1
    assert sink.frames == [second]  # exhausted incidents stay observable

    # A genuinely new run gets a fresh budget and may try the old primary.
    await switcher.process_frame(LLMContextFrame(context), FrameDirection.DOWNSTREAM)
    third = _error(backup, ErrorCategory.CONNECTIVITY)
    await switcher.push_frame(third, FrameDirection.UPSTREAM)
    assert switcher.active_llm is primary
    assert len(retries) == 2


@pytest.mark.asyncio
async def test_real_switcher_propagates_when_no_backup_is_usable() -> None:
    from pipecat.processors.frame_processor import FrameDirection
    from pipecat.utils.errors import ErrorCategory
    from voice.llm.failover import make_orbis_llm_switcher

    primary = _FakeService("primary")
    backup = _FakeService("backup", is_usable=False)
    switcher = make_orbis_llm_switcher([primary, backup])
    retries = _arm_retry(switcher.strategy)
    sink = _FrameSink()
    sink.link(switcher)

    error = _error(primary, ErrorCategory.CONNECTIVITY)
    await switcher.push_frame(error, FrameDirection.UPSTREAM)

    assert switcher.active_llm is primary
    assert sink.frames == [error]
    assert retries == []


@pytest.mark.asyncio
async def test_real_switcher_absorbs_stock_permanent_failover() -> None:
    from pipecat.processors.frame_processor import FrameDirection
    from pipecat.utils.errors import ErrorCategory
    from voice.llm.failover import make_orbis_llm_switcher

    primary = _FakeService("primary", is_usable=False)
    backup = _FakeService("backup")
    switcher = make_orbis_llm_switcher([primary, backup])
    retries = _arm_retry(switcher.strategy)
    sink = _FrameSink()
    sink.link(switcher)

    error = _error(primary, ErrorCategory.AUTHENTICATION)
    await switcher.push_frame(error, FrameDirection.UPSTREAM)

    assert switcher.active_llm is backup
    assert sink.frames == []
    assert retries == [True]


@pytest.mark.asyncio
async def test_substantive_backup_output_clears_incident_attempts() -> None:
    from pipecat.frames.frames import LLMTextFrame
    from pipecat.processors.frame_processor import FrameDirection
    from pipecat.utils.errors import ErrorCategory
    from voice.llm.failover import make_orbis_llm_switcher

    primary = _FakeService("primary")
    backup = _FakeService("backup")
    switcher = make_orbis_llm_switcher([primary, backup])
    retries = _arm_retry(switcher.strategy)

    await switcher.push_frame(
        _error(primary, ErrorCategory.CONNECTIVITY), FrameDirection.UPSTREAM
    )
    assert switcher.active_llm is backup

    await switcher.push_frame(LLMTextFrame(text="recovered"), FrameDirection.DOWNSTREAM)
    await switcher.push_frame(
        _error(backup, ErrorCategory.CONNECTIVITY), FrameDirection.UPSTREAM
    )

    assert switcher.active_llm is primary
    assert retries == [True, True]


@pytest.mark.asyncio
async def test_real_invalid_request_marks_service_unusable_but_does_not_switch() -> None:
    from pipecat.frames.frames import ErrorFrame
    from pipecat.utils.errors import ErrorCategory
    from voice.llm.failover import make_orbis_llm_switcher

    primary = _FakeService("primary")
    backup = _FakeService("backup")
    switcher = make_orbis_llm_switcher([primary, backup])
    retries = _arm_retry(switcher.strategy)
    sink = _FrameSink()
    sink.link(switcher)
    error = ErrorFrame(
        error="unknown model", processor=primary, category=ErrorCategory.INVALID_REQUEST
    )

    await primary.push_error_frame(error)

    assert primary.is_usable is False
    assert switcher.active_llm is primary
    assert retries == []
    assert len(sink.frames) == 1
    assert sink.frames[0].processor is switcher
