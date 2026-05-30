"""Tests for capabilities_block (code-driven tool guidance).

The 'what you can do' prompt section is generated from the tools actually
registered for the session — never hand-maintained — so adding a tool
surfaces it to the model automatically, and a small model is told to CALL
the tool rather than just promise to.
"""

from __future__ import annotations

from types import SimpleNamespace

from agent.tools import capabilities_block, register_tools


class _FakeLLM:
    def register_function(self, *a, **k):
        pass


class _FakeDelivery:
    async def deliver(self, *a, **k):
        pass


class _FakeRegistry:
    def __init__(self, *delegates):
        self._d = list(delegates)

    def get(self, name):
        return next((d for d in self._d if d.name == name), None)

    def names(self):
        return [d.name for d in self._d]

    def all(self):
        return self._d


def _schema():
    ava = SimpleNamespace(name="ava", type="a2a", description="chief of staff")
    return register_tools(_FakeLLM(), delegates=_FakeRegistry(ava), delivery=_FakeDelivery())


def test_lists_registered_action_tools() -> None:
    block = capabilities_block(_schema())
    # the @tool surface
    for name in ("schedule_reminder", "adjust_personality", "check_inbox"):
        assert f"`{name}`" in block
    # the dynamic delegate tools
    assert "`delegate_to`" in block
    assert "`delegate_async`" in block


def test_includes_the_call_dont_narrate_instruction() -> None:
    block = capabilities_block(_schema()).lower()
    assert "call the tool" in block
    assert "reminder" in block  # schedule_reminder's hint made it in


def test_empty_or_none_schema_is_empty() -> None:
    assert capabilities_block(None) == ""
    assert capabilities_block(SimpleNamespace(standard_tools=[])) == ""


def test_hint_is_a_concise_first_sentence() -> None:
    block = capabilities_block(_schema())
    # schedule_reminder's description starts "Schedule a ONE-TIME spoken reminder…"
    line = next(ln for ln in block.splitlines() if "`schedule_reminder`" in ln)
    assert "Schedule a ONE-TIME spoken reminder" in line
    assert len(line) < 160  # trimmed, not the whole multi-paragraph description
