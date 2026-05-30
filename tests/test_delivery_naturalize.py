"""Tests for naturalized proactive deliveries (orbis-2mh).

When an announcer is wired, a real proactive delivery is phrased
in-character by the micro LLM (the announcer) instead of spoken verbatim;
on None/timeout/error it falls back to the raw (attributed) text so a flaky
micro-LLM can never block or change a delivery.
"""

from __future__ import annotations

import pytest

from agent.delivery import DeliveryController, Priority, _attribute


class _Cap:
    def __init__(self):
        self.emitted: list[str] = []

    async def __call__(self, frame):
        self.emitted.append(getattr(frame, "text", None))


class _FakeContext:
    def __init__(self):
        self.messages: list[dict] = []

    def add_message(self, m):
        self.messages.append(m)


def _ctrl():
    c = DeliveryController()
    cap = _Cap()
    c.set_emitter(cap)
    ctx = _FakeContext()
    c.set_context(ctx)
    return c, cap, ctx


@pytest.mark.asyncio
async def test_announcer_naturalizes_now_path() -> None:
    c, cap, ctx = _ctrl()

    async def ann(content, kind, source):
        return f"oh hey — {content}"

    c.set_announcer(ann)
    await c.deliver("drink some water", priority=Priority.CRITICAL, kind="reminder")
    assert cap.emitted == ["oh hey — drink some water"]      # natural, not raw
    assert ctx.messages[0]["content"] == "oh hey — drink some water"  # remembers the spoken line


@pytest.mark.asyncio
async def test_announcer_receives_raw_kind_source() -> None:
    c, cap, _ = _ctrl()
    seen: list[tuple] = []

    async def ann(content, kind, source):
        seen.append((content, kind, source))
        return "x"

    c.set_announcer(ann)
    await c.deliver("the build is green", priority=Priority.CRITICAL,
                    source="ava", kind="delegate")
    # gets the RAW content (un-attributed) + the metadata
    assert seen == [("the build is green", "delegate", "ava")]


@pytest.mark.asyncio
async def test_none_falls_back_to_attributed_raw() -> None:
    c, cap, _ = _ctrl()

    async def ann(content, kind, source):
        return None

    c.set_announcer(ann)
    await c.deliver("done", priority=Priority.CRITICAL, source="ava")
    assert cap.emitted == [_attribute("done", "ava")]  # attributed fallback


@pytest.mark.asyncio
async def test_exception_falls_back() -> None:
    c, cap, _ = _ctrl()

    async def ann(content, kind, source):
        raise RuntimeError("micro-LLM down")

    c.set_announcer(ann)
    await c.deliver("ping", priority=Priority.CRITICAL)
    assert cap.emitted == ["ping"]  # raw fallback, no crash


@pytest.mark.asyncio
async def test_no_announcer_is_verbatim() -> None:
    c, cap, _ = _ctrl()  # no set_announcer
    await c.deliver("verbatim line", priority=Priority.CRITICAL)
    assert cap.emitted == ["verbatim line"]


@pytest.mark.asyncio
async def test_drain_path_naturalizes() -> None:
    c, cap, _ = _ctrl()

    async def ann(content, kind, source):
        return f"got something for you — {content}"

    c.set_announcer(ann)
    # TIME_SENSITIVE → NEXT_SILENCE; user not speaking → drains right away.
    await c.deliver("ava wrapped it up", priority=Priority.TIME_SENSITIVE,
                    source="ava", kind="delegate")
    assert any("got something for you — ava wrapped it up" == e for e in cap.emitted)
