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
Python's async generator ``aclose()`` only injects GeneratorExit when the
generator is suspended at a ``yield`` expression, not at an ``await``.
To guarantee cleanup, the generator never awaits inside the yield loop —
it uses ``asyncio.Event`` + a background waker task that wakes the loop
every POLL_INTERVAL seconds, writes a sentinel to the queue, then the
generator drains via synchronous ``get_nowait()`` and always lands back
at a ``yield`` before the next iteration.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncGenerator

logger = logging.getLogger(__name__)

HEARTBEAT_S: float = 25.0   # seconds between keep-alive comments
_POLL_INTERVAL: float = 0.5  # waker tick interval (seconds)
_QUEUE_MAXSIZE = 64          # per-subscriber backpressure cap

_TICK = object()             # waker sentinel — triggers queue drain
_SENTINEL = object()         # put into queue to signal teardown


class SseBus:
    """Process-wide fan-out bus for SSE events."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()

    # ------------------------------------------------------------------
    # Publish
    # ------------------------------------------------------------------

    async def publish(self, event: str, data: Any = None) -> None:
        """Publish an SSE event to all current subscribers."""
        payload = json.dumps(data) if data is not None else "{}"
        message = f"event: {event}\ndata: {payload}\n\n"
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

        The generator is always suspended at a ``yield`` so that
        ``aclose()`` (triggered by FastAPI on client disconnect) reliably
        reaches the ``finally`` block for cleanup.

        A background asyncio.Task ticks every ``_POLL_INTERVAL`` seconds,
        putting a ``_TICK`` sentinel into the queue so the main loop wakes
        up to drain real events or emit a heartbeat.
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._subscribers.add(q)
        logger.debug("[sse_bus] subscriber connected (total=%d)", len(self._subscribers))

        # Background waker: wakes the generator loop without suspending it
        # at an await inside the generator body.
        async def _waker():
            while True:
                await asyncio.sleep(_POLL_INTERVAL)
                try:
                    q.put_nowait(_TICK)
                except asyncio.QueueFull:
                    pass

        waker_task = asyncio.create_task(_waker(), name="sse-waker")

        last_heartbeat = time.monotonic()
        try:
            # Initial connection acknowledgement — inside try so GeneratorExit
            # thrown here by aclose() is caught and falls into finally.
            yield ": connected\n\n"

            while True:
                # Drain everything currently in the queue (non-blocking).
                emitted = False
                while True:
                    try:
                        item = q.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if item is _SENTINEL:
                        return
                    if item is _TICK:
                        # Waker fired — always yield a comment so the generator
                        # returns to a yield point.  Emit a full heartbeat when
                        # HEARTBEAT_S has elapsed, otherwise a minimal comment.
                        now = time.monotonic()
                        if (now - last_heartbeat) >= HEARTBEAT_S:
                            yield ": heartbeat\n\n"
                            last_heartbeat = now
                        else:
                            yield ": \n\n"
                        emitted = True
                        continue
                    # Real SSE message.
                    yield item
                    emitted = True

                if not emitted:
                    # Queue drained with no real events or tick — yield a
                    # minimal keep-alive comment so:
                    # (a) the generator is at a yield point for aclose(), and
                    # (b) the outer while loop doesn't spin as a busy loop.
                    # EventSource clients ignore comment lines.
                    yield ": \n\n"

        except GeneratorExit:
            pass
        finally:
            waker_task.cancel()
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
