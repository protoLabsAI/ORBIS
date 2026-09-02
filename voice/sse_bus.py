"""SseBus — lightweight in-process Server-Sent Events fan-out bus.

Usage (server-side)
-------------------
    from voice.sse_bus import sse_bus

    # publish from anywhere in the pipeline (sync or async context):
    await sse_bus.publish("bot-state", {"state": "speaking"})

Usage (FastAPI endpoint)
------------------------
    @app.get("/api/events")
    async def events(user=Depends(require_user)):
        return StreamingResponse(
            sse_bus.subscribe(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

Wire-format
-----------
Each event is formatted as:
    event: <name>\\ndata: <json>\\n\\n

Heartbeat (keep-alive) every HEARTBEAT_S seconds:
    : heartbeat\\n\\n

Implementation note on cleanup
-------------------------------
The generator blocks on ``await asyncio.wait_for(q.get(), HEARTBEAT_S)``:
it parks on a real event, and on timeout emits a heartbeat comment. This
both bounds idle wakeups to once per HEARTBEAT_S (no busy loop) and gives
a clean teardown point — Starlette/FastAPI cancels the streaming task on
client disconnect, raising ``CancelledError`` at the ``await``, which runs
the ``finally`` block (subscriber removal). An earlier revision avoided
awaiting and used a 0.5s waker + ``get_nowait()`` drain so the generator
always parked at a ``yield`` for ``aclose()``; that spun a CPU core because
a bare ``yield`` to an eager consumer never actually yields control. The
await-based design relies on cancellation, not ``aclose()``, for cleanup.

Events published with ``retain=True`` keep only the latest frame per event
name and enqueue it when a later subscriber connects. This is for bounded
startup state such as the local hub confirmation, not an event history.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncGenerator

logger = logging.getLogger(__name__)

HEARTBEAT_S: float = 25.0   # seconds between keep-alive comments
_QUEUE_MAXSIZE = 64          # per-subscriber backpressure cap

_SENTINEL = object()         # put into queue to signal teardown


class SseBus:
    """Process-wide fan-out bus for SSE events."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._retained: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Publish
    # ------------------------------------------------------------------

    async def publish(
        self, event: str, data: Any = None, *, retain: bool = False,
    ) -> None:
        """Publish an SSE event, optionally replaying it to late subscribers."""
        payload = json.dumps(data) if data is not None else "{}"
        message = f"event: {event}\ndata: {payload}\n\n"
        if retain:
            self._retained[event] = message
        for q in list(self._subscribers):
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                logger.warning("[sse_bus] subscriber queue full — dropping '%s'", event)

    def publish_sync(self, event: str, data: Any = None) -> None:
        """Fire-and-forget publish from a sync context (uses the running loop)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.publish(event, data))

    # ------------------------------------------------------------------
    # Subscribe (async generator)
    # ------------------------------------------------------------------

    async def subscribe(self) -> AsyncGenerator[str, None]:
        """Yield SSE-formatted strings until the client disconnects.

        Blocks on ``asyncio.wait_for(q.get(), HEARTBEAT_S)``: parks on a
        real event, and on timeout emits a heartbeat comment. Idle wakeups
        are bounded to once per ``HEARTBEAT_S`` (no busy loop). Cleanup runs
        in ``finally`` when Starlette cancels the streaming task on client
        disconnect (``CancelledError`` raised at the await).
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._subscribers.add(q)
        for message in self._retained.values():
            q.put_nowait(message)
        logger.debug("[sse_bus] subscriber connected (total=%d)", len(self._subscribers))

        try:
            yield ": connected\n\n"

            while True:
                try:
                    item = await asyncio.wait_for(q.get(), timeout=HEARTBEAT_S)
                except asyncio.TimeoutError:
                    # Idle for HEARTBEAT_S — keep the connection alive.
                    yield ": heartbeat\n\n"
                    continue
                if item is _SENTINEL:
                    return
                yield item
                # Opportunistically drain any events queued behind this one
                # so a burst flushes in a single wakeup.
                while True:
                    try:
                        item = q.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if item is _SENTINEL:
                        return
                    yield item

        except (GeneratorExit, asyncio.CancelledError):
            pass
        finally:
            self._subscribers.discard(q)
            logger.debug("[sse_bus] subscriber disconnected (total=%d)", len(self._subscribers))

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def close_all(self) -> None:
        """Signal all subscribers to exit."""
        for q in list(self._subscribers):
            try:
                q.put_nowait(_SENTINEL)
            except asyncio.QueueFull:
                pass

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def subscriber_count(self) -> int:
        return len(self._subscribers)


# Module-level singleton.
sse_bus = SseBus()
