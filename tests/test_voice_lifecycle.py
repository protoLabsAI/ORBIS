from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

import pytest
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask

import app as app_module
from voice.lifecycle import VoiceLifecycle, run_native_voice_lifecycle
from voice.local_transport import LocalAudioTransport
from voice.sse_bus import SseBus

run_bot = app_module.run_bot


def _set_ref(ref: list, value) -> None:
    ref[:] = [value]


@pytest.mark.asyncio
async def test_slow_warmup_does_not_start_pipecat_or_block_event_loop() -> None:
    """A warm beyond the old 20s setup budget stays in `warming`.

    The controlled gate represents an arbitrarily long (including >20s) model
    load without making the test sleep for that wall-clock duration.
    """
    lifecycle = VoiceLifecycle(SseBus())
    release_warm = asyncio.Event()
    pipeline_started = False

    async def controlled_off_loop(_fn: Callable[[], None]) -> None:
        await release_warm.wait()

    async def run_pipeline(_transport, on_connected, on_initialized, on_started) -> None:
        nonlocal pipeline_started
        pipeline_started = True
        await on_connected()
        await on_initialized()
        await on_started()
        await asyncio.Event().wait()

    owner = asyncio.create_task(
        run_native_voice_lifecycle(
            lifecycle=lifecycle,
            warm=lambda: None,
            make_transport=object,
            run_pipeline=run_pipeline,
            set_transport=lambda _value: None,
            set_pipeline_task=lambda _value: None,
            run_blocking=controlled_off_loop,
        )
    )
    await asyncio.sleep(0)

    assert lifecycle.snapshot() == {
        "state": "warming",
        "detail": "Loading voice models…",
    }
    assert pipeline_started is False
    # A heartbeat proves the event loop remains available while models warm.
    await asyncio.wait_for(asyncio.sleep(0), timeout=0.1)

    release_warm.set()
    for _ in range(10):
        await asyncio.sleep(0)
        if lifecycle.is_running():
            break
    assert lifecycle.is_running()
    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner


@pytest.mark.asyncio
async def test_shutdown_during_warmup_never_starts_pipeline() -> None:
    lifecycle = VoiceLifecycle(SseBus())
    warm_gate = asyncio.Event()
    pipeline_calls = 0

    async def blocked_warm(_fn: Callable[[], None]) -> None:
        await warm_gate.wait()

    async def run_pipeline(
        _transport, _on_connected, _on_initialized, _on_started,
    ) -> None:
        nonlocal pipeline_calls
        pipeline_calls += 1

    owner = asyncio.create_task(
        run_native_voice_lifecycle(
            lifecycle=lifecycle,
            warm=lambda: None,
            make_transport=object,
            run_pipeline=run_pipeline,
            set_transport=lambda _value: None,
            set_pipeline_task=lambda _value: None,
            run_blocking=blocked_warm,
        )
    )
    await asyncio.sleep(0)
    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner

    assert pipeline_calls == 0
    assert lifecycle.snapshot()["state"] == "warming"


@pytest.mark.asyncio
async def test_warm_failure_is_bounded_and_pipeline_is_not_started() -> None:
    bus = SseBus()
    lifecycle = VoiceLifecycle(bus)
    pipeline_calls = 0

    def fail_warm() -> None:
        raise RuntimeError("sensitive-ish " + "x" * 500)

    async def run_pipeline(
        _transport, _on_connected, _on_initialized, _on_started,
    ) -> None:
        nonlocal pipeline_calls
        pipeline_calls += 1

    await run_native_voice_lifecycle(
        lifecycle=lifecycle,
        warm=fail_warm,
        make_transport=object,
        run_pipeline=run_pipeline,
        set_transport=lambda _value: None,
        set_pipeline_task=lambda _value: None,
    )

    snapshot = lifecycle.snapshot()
    assert snapshot["state"] == "failed"
    assert len(snapshot["detail"]) <= 240
    assert snapshot == {
        "state": "failed",
        "detail": "Voice models failed to load",
        "code": "warmup_failed",
        "action": "retry",
    }
    assert "sensitive-ish" not in str(snapshot)
    assert pipeline_calls == 0
    assert "voice-lifecycle" in bus._retained


@pytest.mark.asyncio
async def test_pipeline_is_running_only_after_pipecat_start_signal() -> None:
    lifecycle = VoiceLifecycle(SseBus())
    allow_started = asyncio.Event()
    keep_running = asyncio.Event()
    pipeline_ref: list = []

    async def run_pipeline(_transport, on_connected, on_initialized, on_started) -> None:
        await allow_started.wait()
        await on_connected()
        await on_initialized()
        await on_started()
        await keep_running.wait()

    owner = asyncio.create_task(
        run_native_voice_lifecycle(
            lifecycle=lifecycle,
            warm=lambda: None,
            make_transport=object,
            run_pipeline=run_pipeline,
            set_transport=lambda _value: None,
            set_pipeline_task=lambda value: _set_ref(pipeline_ref, value),
        )
    )
    for _ in range(20):
        await asyncio.sleep(0)
        if lifecycle.snapshot()["state"] == "starting":
            break

    assert pipeline_ref[0].done() is False
    assert lifecycle.snapshot()["state"] == "starting"
    assert lifecycle.is_running() is False

    allow_started.set()
    for _ in range(10):
        await asyncio.sleep(0)
        if lifecycle.is_running():
            break
    assert lifecycle.is_running() is True

    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner


@pytest.mark.asyncio
async def test_pipeline_setup_return_before_start_signal_is_failed() -> None:
    lifecycle = VoiceLifecycle(SseBus())

    async def setup_times_out(
        _transport, on_connected, on_initialized, _on_started,
    ) -> None:
        # Pipecat 1.8's setup-timeout path returns from PipelineRunner.run.
        await on_connected()
        await on_initialized()
        return

    await run_native_voice_lifecycle(
        lifecycle=lifecycle,
        warm=lambda: None,
        make_transport=object,
        run_pipeline=setup_times_out,
        set_transport=lambda _value: None,
        set_pipeline_task=lambda _value: None,
    )

    assert lifecycle.snapshot() == {
        "state": "failed",
        "detail": "Voice pipeline setup ended before it became ready",
        "code": "pipeline_setup_incomplete",
        "action": "relaunch_required",
    }


@pytest.mark.asyncio
async def test_pipeline_self_cancellation_cannot_leave_stale_running_state() -> None:
    lifecycle = VoiceLifecycle(SseBus())

    async def cancelled_pipeline(
        _transport, on_connected, on_initialized, on_started,
    ) -> None:
        await on_connected()
        await on_initialized()
        await on_started()
        raise asyncio.CancelledError

    await run_native_voice_lifecycle(
        lifecycle=lifecycle,
        warm=lambda: None,
        make_transport=object,
        run_pipeline=cancelled_pipeline,
        set_transport=lambda _value: None,
        set_pipeline_task=lambda _value: None,
    )

    assert lifecycle.snapshot()["state"] == "failed"


@pytest.mark.asyncio
async def test_reset_clears_snapshot_and_retained_event() -> None:
    bus = SseBus()
    lifecycle = VoiceLifecycle(bus)
    await lifecycle.transition("running", "Voice pipeline ready")

    lifecycle.reset()

    assert lifecycle.snapshot() is None
    assert "voice-lifecycle" not in bus._retained


@pytest.mark.asyncio
async def test_real_to_thread_shutdown_is_prompt_but_worker_finishes_cooperatively() -> None:
    """Cancellation stops ownership, not already-running synchronous Python."""
    lifecycle = VoiceLifecycle(SseBus())
    worker_started = threading.Event()
    release_worker = threading.Event()
    pipeline_calls = 0

    def warm() -> None:
        worker_started.set()
        release_worker.wait(timeout=2)

    async def run_pipeline(
        _transport, _on_connected, _on_initialized, _on_started,
    ) -> None:
        nonlocal pipeline_calls
        pipeline_calls += 1

    owner = asyncio.create_task(
        run_native_voice_lifecycle(
            lifecycle=lifecycle,
            warm=warm,
            make_transport=object,
            run_pipeline=run_pipeline,
            set_transport=lambda _value: None,
            set_pipeline_task=lambda _value: None,
        )
    )
    try:
        assert await asyncio.to_thread(worker_started.wait, 1)
        owner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(owner, timeout=0.5)
        assert release_worker.is_set() is False
        assert pipeline_calls == 0
    finally:
        # `to_thread` cannot kill this function; release it so test-process
        # executor shutdown does not wait for the safety timeout.
        release_worker.set()


@pytest.mark.asyncio
async def test_locked_pipecat_start_signal_does_not_prove_transport_connected(
    tmp_path,
) -> None:
    """Pipecat 1.8.1 reaches started after LocalAudioInput swallows connect failure."""
    transport = LocalAudioTransport(sock_path=str(tmp_path / "missing.sock"))
    task = PipelineTask(
        Pipeline([transport.input(), transport.output()]),
        cancel_on_idle_timeout=False,
    )
    pipeline_started = asyncio.Event()

    @task.event_handler("on_pipeline_started")
    async def _started(_task, _frame) -> None:
        pipeline_started.set()

    runner = asyncio.create_task(PipelineRunner(handle_sigint=False).run(task))
    try:
        await asyncio.wait_for(pipeline_started.wait(), timeout=1)
        assert transport.connected is False
    finally:
        await task.cancel()
        await asyncio.wait_for(runner, timeout=1)


@pytest.mark.asyncio
async def test_connect_failure_never_starts_pipeline_and_remains_retryable(
    tmp_path,
) -> None:
    lifecycle = VoiceLifecycle(SseBus())
    transport = LocalAudioTransport(sock_path=str(tmp_path / "missing.sock"))
    pipeline_entries = 0
    runner_calls = 0

    async def run_pipeline(value, on_connected, _on_initialized, _on_started) -> None:
        nonlocal pipeline_entries, runner_calls
        pipeline_entries += 1
        if not await value.connect():
            raise RuntimeError("native audio transport connection failed")
        await on_connected()
        runner_calls += 1

    await run_native_voice_lifecycle(
        lifecycle=lifecycle,
        warm=lambda: None,
        make_transport=lambda: transport,
        transport_connected=lambda value: value.connected,
        run_pipeline=run_pipeline,
        set_transport=lambda _value: None,
        set_pipeline_task=lambda _value: None,
    )

    assert transport.connected is False
    assert pipeline_entries == 1
    assert runner_calls == 0
    assert lifecycle.is_running() is False
    assert lifecycle.snapshot() == {
        "state": "failed",
        "detail": "Native audio connection failed",
        "code": "transport_connect_failed",
        "action": "retry",
    }


@pytest.mark.asyncio
async def test_running_waits_for_session_initialization_after_successful_connect() -> None:
    lifecycle = VoiceLifecycle(SseBus())
    allow_initialization = asyncio.Event()
    pipeline_started = asyncio.Event()

    class ConnectedTransport:
        connected = True

    async def run_pipeline(transport, on_connected, on_initialized, on_started) -> None:
        await on_connected()
        await on_started()
        pipeline_started.set()
        await allow_initialization.wait()
        await on_initialized()
        await asyncio.Event().wait()

    owner = asyncio.create_task(
        run_native_voice_lifecycle(
            lifecycle=lifecycle,
            warm=lambda: None,
            make_transport=ConnectedTransport,
            transport_connected=lambda value: value.connected,
            run_pipeline=run_pipeline,
            set_transport=lambda _value: None,
            set_pipeline_task=lambda _value: None,
        )
    )
    await asyncio.wait_for(pipeline_started.wait(), timeout=1)
    assert lifecycle.snapshot()["state"] == "starting"

    allow_initialization.set()
    for _ in range(10):
        await asyncio.sleep(0)
        if lifecycle.is_running():
            break
    assert lifecycle.is_running() is True
    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner


def test_run_bot_connect_readiness_follows_real_session_initializers() -> None:
    """Tripwire the successful run_bot connection order at its real boundary."""
    source = inspect.getsource(run_bot)
    initializer = source.index("async def _initialize_session")
    delivery = source.index("state.active_delivery = delivery", initializer)
    tracer = source.index("state.active_tracer = turn_tracer", initializer)
    tts = source.index("state.active_tts = tts", initializer)
    llm = source.index("state.active_llm = llm", initializer)
    metrics = source.index('_METRICS["sessions_active"] += 1', initializer)
    session_sse = source.index('sse_bus.publish("session"', initializer)
    requery = source.index("requery_outbound(_DELEGATES, delivery)", initializer)
    resolved = source.index("initialization_ready.set_result(None)", requery)
    handler = source.index('@transport.event_handler("on_client_connected")', resolved)
    connect = source.index("if not await transport.connect()", handler)
    connected = source.index("await on_transport_connected()", connect)
    await_initializer = source.index("await initialization_ready", connected)
    initialized = source.index("await on_session_initialized()", await_initializer)
    runner = source.index("PipelineRunner(handle_sigint=False).run(task)", initialized)

    assert initializer < delivery < tracer < tts < llm < metrics < session_sse
    assert session_sse < requery < resolved < handler < connect < connected
    assert connected < await_initializer < initialized < runner


@pytest.mark.asyncio
async def test_successful_real_transport_waits_for_connection_handler_initialization(
) -> None:
    """Integrate real socket/event dispatch with the run_bot readiness order."""
    socket_path = Path("/tmp") / f"orbis-lifecycle-{uuid4().hex}.sock"
    peer_closed = asyncio.Event()

    async def handle_peer(_reader, writer) -> None:
        await peer_closed.wait()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_unix_server(handle_peer, path=str(socket_path))
    transport = LocalAudioTransport(sock_path=str(socket_path))
    lifecycle = VoiceLifecycle(SseBus())
    initialized: list[str] = []

    async def run_pipeline(value, on_connected, on_initialized, on_started) -> None:
        @value.event_handler("on_client_connected")
        async def _real_session_initializer(_transport, _client) -> None:
            # Mirrors the state families guarded by the source-order tripwire.
            initialized.extend(
                ["delivery", "tracer", "tts", "llm", "metrics", "sse", "requery"]
            )
            await asyncio.sleep(0)
            await on_initialized()

        assert await value.connect() is True
        await on_connected()
        await on_started()
        await asyncio.Event().wait()

    owner = asyncio.create_task(
        run_native_voice_lifecycle(
            lifecycle=lifecycle,
            warm=lambda: None,
            make_transport=lambda: transport,
            transport_connected=lambda value: value.connected,
            run_pipeline=run_pipeline,
            set_transport=lambda _value: None,
            set_pipeline_task=lambda _value: None,
        )
    )
    try:
        for _ in range(20):
            await asyncio.sleep(0)
            if lifecycle.is_running():
                break
        assert lifecycle.is_running() is True
        assert initialized == [
            "delivery",
            "tracer",
            "tts",
            "llm",
            "metrics",
            "sse",
            "requery",
        ]
    finally:
        owner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await owner
        await transport._disconnect()
        peer_closed.set()
        server.close()
        await server.wait_closed()
        socket_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_real_connection_initializer_failure_becomes_terminal_failed() -> None:
    socket_path = Path("/tmp") / f"orbis-lifecycle-{uuid4().hex}.sock"
    peer_closed = asyncio.Event()

    async def handle_peer(_reader, writer) -> None:
        await peer_closed.wait()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_unix_server(handle_peer, path=str(socket_path))
    transport = LocalAudioTransport(sock_path=str(socket_path))
    lifecycle = VoiceLifecycle(SseBus())

    async def run_pipeline(value, on_connected, _on_initialized, _on_started) -> None:
        initialization_ready = asyncio.get_running_loop().create_future()

        @value.event_handler("on_client_connected")
        async def _failing_initializer(_transport, _client) -> None:
            try:
                raise RuntimeError("private initialization detail")
            except Exception as exc:
                initialization_ready.set_exception(exc)
                raise

        assert await value.connect() is True
        await on_connected()
        await initialization_ready

    try:
        await run_native_voice_lifecycle(
            lifecycle=lifecycle,
            warm=lambda: None,
            make_transport=lambda: transport,
            transport_connected=lambda value: value.connected,
            run_pipeline=run_pipeline,
            set_transport=lambda _value: None,
            set_pipeline_task=lambda _value: None,
        )

        assert lifecycle.snapshot() == {
            "state": "failed",
            "detail": "Voice pipeline failed to start",
            "code": "pipeline_start_failed",
            "action": "relaunch_required",
        }
        assert "private initialization detail" not in str(lifecycle.snapshot())
    finally:
        await transport._disconnect()
        peer_closed.set()
        server.close()
        await server.wait_closed()
        socket_path.unlink(missing_ok=True)
