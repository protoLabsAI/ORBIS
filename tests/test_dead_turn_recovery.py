"""Tests for #694: a completion that produces neither text nor a tool call
(truncated tool-call stream, reasoning-only response) speaks one canned
recovery line instead of a silent turn."""

from __future__ import annotations

import pytest
from pipecat.frames.frames import LLMTextFrame, TTSSpeakFrame
from pipecat.services.llm_service import LLMService
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
    # The REAL tool-call shape (live-QA 2026-08-18 false-fire): a tool-only
    # completion pushes no text frames — the base service awaits
    # run_function_calls at the end of the stream, and the actual InProgress
    # frames are broadcast from a created task AFTER _process_context
    # returns. Detection must ride run_function_calls, not frames.
    ran: list = []

    async def _base_run(self, function_calls):
        ran.append(function_calls)  # don't execute — no registry in tests

    monkeypatch.setattr(LLMService, "run_function_calls", _base_run)

    async def _calls(self, context):
        await service.run_function_calls([object()])  # one parsed tool call

    monkeypatch.setattr(OpenAILLMService, "_process_context", _calls)
    await service._process_context(object())
    assert ran, "super().run_function_calls must still execute"
    assert not [f for f in service._pushed if isinstance(f, TTSSpeakFrame)]


@pytest.mark.asyncio
async def test_empty_function_call_list_does_not_count(service, monkeypatch):
    async def _base_run(self, function_calls):
        pass

    monkeypatch.setattr(LLMService, "run_function_calls", _base_run)

    async def _calls(self, context):
        await service.run_function_calls([])  # base no-ops on empty too

    monkeypatch.setattr(OpenAILLMService, "_process_context", _calls)
    await service._process_context(object())
    speaks = [f for f in service._pushed if isinstance(f, TTSSpeakFrame)]
    assert len(speaks) == 1  # still a dead turn


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
