"""Tests for #694: a completion that produces neither text nor a tool call
(truncated tool-call stream, reasoning-only response) speaks one canned
recovery line instead of a silent turn."""

from __future__ import annotations

import pytest
from pipecat.frames.frames import (
    FunctionCallInProgressFrame,
    LLMTextFrame,
    TTSSpeakFrame,
)
from pipecat.services.openai.llm import OpenAILLMService

from voice.llm.guarded import _RECOVERY_LINE, GuardedOpenAILLMService


@pytest.fixture
def service(monkeypatch):
    svc = GuardedOpenAILLMService(api_key="test", model="test-model")
    pushed: list = []

    async def _capture(self, frame, direction=None):
        # Bypass FrameProcessor plumbing (no pipeline linkage in tests) but
        # keep the guarded class's own push_frame logic in the path by
        # patching one level down.
        pushed.append(frame)

    monkeypatch.setattr(OpenAILLMService, "push_frame", _capture)
    svc._pushed = pushed
    return svc


@pytest.mark.asyncio
async def test_empty_completion_speaks_recovery(service, monkeypatch):
    async def _silent(self, context):
        return  # stream produced nothing (truncated tool call dropped)

    monkeypatch.setattr(OpenAILLMService, "_process_context", _silent)
    await service._process_context(object())
    speaks = [f for f in service._pushed if isinstance(f, TTSSpeakFrame)]
    assert len(speaks) == 1
    assert speaks[0].text == _RECOVERY_LINE


@pytest.mark.asyncio
async def test_text_output_suppresses_recovery(service, monkeypatch):
    async def _talks(self, context):
        await service.push_frame(LLMTextFrame(text="hello there"))

    monkeypatch.setattr(OpenAILLMService, "_process_context", _talks)
    await service._process_context(object())
    assert not [f for f in service._pushed if isinstance(f, TTSSpeakFrame)]


@pytest.mark.asyncio
async def test_function_call_suppresses_recovery(service, monkeypatch):
    async def _calls(self, context):
        await service.push_frame(
            FunctionCallInProgressFrame(
                function_name="delegate_to", tool_call_id="t1",
                arguments="{}", cancel_on_interruption=False,
            )
        )

    monkeypatch.setattr(OpenAILLMService, "_process_context", _calls)
    await service._process_context(object())
    assert not [f for f in service._pushed if isinstance(f, TTSSpeakFrame)]


@pytest.mark.asyncio
async def test_errors_propagate_without_recovery(service, monkeypatch):
    async def _boom(self, context):
        raise RuntimeError("stream died")

    monkeypatch.setattr(OpenAILLMService, "_process_context", _boom)
    with pytest.raises(RuntimeError):
        await service._process_context(object())
    # Errored completions are the failover/announcer's job, not ours.
    assert not [f for f in service._pushed if isinstance(f, TTSSpeakFrame)]


@pytest.mark.asyncio
async def test_empty_text_frames_do_not_count_as_output(service, monkeypatch):
    async def _empty_text(self, context):
        await service.push_frame(LLMTextFrame(text=""))

    monkeypatch.setattr(OpenAILLMService, "_process_context", _empty_text)
    await service._process_context(object())
    speaks = [f for f in service._pushed if isinstance(f, TTSSpeakFrame)]
    assert len(speaks) == 1
