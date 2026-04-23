"""Tests for the personality prompt-rendering module.

The drift-analyzer LLM call is covered by smoke/integration tests
elsewhere; this file focuses on the pure rendering + axis-label math.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.personality import (
    _axis_label,
    render_personality_block,
)
from memory import Memory


@pytest.fixture
def mem(tmp_path: Path) -> Memory:
    m = Memory(tmp_path / "orbis.sqlite")
    m.personality.seed_defaults()
    return m


# --- axis label tiers -------------------------------------------------------


def test_axis_label_neutral_under_15():
    assert _axis_label(0.05, "serious", "playful") == "neutral"
    assert _axis_label(-0.1, "serious", "playful") == "neutral"


def test_axis_label_slightly_15_to_40():
    assert _axis_label(0.2, "serious", "playful") == "slightly playful"
    assert _axis_label(-0.3, "serious", "playful") == "slightly serious"


def test_axis_label_plain_40_to_75():
    assert _axis_label(0.5, "serious", "playful") == "playful"
    assert _axis_label(-0.6, "serious", "playful") == "serious"


def test_axis_label_strongly_above_75():
    assert _axis_label(0.9, "serious", "playful") == "strongly playful"
    assert _axis_label(-1.0, "serious", "playful") == "strongly serious"


# --- render_personality_block ------------------------------------------------


def test_empty_state_renders_empty(tmp_path: Path):
    m = Memory(tmp_path / "fresh.sqlite")
    # No seeded axes; mood starts neutral. Block should be empty.
    assert render_personality_block(m) == ""


def test_only_neutral_axes_renders_empty(mem: Memory):
    # seed_defaults writes all at 0.0; no axis survives the |0.15| filter.
    block = render_personality_block(mem)
    # Mood also neutral, so entire block should be empty.
    assert block == ""


def test_axis_above_threshold_surfaces(mem: Memory):
    mem.personality.upsert_axis("playful_serious", 0.5)
    block = render_personality_block(mem)
    assert "playful" in block
    assert "## PERSONALITY" in block


def test_negative_axis_uses_negative_adjective(mem: Memory):
    mem.personality.upsert_axis("warm_guarded", -0.6)
    block = render_personality_block(mem)
    assert "guarded" in block
    assert "warm" not in block.replace("warm_guarded", "")


def test_mood_surfaces_in_block(mem: Memory):
    mem.personality.set_mood(valence=0.4, arousal=-0.3, guardedness=0.0)
    block = render_personality_block(mem)
    assert "bright" in block
    assert "sleepy" in block


def test_guardedness_only_surfaces_when_positive(mem: Memory):
    """Guardedness is a one-sided axis; negative values (i.e. relaxed)
    don't render 'unguarded' — just omit the descriptor."""
    mem.personality.set_mood(guardedness=-0.5)
    block = render_personality_block(mem)
    assert "guarded" not in block


def test_combined_axes_and_mood_render(mem: Memory):
    mem.personality.upsert_axis("playful_serious", 0.8)
    mem.personality.upsert_axis("hopeful_cynical", -0.5)
    mem.personality.set_mood(valence=0.3)
    block = render_personality_block(mem)
    assert "playful" in block
    assert "cynical" in block
    assert "bright" in block


def test_strongly_label_appears_for_extreme_values(mem: Memory):
    mem.personality.upsert_axis("probing_incurious", 0.85)
    block = render_personality_block(mem)
    assert "strongly probing" in block


def test_block_has_nudge_footer(mem: Memory):
    mem.personality.upsert_axis("playful_serious", 0.5)
    block = render_personality_block(mem)
    # The trailing nudge telling the model not to caricature.
    assert "color your tone" in block or "drift through" in block
