"""Tests for the LLM error announcer (#576)."""

from __future__ import annotations

import asyncio
import time

import pytest
from pipecat.frames.frames import ErrorFrame, LLMTextFrame, TTSSpeakFrame
from pipecat.observers.base_observer import FramePushed
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import LLMService

from agent.llm_error_announcer import (
    _LINES,
    LLMErrorAnnouncer,
    classify_llm_error,
)

# isinstance is all the announcer checks — skip LLMService.__init__ (it wants
# a full service config), same spirit as the watchdog tests driving internals.
_LLM = LLMService.__new__(LLMService)


def _pushed(frame) -> FramePushed:
    return FramePushed(
        source=None,
        destination=None,
        frame=frame,
        direction=FrameDirection.UPSTREAM,
        timestamp=0,
    )


def _llm_error(msg: str = "Error during completion: boom", **kw) -> ErrorFrame:
    return ErrorFrame(error=msg, processor=_LLM, **kw)


def _make(**kw) -> tuple[LLMErrorAnnouncer, list]:
    a = LLMErrorAnnouncer(debounce_secs=0.01, throttle_secs=20.0, **kw)
    spoken: list = []

    async def emit(frame):
        spoken.append(frame)

    a.set_emitter(emit)
    return a, spoken


# --- classification -------------------------------------------------------


def test_classify_exception_status_code_wins() -> None:
    class _Exc(Exception):
        status_code = 401

    assert classify_llm_error("anything at all", _Exc()) == "auth"


def test_classify_exception_type_names() -> None:
    # Mirror the openai SDK type names without importing its constructors
    # (they require request/response objects).
    auth = type("AuthenticationError", (Exception,), {})()
    conn = type("APIConnectionError", (Exception,), {})()
    assert classify_llm_error("x", auth) == "auth"
    assert classify_llm_error("x", conn) == "unreachable"


def test_classify_text_markers() -> None:
    # Local endpoints (Ollama / LM Studio / MLX) surface plain strings.
    assert classify_llm_error("Connection refused") == "unreachable"
    assert classify_llm_error("Error code: 401 - invalid api key") == "auth"
    assert classify_llm_error("request timed out") == "unreachable"
    assert classify_llm_error("something exploded") == "generic"


# --- announce flow ---------------------------------------------------------


@pytest.mark.asyncio
async def test_announces_classified_line_after_debounce() -> None:
    a, spoken = _make()
    await a.on_push_frame(_pushed(_llm_error("Connection refused")))
    await asyncio.sleep(0.05)
    assert len(spoken) == 1
    assert isinstance(spoken[0], TTSSpeakFrame)
    assert spoken[0].text == _LINES["unreachable"]
    # Out-of-band: the LLM must not riff on its own error announcement.
    assert spoken[0].append_to_context is False


@pytest.mark.asyncio
async def test_real_output_cancels_pending_announce() -> None:
    # Single-LLM retry or LLMSwitcher failover recovered — stay quiet.
    a, spoken = _make()
    await a.on_push_frame(_pushed(_llm_error()))
    await a.on_push_frame(_pushed(LLMTextFrame(text="recovered")))
    await asyncio.sleep(0.05)
    assert spoken == []


@pytest.mark.asyncio
async def test_throttle_one_line_per_window() -> None:
    # A flapping endpoint errors repeatedly — one spoken line per window.
    a, spoken = _make()
    await a.on_push_frame(_pushed(_llm_error("Connection refused")))
    await asyncio.sleep(0.05)
    await a.on_push_frame(_pushed(_llm_error("Connection refused")))
    await asyncio.sleep(0.05)
    assert len(spoken) == 1


@pytest.mark.asyncio
async def test_same_frame_observed_per_hop_arms_once() -> None:
    # The observer sees the SAME ErrorFrame once per upstream hop; a later
    # hop of an already-cancelled error must not re-arm the announcement.
    a, spoken = _make()
    err = _llm_error()
    await a.on_push_frame(_pushed(err))
    await a.on_push_frame(_pushed(LLMTextFrame(text="recovered")))  # cancel
    await a.on_push_frame(_pushed(err))  # next hop, same frame
    await asyncio.sleep(0.05)
    assert spoken == []


@pytest.mark.asyncio
async def test_ignores_non_llm_fatal_and_tool_errors() -> None:
    a, spoken = _make()
    # Not from an LLM service (processor unset — e.g. a TTS failure).
    await a.on_push_frame(_pushed(ErrorFrame(error="tts blew up")))
    # Fatal → the app is going down; a canned line helps nobody.
    await a.on_push_frame(_pushed(_llm_error(fatal=True)))
    # Tool execution failed, not the LLM — the alive LLM narrates it itself.
    await a.on_push_frame(
        _pushed(_llm_error("Error executing function call [delegate_to]: boom"))
    )
    await asyncio.sleep(0.05)
    assert spoken == []


@pytest.mark.asyncio
async def test_disabled_stays_silent() -> None:
    a, spoken = _make(enabled=False)
    await a.on_push_frame(_pushed(_llm_error()))
    await asyncio.sleep(0.05)
    assert spoken == []


@pytest.mark.asyncio
async def test_fish_gets_softly_prefix() -> None:
    a, spoken = _make(tts_backend="fish")
    await a.on_push_frame(_pushed(_llm_error()))
    await asyncio.sleep(0.05)
    assert spoken[0].text.startswith("[softly] ")


@pytest.mark.asyncio
async def test_failover_reclassifies_the_line() -> None:
    # pipecat's failover switches the active service but does NOT retry the
    # failed generation — the erroring turn dies unanswered either way. When
    # app.py's on_service_switched handler notes the failover, the
    # announcement must say "switched to backup — ask again", not "check
    # settings". (Live-soaked 2026-07-11: switcher + observer see the same
    # ErrorFrame in the same instant.)
    a, spoken = _make()
    await a.on_push_frame(_pushed(_llm_error("Connection refused")))
    a.note_failover()
    await asyncio.sleep(0.05)
    assert len(spoken) == 1
    assert spoken[0].text == _LINES["failover"]


@pytest.mark.asyncio
async def test_stale_failover_does_not_reclassify() -> None:
    # A failover from a much earlier incident must not relabel a fresh,
    # unrelated LLM error (e.g. the backup itself dying minutes later).
    a, spoken = _make()
    a._failover_streak = 1
    a._last_failover_at = time.monotonic() - 60.0
    await a.on_push_frame(_pushed(_llm_error("Connection refused")))
    await asyncio.sleep(0.05)
    assert len(spoken) == 1
    assert spoken[0].text == _LINES["unreachable"]


@pytest.mark.asyncio
async def test_double_failover_means_all_dead_keeps_class_line() -> None:
    # Primary dies → failover → retry on backup → backup dies → second
    # failover (the member list wrapped). "Switched to my backup — ask me
    # that again" would be a lie; the class line is the honest one.
    a, spoken = _make()
    await a.on_push_frame(_pushed(_llm_error("Connection refused")))
    a.note_failover()
    a.note_failover()  # backup errored too, switcher wrapped around
    await asyncio.sleep(0.05)
    assert len(spoken) == 1
    assert spoken[0].text == _LINES["unreachable"]
