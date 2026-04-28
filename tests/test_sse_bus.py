"""Tests for SseBus."""

from __future__ import annotations

import asyncio
import json

import pytest

from voice.sse_bus import SseBus, _POLL_INTERVAL


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _collect(gen, n: int, timeout: float = 2.0) -> list[str]:
    """Collect up to n non-comment SSE frames from an async generator."""
    items: list[str] = []
    async def _run():
        async for chunk in gen:
            if chunk.startswith(":"):
                continue  # skip heartbeat / comment lines
            items.append(chunk)
            if len(items) >= n:
                return
    await asyncio.wait_for(_run(), timeout=timeout)
    return items


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_publish_reaches_subscriber():
    """A published event is received by the subscriber."""
    bus = SseBus()
    gen = bus.subscribe()

    # Consume the initial ": connected\n\n" comment.
    first = await gen.__anext__()
    assert first.startswith(":")

    await bus.publish("bot-state", {"state": "speaking"})
    chunk = await asyncio.wait_for(gen.__anext__(), timeout=1.0)

    assert "event: bot-state" in chunk
    data = json.loads(chunk.split("data: ")[1].strip())
    assert data["state"] == "speaking"

    await gen.aclose()


@pytest.mark.asyncio
async def test_multiple_subscribers_all_receive():
    """All subscribers receive the same published event."""
    bus = SseBus()
    gen_a = bus.subscribe()
    gen_b = bus.subscribe()

    # Drain connect comments.
    await gen_a.__anext__()
    await gen_b.__anext__()

    await bus.publish("ping", {"x": 1})

    chunk_a = await asyncio.wait_for(gen_a.__anext__(), timeout=1.0)
    chunk_b = await asyncio.wait_for(gen_b.__anext__(), timeout=1.0)

    assert "ping" in chunk_a
    assert "ping" in chunk_b

    await gen_a.aclose()
    await gen_b.aclose()


@pytest.mark.asyncio
async def test_subscriber_count():
    bus = SseBus()
    assert bus.subscriber_count() == 0

    gen = bus.subscribe()
    await gen.__anext__()  # connect comment; subscriber registered synchronously

    assert bus.subscriber_count() == 1

    await gen.aclose()
    # aclose() triggers GeneratorExit between poll ticks; finally runs immediately.
    await asyncio.sleep(_POLL_INTERVAL * 2)
    assert bus.subscriber_count() == 0


@pytest.mark.asyncio
async def test_close_all_terminates_subscriber():
    """close_all() sends the sentinel and the generator exits cleanly."""
    bus = SseBus()
    gen = bus.subscribe()
    await gen.__anext__()  # connect comment

    await bus.close_all()

    # After sentinel the generator should stop (StopAsyncIteration).
    items = []
    async for chunk in gen:
        items.append(chunk)
        if len(items) > 5:
            raise AssertionError("generator did not stop after close_all")


@pytest.mark.asyncio
async def test_publish_no_subscribers_no_error():
    """Publishing with zero subscribers must not raise."""
    bus = SseBus()
    # Should not raise even with no subscribers.
    await bus.publish("test", {"x": 1})


@pytest.mark.asyncio
async def test_publish_sync():
    """publish_sync fires the event via the running loop."""
    bus = SseBus()
    gen = bus.subscribe()
    await gen.__anext__()  # connect comment

    # publish_sync must work from a coroutine context (running loop exists).
    bus.publish_sync("sync-event", {"val": 42})
    # Give the scheduled task a chance to run.
    await asyncio.sleep(0)

    chunk = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    assert "sync-event" in chunk

    await gen.aclose()


@pytest.mark.asyncio
async def test_heartbeat_emitted_when_idle():
    """When no events are published, a heartbeat comment is sent after the deadline."""
    bus = SseBus()

    # Temporarily shrink HEARTBEAT_S so we don't wait 25 seconds.
    import voice.sse_bus as _mod
    original = _mod.HEARTBEAT_S
    _mod.HEARTBEAT_S = 0  # immediate heartbeat on next timeout
    try:
        gen = bus.subscribe()
        await gen.__anext__()  # connect comment
        # The next item should be a heartbeat comment.
        heartbeat = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
        assert heartbeat.startswith(":"), f"Expected heartbeat comment, got: {heartbeat!r}"
    finally:
        _mod.HEARTBEAT_S = original
        await gen.aclose()


@pytest.mark.asyncio
async def test_event_format():
    """Verify the exact SSE wire format (event + data lines, double newline)."""
    bus = SseBus()
    gen = bus.subscribe()
    await gen.__anext__()  # connect comment

    await bus.publish("my-event", {"key": "value"})
    chunk = await asyncio.wait_for(gen.__anext__(), timeout=1.0)

    lines = chunk.split("\n")
    assert lines[0] == "event: my-event"
    assert lines[1].startswith("data: ")
    data = json.loads(lines[1][len("data: "):])
    assert data == {"key": "value"}
    # Must end with double newline → last two elements are empty strings.
    assert lines[-1] == "" and lines[-2] == ""

    await gen.aclose()
