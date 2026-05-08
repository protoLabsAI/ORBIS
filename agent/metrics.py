"""In-process metric counters — the central place for "this thing
happened, count it" so silent-failure paths surface in /api/metrics.

Why a module
------------
Several modules (personality drift, ollama/mlx tool-call translation,
delegate probes) catch exceptions and log at INFO/WARNING. Without a
counter, you can't tell from outside whether those caught paths fire
once a week or every turn. The audit called this out: drift analysis
silently degrades on LLM failure, tool-call translation silently
falls back, etc.

Pattern
-------
Single shared dict, single module. Anywhere you'd write a "best-effort"
``except: log.info(...)`` block, also call ``metrics.inc("drift_llm_call_failed")``.
``app.py``'s /api/metrics merges this snapshot into the response so the
counters surface without any extra plumbing.

Counters reset on process restart — same posture as the existing
``_METRICS`` dict in app.py. Persisting across restarts isn't worth
the complexity for in-process diagnostics.

Gauge vs counter
----------------
Most callers use ``inc()`` (monotonic counter). For point-in-time
state (queue depth, last latency) use ``set(name, value)``; the
snapshot returns whatever was last written.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

# Single global state, locked so concurrent inc() calls from async tasks
# can't lose increments. The lock is taken for microseconds — never
# wraps an I/O call — so it can't bottleneck the event loop.
_LOCK = threading.Lock()
_COUNTERS: dict[str, int] = {}
_GAUGES: dict[str, Any] = {}


def inc(name: str, by: int = 1) -> None:
    """Bump a monotonic counter. Safe to call from any thread / task."""
    if by == 0:
        return
    with _LOCK:
        _COUNTERS[name] = _COUNTERS.get(name, 0) + by


def set(name: str, value: Any) -> None:  # noqa: A001 — `set` is the natural verb here
    """Set a gauge value. Used for point-in-time observations like
    last-recorded latency or current queue depth."""
    with _LOCK:
        _GAUGES[name] = value


def snapshot() -> dict[str, Any]:
    """Return a shallow copy of the current state. Safe to mutate."""
    with _LOCK:
        return {
            "counters": dict(_COUNTERS),
            "gauges": dict(_GAUGES),
        }


def reset() -> None:
    """Clear all state. Used by tests; never called in production."""
    with _LOCK:
        _COUNTERS.clear()
        _GAUGES.clear()
