"""Pipeline-placement smoke tests for AudioTagsTap (#66 Phase 4).

Checks that the construction call exists in run_bot and that the tap
sits between `stt` and `user_agg` in the Pipeline list — the placement
the AudioTagsTap depends on (TranscriptionFrame must reach the tap
before user_agg adds it to LLM context, otherwise the [audio] system
message lands AFTER the user message and loses its "before" semantics).

Cheaper than wiring the full pipeline; catches accidental removal /
re-ordering on review.
"""

from __future__ import annotations

import app


def test_audio_tags_constructed_in_run_bot() -> None:
    """make_audio_tags_tap is called during run_bot construction so
    the tap is per-session (passes mem from get_memory())."""
    src = open(app.run_bot.__code__.co_filename).read()
    assert "make_audio_tags_tap(mem=get_memory())" in src


def test_audio_tags_imported_at_module_top() -> None:
    """Import is module-level — no lazy import inside run_bot. The
    [audio_tags] is part of the project's hard dep set; lazy-loading
    it would mask import failures until first session."""
    src = open(app.run_bot.__code__.co_filename).read()
    assert "from agent.audio_tags import make_audio_tags_tap" in src


def test_audio_tags_placed_between_stt_and_user_agg() -> None:
    """AudioTagsTap MUST sit between stt and user_agg. user_agg
    accumulates messages into LLM context — the [audio] annotation
    must reach the context BEFORE the user transcription does, which
    means AudioTagsTap (which injects the LLMMessagesAppendFrame)
    has to fire on the TranscriptionFrame BEFORE user_agg sees it.

    Pipeline list inside run_bot has line entries for each stage; we
    verify ordering by string-search since constructing the full
    pipeline needs Pipecat's task/runtime which is a heavier fixture.
    """
    src = open(app.run_bot.__code__.co_filename).read()
    # Find the Pipeline([ block — ordering is ascertainable from there.
    pipe_start = src.index("pipeline = Pipeline([")
    pipe_end = src.index("])", pipe_start)
    pipe_body = src[pipe_start:pipe_end]

    # Each stage appears as `<name>,` on its own line in the list.
    stt_idx = pipe_body.index("stt,")
    audio_tags_idx = pipe_body.index("audio_tags,")
    user_agg_idx = pipe_body.index("user_agg,")

    assert stt_idx < audio_tags_idx < user_agg_idx, (
        "AudioTagsTap must sit between stt and user_agg — see test "
        "docstring for why ordering is load-bearing"
    )


def test_audio_tags_after_speaker_gate() -> None:
    """SpeakerGate emits OwnerVerifiedFrame / StrangerDetectedFrame
    that AudioTagsTap consumes (mood-write owner gate). If AudioTagsTap
    sat upstream of SpeakerGate, every emotion turn would arrive
    flagged speaker_verified=True (the tap's default) regardless of
    the gate's actual decision."""
    src = open(app.run_bot.__code__.co_filename).read()
    pipe_start = src.index("pipeline = Pipeline([")
    pipe_end = src.index("])", pipe_start)
    pipe_body = src[pipe_start:pipe_end]

    speaker_gate_idx = pipe_body.index("speaker_gate,")
    audio_tags_idx = pipe_body.index("audio_tags,")
    assert speaker_gate_idx < audio_tags_idx
