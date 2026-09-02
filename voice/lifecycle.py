"""Native voice startup lifecycle.

The desktop audio socket becoming connected does not mean that Pipecat is
ready to consume microphone frames.  This module owns the narrower, explicit
contract used by health checks and the desktop UI.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from voice.sse_bus import SseBus, sse_bus

logger = logging.getLogger(__name__)

VoiceLifecycleState = Literal["warming", "starting", "running", "failed"]
_MAX_DETAIL_CHARS = 240


class VoiceLifecycle:
    """Bounded, retained snapshot of native voice readiness."""

    def __init__(self, bus: SseBus = sse_bus) -> None:
        self._bus = bus
        self._snapshot: dict[str, str] | None = None

    def reset(self) -> None:
        """Clear state when no native audio socket is configured."""
        self._snapshot = None
        self._bus.clear_retained("voice-lifecycle")

    def snapshot(self) -> dict[str, str] | None:
        return dict(self._snapshot) if self._snapshot is not None else None

    def is_running(self) -> bool:
        return bool(self._snapshot and self._snapshot["state"] == "running")

    def recovery_action(self) -> str | None:
        return self._snapshot.get("action") if self._snapshot else None

    async def transition(
        self,
        state: VoiceLifecycleState,
        detail: str,
        *,
        code: str | None = None,
        action: Literal["retry", "relaunch_required"] | None = None,
    ) -> None:
        clean_detail = " ".join(str(detail).split())[:_MAX_DETAIL_CHARS]
        payload = {"state": state, "detail": clean_detail}
        if code is not None:
            payload["code"] = code
        if action is not None:
            payload["action"] = action
        self._snapshot = payload
        await self._bus.publish("voice-lifecycle", payload, retain=True)


PipelineStarted = Callable[[], Awaitable[None]]
TransportConnected = Callable[[], Awaitable[None]]
SessionInitialized = Callable[[], Awaitable[None]]
PipelineRunner = Callable[
    [Any, TransportConnected, SessionInitialized, PipelineStarted], Awaitable[None]
]


def _reports_connected(_transport: Any) -> bool:
    return True


async def run_native_voice_lifecycle(
    *,
    lifecycle: VoiceLifecycle,
    warm: Callable[[], None],
    make_transport: Callable[[], Any],
    run_pipeline: PipelineRunner,
    set_transport: Callable[[Any], None],
    set_pipeline_task: Callable[[asyncio.Task | None], None],
    transport_connected: Callable[[Any], bool] = _reports_connected,
    run_blocking: Callable[[Callable[[], None]], Awaitable[Any]] = asyncio.to_thread,
) -> None:
    """Warm once off-loop, then own the native pipeline until shutdown.

    There is deliberately no startup deadline here: a cold local model may
    take longer than Pipecat's setup timeout.  Pipecat is not started until
    warming has completed, so its own setup deadline begins at the right time.
    """
    pipeline_task: asyncio.Task | None = None
    pipeline_started = False
    session_initialized = False
    terminal = False
    phase = "warmup"
    try:
        await lifecycle.transition("warming", "Loading voice models…")
        await run_blocking(warm)

        phase = "connect"
        await lifecycle.transition("starting", "Starting voice pipeline…")
        transport = make_transport()
        set_transport(transport)

        async def _on_transport_connected() -> None:
            nonlocal phase
            phase = "pipeline"

        async def _maybe_running() -> None:
            if (
                pipeline_started
                and session_initialized
                and not terminal
                and transport_connected(transport)
            ):
                await lifecycle.transition("running", "Voice pipeline ready")

        async def _on_session_initialized() -> None:
            nonlocal session_initialized
            session_initialized = True
            await _maybe_running()

        async def _on_pipeline_started() -> None:
            nonlocal pipeline_started
            if not transport_connected(transport):
                await lifecycle.transition(
                    "failed",
                    "Native audio disconnected during startup",
                    code="transport_disconnected",
                    action="relaunch_required",
                )
                return
            pipeline_started = True
            await _maybe_running()

        pipeline_task = asyncio.create_task(
            run_pipeline(
                transport,
                _on_transport_connected,
                _on_session_initialized,
                _on_pipeline_started,
            ),
            name="orbis-native-pipeline",
        )
        set_pipeline_task(pipeline_task)
        await pipeline_task

        terminal = True
        detail = (
            "Voice pipeline stopped unexpectedly"
            if pipeline_started
            else "Voice pipeline setup ended before it became ready"
        )
        logger.error("[native audio] %s", detail)
        await lifecycle.transition(
            "failed",
            detail,
            code=(
                "pipeline_stopped" if pipeline_started else "pipeline_setup_incomplete"
            ),
            action="relaunch_required",
        )
    except asyncio.CancelledError:
        terminal = True
        # Cancellation of the owner is normal shutdown. A pipeline that
        # cancels itself is an unexpected terminal failure and must not leave
        # a stale `running` snapshot behind.
        if not asyncio.current_task().cancelling():
            await lifecycle.transition(
                "failed",
                "Voice pipeline stopped unexpectedly",
                code="pipeline_stopped",
                action="relaunch_required",
            )
            return
        if pipeline_task is not None and not pipeline_task.done():
            pipeline_task.cancel()
            try:
                await pipeline_task
            except (asyncio.CancelledError, Exception):
                pass
        raise
    except Exception:  # noqa: BLE001 — lifecycle must expose startup failure
        terminal = True
        # The full exception belongs in private sidecar logs. `/healthz` and
        # SSE are public surfaces, so expose only stable, actionable codes.
        logger.exception("[native audio] voice %s failed", phase)
        if phase == "warmup":
            await lifecycle.transition(
                "failed",
                "Voice models failed to load",
                code="warmup_failed",
                action="retry",
            )
        elif phase == "connect":
            await lifecycle.transition(
                "failed",
                "Native audio connection failed",
                code="transport_connect_failed",
                action="retry",
            )
        else:
            await lifecycle.transition(
                "failed",
                "Voice pipeline failed to start",
                code="pipeline_start_failed",
                action="relaunch_required",
            )
