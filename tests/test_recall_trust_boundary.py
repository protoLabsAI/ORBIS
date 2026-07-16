"""Regression: recall/summary must not override live capability authority (#625).

The 2026-07-12 QA bug: a rolling summary recorded the user saying an orb
control "looked wedged/stuck". Because `_recall_block` was appended to the
system prompt AFTER the code-derived capabilities/tool blocks, the model read
that historical claim as capability truth and stopped calling the tool for the
rest of the (cross-)session.

The fix has two deterministic guarantees, both asserted here:
  1. `_effective_prompt` renders the recall block BEFORE the capabilities and
     grounding blocks — the code-derived capability list is the authoritative,
     most-recent word on what the agent can do.
  2. The recall block itself carries an explicit trust-boundary rule telling the
     model to ignore "broken/unavailable/wedged" claims from memory.

These are the mechanism of the bug, so guarding them deterministically catches a
regression that a live-model eval (nondeterministic; today's model happens to be
robust to descriptive poisons on the still-registered tools) would not.
"""

from __future__ import annotations

from types import SimpleNamespace

import app
from agent.filler import Verbosity, recall_block

_POISON = "The user reported that orb control looks wedged/stuck and seems broken."
_CAPS_HEADER = "## WHAT YOU CAN DO"
_GROUNDING_HEADER = "## GROUNDING"
_MEMORY_HEADER = "## MEMORY"


def test_recall_block_states_trust_boundary() -> None:
    """The recall framing must tell the model that memory is not capability
    truth and to keep using the tool despite a 'broken' claim."""
    block = recall_block(summary=_POISON)
    assert "NOT capability truth" in block
    assert "IGNORE that claim" in block
    assert _POISON in block
    # Nothing to recall → no block at all (no empty header injected).
    assert recall_block() == ""
    assert recall_block(summary="") == ""


def _assemble_with_recall(monkeypatch, *, summary):
    """Assemble the REAL system prompt with a fixed recall summary, mocking the
    memory reads so the test is deterministic and offline."""
    monkeypatch.setattr(app, "load_last_summary", lambda _uid: summary)
    # No SQLite prior sessions / inbox / personality — force the empty paths so
    # the only user-state block is the recall summary under test.
    def _boom(*_a, **_k):
        raise RuntimeError("no memory in this test")
    monkeypatch.setattr(app, "get_memory", _boom)

    skill = SimpleNamespace(system_prompt="PERSONA_BASE", user_name="")
    tools_schema = SimpleNamespace(
        standard_tools=[SimpleNamespace(name="delegate_to", description="Hand a task to an agent.")]
    )
    return app._effective_prompt(
        skill, "kokoro", verbosity=Verbosity.BRIEF, user_id="default", tools_schema=tools_schema
    )


def test_recall_renders_before_capability_authority(monkeypatch) -> None:
    """The core #625 guarantee: recall precedes the capabilities + grounding
    blocks, and the trust rule is present in the assembled prompt."""
    prompt = _assemble_with_recall(monkeypatch, summary=_POISON)

    i_recall = prompt.find(_MEMORY_HEADER)
    i_caps = prompt.find(_CAPS_HEADER)
    i_ground = prompt.find(_GROUNDING_HEADER)

    assert i_recall != -1, "recall block missing from assembled prompt"
    assert i_caps != -1, "capabilities block missing (test fixture broken)"
    assert i_ground != -1, "grounding block missing (test fixture broken)"

    assert i_recall < i_caps, "recall must render BEFORE the capabilities block (#625)"
    assert i_recall < i_ground, "recall must render BEFORE the grounding block (#625)"
    assert "IGNORE that claim" in prompt, "trust-boundary rule lost from assembled prompt"


def test_no_recall_injects_no_memory_block(monkeypatch) -> None:
    """Empty memory → no MEMORY header, and the rest of the prompt still
    assembles (recall must be optional, not load-bearing)."""
    prompt = _assemble_with_recall(monkeypatch, summary=None)
    assert _MEMORY_HEADER not in prompt
    assert _CAPS_HEADER in prompt
