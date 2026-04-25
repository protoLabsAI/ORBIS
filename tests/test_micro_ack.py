"""Tests for MicroAckInjector — verbosity gating, telemetry span,
construction-time defaults.

Avoids exercising the full pipeline (which needs a started FrameProcessor
and a downstream consumer). Instead tests the gate logic at the timer-fire
boundary by stubbing push_frame.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent.filler import Verbosity
from agent.micro_ack import MicroAckInjector


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
async def test_phrases_match_backend(injector_factory) -> None:
    """Fish backend gets the [softly]-prefixed acks; others plain."""
    fish = injector_factory(tts_backend="fish")
    await fish._fire_after_delay()
    assert fish._pushed_frames[0].text.startswith("[softly]")

    kokoro = injector_factory(tts_backend="kokoro")
    await kokoro._fire_after_delay()
    assert not kokoro._pushed_frames[0].text.startswith("[")


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
