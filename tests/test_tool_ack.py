"""Opening acks must not stack behind a preamble, reply, or interruption."""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from agent.tool_ack import ToolAckGate
from pipecat.frames.frames import (
    EndFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection


@pytest.fixture
def gate():
    gate = ToolAckGate()
    gate.passed = []

    async def capture(frame, direction):
        gate.passed.append(frame)

    gate.push_frame = capture
    return gate


async def send(gate, *frames):
    for frame in frames:
        await gate.process_frame(frame, FrameDirection.DOWNSTREAM)


@pytest.mark.asyncio
async def test_preamble_already_acknowledges_tool(gate):
    ack = gate.opening_frame("okay, checking")
    await send(gate, LLMFullResponseStartFrame(), LLMTextFrame("I'll check with the hub."),
               LLMFullResponseEndFrame(), ack)
    assert ack not in gate.passed


@pytest.mark.asyncio
async def test_ack_arriving_after_result_is_dropped(gate):
    ack = gate.opening_frame("on it")
    await send(gate, LLMFullResponseStartFrame(), LLMFullResponseEndFrame(),
               LLMFullResponseStartFrame(), LLMTextFrame("Couldn't reach the hub."), ack)
    assert ack not in gate.passed


@pytest.mark.asyncio
async def test_tool_only_turn_gets_one_ack_even_across_chained_calls(gate):
    first = gate.opening_frame("checking")
    second = gate.opening_frame("one sec")
    await send(gate, LLMFullResponseStartFrame(), LLMTextFrame(" "),
               LLMFullResponseEndFrame(), first, LLMFullResponseStartFrame(),
               LLMFullResponseEndFrame(), second)
    assert first in gate.passed and not first.append_to_context
    assert second not in gate.passed


@pytest.mark.parametrize("boundary", [InterruptionFrame, UserStartedSpeakingFrame])
@pytest.mark.asyncio
async def test_stale_ack_dropped_and_next_turn_can_ack(gate, boundary):
    old = gate.opening_frame("checking")
    await send(gate, LLMTextFrame("Done."), boundary())
    fresh = gate.opening_frame("one sec")
    await send(gate, old, fresh)
    assert old not in gate.passed
    assert fresh in gate.passed


@pytest.mark.asyncio
async def test_progress_deliveries_and_answer_still_pass(gate):
    progress = TTSSpeakFrame("Still waiting on the hub.", append_to_context=False)
    answer = LLMTextFrame("The build passed.")
    await send(gate, gate.opening_frame("checking"), progress, answer)
    assert progress in gate.passed and answer in gate.passed


@pytest.mark.parametrize("preamble,expected", [
    ("I'll check in with the hub to get a status update on the fleet.",
     ["I'll check in with the hub to get a status update on the fleet.",
      "Couldn't reach the hub."]),
    ("", ["okay, checking", "Couldn't reach the hub."]),
    ("<think>I should call the hub.</think>",
     ["okay, checking", "Couldn't reach the hub."]),
])
@pytest.mark.asyncio
async def test_real_tts_does_not_synthesize_duplicate_ack(monkeypatch, preamble, expected):
    from agent.reasoning_gate import ReasoningTagGate
    from agent.spoken_logger import SpokenTextLogger
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.runner import PipelineRunner
    from pipecat.pipeline.task import PipelineTask
    import voice.tts.kokoro as kokoro

    spoken = []

    def fake_model(text, **_kwargs):
        spoken.append(text.strip())
        yield ("", "", np.zeros(240, dtype=np.float32))

    monkeypatch.setattr(kokoro, "_get_pipe", lambda _lang="a": fake_model)
    gate = ToolAckGate()
    task = PipelineTask(
        Pipeline([ReasoningTagGate(), gate, SpokenTextLogger(), kokoro.LocalKokoroTTS()]),
        cancel_on_idle_timeout=False,
    )
    await task.queue_frames([
        LLMFullResponseStartFrame(), LLMTextFrame(preamble),
        LLMFullResponseEndFrame(), gate.opening_frame("okay, checking"),
        LLMFullResponseStartFrame(), LLMTextFrame("Couldn't reach the hub."),
        LLMFullResponseEndFrame(), EndFrame(),
    ])
    await asyncio.wait_for(PipelineRunner(handle_sigint=False).run(task), timeout=10)
    assert spoken == expected
