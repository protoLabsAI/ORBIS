"""Spoken replies stay concise, and display Markdown never reaches synthesis."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import numpy as np
import pytest

from agent.filler import Verbosity, plan_block, tool_response_block
from agent.prosody import ProsodyTextFilter
from agent.speech_text import SpeechTextFilter, strip_markdown_for_speech
from agent.voice_style import SPOKEN_DELIVERY


@pytest.mark.parametrize("text,spoken", [
    ("**Done**, it's set for **nine**.", "Done, it's set for nine."),
    ("**First sentence.", "First sentence."),
    ("Second sentence.**", "Second sentence."),
    ("*That's it.", "That's it."),
    ("## Result\n1. First\n2. Second", "Result\nFirst\nSecond"),
    ("#4028 is filed.", "#4028 is filed."),
    ("[the ticket](https://example.test/a_(b)) is filed.", "the ticket is filed."),
    ("![a diagram](https://example.test/image.png)", "a diagram"),
    ("```python\nprint('hello')\n```", "print('hello')"),
    ("---\nAll set.", "All set."),
    ("~~Old time~~. Nine now.", "Old time. Nine now."),
    ("| Task | State |\n| --- | --- |\n| **Build** | Done |", "Task, State\nBuild, Done"),
    ("The result is 2 * 3 = 6.", "The result is 2 * 3 = 6."),
    ("2 ** 3 = 8", "2 ** 3 = 8"),
    ("Use snake_case or file__name__part.", "Use snake_case or file__name__part."),
    ("Call `__init__` with `*args`.", "Call __init__ with *args."),
    ("`2 | 3`", "2 | 3"),
    ("Version 1.2.3 is ready.", "Version 1.2.3 is ready."),
    ("", ""),
])
def test_display_formatting_removed_without_losing_meaning(text, spoken):
    assert strip_markdown_for_speech(text) == spoken


@pytest.mark.asyncio
async def test_non_fish_filter_removes_markdown_and_prosody():
    assert await ProsodyTextFilter().filter(
        "**Done** [softly] for **nine**."
    ) == "Done for nine."


@pytest.mark.asyncio
async def test_fish_filter_keeps_native_prosody_and_fragment_spacing():
    speech_filter = SpeechTextFilter()
    assert await speech_filter.filter(" [softly] **Done**. ") == " [softly] Done. "
    assert await speech_filter.filter(" ** ") == ""
    await speech_filter.handle_interruption()
    await speech_filter.reset_interruption()
    assert await speech_filter.filter("Next **reply**.") == "Next reply."


@pytest.mark.parametrize("verbosity", list(Verbosity))
def test_no_plan_preamble_or_routine_followup_at_any_verbosity(verbosity):
    assert plan_block(verbosity) == ""
    response = tool_response_block(verbosity)
    assert "No routine follow-up offers or questions" in response
    assert "Give more detail when requested" in response
    assert "want the details?" not in response
    assert "warm follow-up offer" not in response


def test_saved_persona_gets_spoken_contract_on_real_prompt_composition(monkeypatch):
    import app

    monkeypatch.setattr(app, "_recall_block", lambda _uid: "A prior Markdown report.")
    monkeypatch.setattr(app, "get_memory", lambda: SimpleNamespace())
    monkeypatch.setattr(app, "_render_inbox_pending_block", lambda _mem: "")
    monkeypatch.setenv("ORBIS_EMOTIONAL_LAYER", "0")
    legacy = "You're Sage. Answer with long Markdown reports."
    persona = SimpleNamespace(system_prompt=legacy, user_name="", stt=None)
    prompt = app._effective_prompt(
        persona, "kokoro", verbosity=Verbosity.BRIEF, user_id="test",
    )
    assert prompt.startswith(legacy)  # Preserve the saved persona identity.
    assert prompt.endswith(SPOKEN_DELIVERY)
    assert "PLANNING SIGNAL" not in prompt
    assert "One short sentence is usually enough" in prompt
    assert "When the user asks for an explanation or detail" in prompt


@pytest.mark.asyncio
async def test_real_kokoro_pipeline_filters_streamed_reply_and_direct_speech(monkeypatch):
    """Use the actual TTS aggregation/filter path, faking only the speech model."""
    from pipecat.frames.frames import (
        EndFrame, LLMFullResponseEndFrame, LLMFullResponseStartFrame,
        LLMTextFrame, TTSSpeakFrame,
    )
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.runner import PipelineRunner
    from pipecat.pipeline.task import PipelineTask
    import voice.tts.kokoro as kokoro

    spoken = []

    def fake_pipe(text, **_kwargs):
        spoken.append(text.strip())
        yield ("", "", np.zeros(240, dtype=np.float32))

    monkeypatch.setattr(kokoro, "_get_pipe", lambda _lang="a": fake_pipe)
    task = PipelineTask(
        Pipeline([kokoro.LocalKokoroTTS()]), cancel_on_idle_timeout=False,
    )
    await task.queue_frames([
        LLMFullResponseStartFrame(),
        LLMTextFrame("**Do"), LLMTextFrame("ne**, it's set for **ni"),
        LLMTextFrame("ne**."),
        LLMFullResponseEndFrame(),
        TTSSpeakFrame(
            "Filed **#4028**: [the ticket](https://example.test/ticket).",
            append_to_context=False,
        ),
        EndFrame(),
    ])
    runner = PipelineRunner(handle_sigint=False)
    await asyncio.wait_for(runner.run(task), timeout=10)
    assert spoken == ["Done, it's set for nine.", "Filed #4028: the ticket."]
