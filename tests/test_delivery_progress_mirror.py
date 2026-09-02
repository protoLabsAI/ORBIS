"""Tests for the delegation-progress mirror channel on DeliveryController.

When a delegate's progress narration is spoken via ``speak_now()``, the
controller also broadcasts a typed ``delegation-progress`` server
message so the SPA can render the same text under the "Asking ava…"
pill (see web/src/voice/VoiceStateBridge.tsx). The verbal channel is
the source of truth — the message channel is a best-effort mirror, so
emitter exceptions must NOT bubble out of speak_now.
"""

from __future__ import annotations

import pytest
from pipecat.frames.frames import TTSSpeakFrame

from agent.delivery import DeliveryController


@pytest.fixture
def ctrl_with_emitters():
    """Build a controller with both frame and message emitters wired
    to capture buffers, mirroring how app.py sets it up."""
    ctrl = DeliveryController()
    frames: list = []
    messages: list[dict] = []

    async def _frame_emitter(frame):
        frames.append(frame)

    async def _msg_emitter(msg):
        messages.append(msg)

    ctrl.set_emitter(_frame_emitter)
    ctrl.set_message_emitter(_msg_emitter)
    return ctrl, frames, messages


@pytest.mark.asyncio
async def test_speak_now_mirrors_to_message_channel(ctrl_with_emitters):
    """The verbal channel and the message channel both fire — the SPA
    rendering the subtitle and the TTS speaking the line are paired."""
    ctrl, frames, messages = ctrl_with_emitters
    await ctrl.speak_now("still working on it", source="ava")

    # Verbal: TTSSpeakFrame with the source-attributed phrase
    assert len(frames) == 1
    assert isinstance(frames[0], TTSSpeakFrame)
    assert frames[0].text == "ava says — still working on it"
    assert frames[0].append_to_context is False

    # Message: typed event with raw text + source preserved
    assert len(messages) == 1
    assert messages[0] == {
        "type": "delegation-progress",
        "source": "ava",
        "text": "still working on it",
    }


@pytest.mark.asyncio
async def test_speak_now_works_without_message_emitter():
    """Backwards-compat — controllers built before the mirror was wired
    must still work. The frame emitter alone is enough."""
    ctrl = DeliveryController()
    frames: list = []

    async def _frame_emitter(frame):
        frames.append(frame)

    ctrl.set_emitter(_frame_emitter)
    # No set_message_emitter call.
    await ctrl.speak_now("on it", source="ava")

    assert len(frames) == 1
    assert isinstance(frames[0], TTSSpeakFrame)


@pytest.mark.asyncio
async def test_message_emitter_failure_doesnt_crash_speak_now(ctrl_with_emitters):
    """If the RTVI channel is broken (data channel closed mid-call,
    serialization bug), the verbal path must still land. We log + drop."""
    ctrl, frames, _messages = ctrl_with_emitters

    async def _broken(_msg):
        raise RuntimeError("data channel closed")

    ctrl.set_message_emitter(_broken)

    # Should NOT raise
    await ctrl.speak_now("trying again", source="ava")

    # Verbal channel still fired
    assert len(frames) == 1
    assert frames[0].text == "ava says — trying again"


@pytest.mark.asyncio
async def test_speak_now_without_source(ctrl_with_emitters):
    """Source is optional — when omitted the verbal path drops the
    attribution prefix; the message channel still mirrors with source=None
    so the UI can choose whether to label it."""
    ctrl, frames, messages = ctrl_with_emitters
    await ctrl.speak_now("plain status update")

    assert frames[0].text == "plain status update"
    assert messages[0]["source"] is None
    assert messages[0]["text"] == "plain status update"


@pytest.mark.asyncio
async def test_deliver_does_not_use_message_channel(ctrl_with_emitters):
    """The mirror is scoped to in-flight progress narration only —
    final results going through the deliver() path don't fire the
    typed event. (The SPA already gets final results via the LLM
    response stream + TTS.)"""
    ctrl, frames, messages = ctrl_with_emitters
    await ctrl.deliver("done", source="ava")

    # Verbal path may emit (it does for default ACTIVE priority? actually
    # ACTIVE -> WHEN_ASKED policy which queues, not emits). Either way,
    # message channel must stay empty — the mirror is for speak_now only.
    assert messages == []


@pytest.mark.asyncio
async def test_structured_delegate_event_is_forwarded_without_tool_secrets(
    ctrl_with_emitters,
):
    ctrl, _frames, messages = ctrl_with_emitters
    await ctrl.note_delegate_event({
        "type": "delegate.tool",
        "delegate_id": "hub",
        "task_id": "task-1",
        "name": "web_search",
        "status": "started",
    })

    assert messages == [{
        "type": "delegate.tool",
        "delegate_id": "hub",
        "task_id": "task-1",
        "name": "web_search",
        "status": "started",
    }]


@pytest.mark.asyncio
async def test_unknown_delegate_event_is_not_forwarded(ctrl_with_emitters):
    ctrl, _frames, messages = ctrl_with_emitters
    await ctrl.note_delegate_event({"type": "delegate.secret", "token": "nope"})
    assert messages == []


@pytest.mark.asyncio
async def test_legacy_progress_payload_is_bounded(ctrl_with_emitters):
    ctrl, _frames, messages = ctrl_with_emitters
    await ctrl.note_progress("é" * 10_000, source="a" * 1_000)

    assert len(messages[0]["text"].encode()) <= 1024
    assert len(messages[0]["source"].encode()) <= 256


def test_delegate_event_scope_is_invalidated_by_barge_and_release():
    ctrl = DeliveryController()
    first = ctrl.begin_delegate_event_scope()
    second = ctrl.begin_delegate_event_scope()
    assert ctrl.owns_delegate_event_scope(first)
    assert ctrl.owns_delegate_event_scope(second)

    ctrl.end_delegate_event_scope(first)
    assert not ctrl.owns_delegate_event_scope(first)
    assert ctrl.owns_delegate_event_scope(second)

    ctrl.bump_barge()
    assert not ctrl.owns_delegate_event_scope(second)
