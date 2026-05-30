"""Tests for MicroAckInjector — verbosity gating, telemetry span,
construction-time defaults.

Avoids exercising the full pipeline (which needs a started FrameProcessor
and a downstream consumer). Instead tests the gate logic at the timer-fire
boundary by stubbing push_frame.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent.filler import Verbosity
from agent.micro_ack import MicroAckInjector
from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection


@pytest.fixture
def injector_factory():
    """Build an injector with a synchronous fire path stand-in.

    The real _fire_after_delay sleeps for self._trigger_s; we shrink it
    to ~0 so tests run instantly. We also intercept push_frame so we can
    assert on emitted frames.
    """
    def _build(
        tts_backend: str = "kokoro",
        verbosity_getter=None,
        enabled: bool = True,
    ):
        inj = MicroAckInjector(
            tts_backend=tts_backend,
            trigger_ms=1,  # ~immediate
            min_interval_secs=0.0,
            enabled=enabled,
            verbosity_getter=verbosity_getter,
        )
        inj._pushed_frames: list[Any] = []

        async def _capture(frame, direction=None):
            inj._pushed_frames.append(frame)

        inj.push_frame = _capture  # type: ignore[method-assign]
        return inj
    return _build


@pytest.mark.asyncio
async def test_emits_when_no_verbosity_gate(injector_factory) -> None:
    """Default (None getter) preserves back-compat: emits unconditionally."""
    inj = injector_factory(verbosity_getter=None)
    await inj._fire_after_delay()
    assert len(inj._pushed_frames) == 1


@pytest.mark.asyncio
async def test_emits_when_verbosity_brief(injector_factory) -> None:
    inj = injector_factory(verbosity_getter=lambda: Verbosity.BRIEF)
    await inj._fire_after_delay()
    assert len(inj._pushed_frames) == 1


@pytest.mark.asyncio
async def test_emits_when_verbosity_narrated(injector_factory) -> None:
    inj = injector_factory(verbosity_getter=lambda: Verbosity.NARRATED)
    await inj._fire_after_delay()
    assert len(inj._pushed_frames) == 1


@pytest.mark.asyncio
async def test_emits_when_verbosity_chatty(injector_factory) -> None:
    inj = injector_factory(verbosity_getter=lambda: Verbosity.CHATTY)
    await inj._fire_after_delay()
    assert len(inj._pushed_frames) == 1


@pytest.mark.asyncio
async def test_suppressed_when_verbosity_silent(injector_factory) -> None:
    """The R2 fix — SILENT must skip emission."""
    inj = injector_factory(verbosity_getter=lambda: Verbosity.SILENT)
    await inj._fire_after_delay()
    assert inj._pushed_frames == []


@pytest.mark.asyncio
async def test_verbosity_check_is_live(injector_factory) -> None:
    """Switch verbosity between two fires; the second fire honors the flip."""
    state = {"v": Verbosity.BRIEF}
    inj = injector_factory(verbosity_getter=lambda: state["v"])

    await inj._fire_after_delay()
    assert len(inj._pushed_frames) == 1

    state["v"] = Verbosity.SILENT
    await inj._fire_after_delay()
    assert len(inj._pushed_frames) == 1, "SILENT must suppress the second emit"

    state["v"] = Verbosity.NARRATED
    await inj._fire_after_delay()
    assert len(inj._pushed_frames) == 2, "flipping back to non-SILENT re-enables"


@pytest.mark.asyncio
async def test_suppressed_when_bot_speaking(injector_factory) -> None:
    """Existing guard preserved — bot-is-speaking stops emit even if
    verbosity allows."""
    inj = injector_factory(verbosity_getter=lambda: Verbosity.BRIEF)
    inj._bot_speaking = True
    await inj._fire_after_delay()
    assert inj._pushed_frames == []


@pytest.mark.asyncio
async def test_verbosity_getter_exception_does_not_crash_timer(injector_factory) -> None:
    """A getter that raises must not crash the background task. We
    treat the exception as non-SILENT (best-effort emit) and continue,
    so a torn-down user_state during shutdown can't silently swallow
    every filler."""
    def _broken_getter():
        raise RuntimeError("user_state torn down")

    inj = injector_factory(verbosity_getter=_broken_getter)
    await inj._fire_after_delay()
    # Treated as non-SILENT — frame still emits.
    assert len(inj._pushed_frames) == 1


@pytest.mark.asyncio
async def test_phrases_match_backend(injector_factory) -> None:
    """Fish backend gets the [softly]-prefixed acks; others plain. Assert
    against the module's pools so expanding them never breaks this test."""
    from agent.micro_ack import _FISH_ACKS, _KOKORO_ACKS, _PLAIN_ACKS

    fish = injector_factory(tts_backend="fish")
    await fish._fire_after_delay()
    assert fish._pushed_frames[0].text.startswith("[softly]")
    assert fish._pushed_frames[0].text in set(_FISH_ACKS)

    kokoro = injector_factory(tts_backend="kokoro")
    await kokoro._fire_after_delay()
    assert not kokoro._pushed_frames[0].text.startswith("[")
    assert kokoro._pushed_frames[0].text in set(_KOKORO_ACKS)

    openai = injector_factory(tts_backend="openai")
    await openai._fire_after_delay()
    assert openai._pushed_frames[0].text in set(_PLAIN_ACKS)


@pytest.mark.asyncio
async def test_pick_avoids_immediate_repeats(injector_factory) -> None:
    inj = injector_factory(tts_backend="kokoro")
    picks = [inj._pick() for _ in range(60)]
    repeats = sum(1 for i in range(1, len(picks)) if picks[i] == picks[i - 1])
    assert repeats == 0  # never the same ack twice in a row
    assert len(set(picks)) >= 5  # genuine variety from the pool


@pytest.mark.asyncio
async def test_emits_through_tracing_span(injector_factory, monkeypatch) -> None:
    """Telemetry gap fix — emission goes through a tracing.span call."""
    from agent import tracing as tracing_mod

    span_calls: list[tuple[str, dict]] = []

    class _DummySpan:
        def __init__(self, name: str):
            self.name = name
            self.updates: dict = {}
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False
        def update(self, **kwargs):
            self.updates.update(kwargs)
            span_calls.append((self.name, dict(self.updates)))

    monkeypatch.setattr(tracing_mod, "span", lambda name, **kw: _DummySpan(name))

    inj = injector_factory()
    await inj._fire_after_delay()
    assert any(name == "filler.micro_ack" for name, _ in span_calls), \
        "expected a filler.micro_ack span"
    micro_calls = [u for n, u in span_calls if n == "filler.micro_ack"]
    assert any("output" in u for u in micro_calls), \
        "span should record the emitted phrase as output"


@pytest.mark.asyncio
async def test_disabled_means_no_emit() -> None:
    """The boolean enabled toggle still wins — verbosity gate only adds
    a second axis."""
    inj = MicroAckInjector(
        tts_backend="kokoro",
        trigger_ms=1,
        min_interval_secs=0.0,
        enabled=False,
        verbosity_getter=lambda: Verbosity.BRIEF,
    )
    inj._arm_timer()
    # _arm_timer should no-op on disabled.
    assert inj._timer is None


@pytest.mark.asyncio
async def test_llm_text_cancels_pending_ack(injector_factory) -> None:
    """Once real LLM text is flowing, an ack would queue behind the reply."""
    inj = injector_factory()
    inj._arm_timer()
    assert inj._timer is not None

    await inj.process_frame(LLMTextFrame("hello"), FrameDirection.DOWNSTREAM)

    assert inj._llm_responding is True
    assert inj._timer is None
    assert len(inj._pushed_frames) == 1
    assert isinstance(inj._pushed_frames[0], LLMTextFrame)


@pytest.mark.asyncio
async def test_llm_response_end_allows_future_ack(injector_factory) -> None:
    inj = injector_factory()
    await inj.process_frame(LLMTextFrame("hello"), FrameDirection.DOWNSTREAM)
    assert inj._llm_responding is True

    await inj.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)

    assert inj._llm_responding is False


@pytest.mark.asyncio
async def test_post_bot_grace_suppresses_echo_retrigger(injector_factory) -> None:
    """Native speaker bleed can create a fake UserStopped after bot audio."""
    inj = injector_factory()
    await inj.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)

    await inj.process_frame(UserStoppedSpeakingFrame(), FrameDirection.UPSTREAM)

    assert inj._timer is None
