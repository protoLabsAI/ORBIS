"""Tests for the delegate-health probe + background loop.

Covers:
  - probe() against a2a happy / 401 / unreachable / openai routes
  - DelegateRegistry health cache: ok / fail / consecutive count
  - health_loop iteration: runs probes, updates cache, survives a
    crashing probe, respects cancellation
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from agent import metrics
from agent.delegates import (
    Delegate,
    DelegateRegistry,
    health_loop,
    probe,
    probe_local_hub_at_boot,
)


# ---------------------------------------------------------------------------
# probe() — a2a path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_a2a_happy(respx_mock):
    respx_mock.get("http://ava:3008/.well-known/agent-card.json").respond(
        status_code=200, json={"name": "ava"},
    )
    delegate = Delegate(
        name="ava", description="hi", type="a2a",
        url="http://ava:3008/a2a",
    )
    r = await probe(delegate)
    assert r["ok"] is True
    assert isinstance(r["latency_ms"], int)


@pytest.mark.asyncio
async def test_probe_a2a_auth_rejected(respx_mock):
    respx_mock.get("http://ava:3008/.well-known/agent-card.json").respond(
        status_code=401,
    )
    delegate = Delegate(
        name="ava", description="hi", type="a2a",
        url="http://ava:3008/a2a",
        auth_scheme="apiKey", a2a_credential="wrong",
    )
    r = await probe(delegate)
    assert r["ok"] is False
    assert "auth" in r["error"]
    assert r["status"] == 401


@pytest.mark.asyncio
async def test_probe_a2a_unreachable(respx_mock):
    respx_mock.get("http://ava/.well-known/agent-card.json").mock(
        side_effect=httpx.ConnectError("connection refused"),
    )
    delegate = Delegate(name="ava", description="hi", type="a2a", url="http://ava/a2a")
    r = await probe(delegate)
    assert r["ok"] is False
    assert "unreachable" in r["error"]


@pytest.mark.asyncio
async def test_probe_a2a_malformed_url():
    """No scheme/host → probe rejects without making a request."""
    delegate = Delegate(name="bad", description="hi", type="a2a", url="not a url")
    r = await probe(delegate)
    assert r["ok"] is False
    assert "malformed" in r["error"]


@pytest.mark.asyncio
async def test_probe_a2a_sends_auth_header(respx_mock):
    """Bearer scheme produces the right header so the probe matches
    what dispatch would actually use."""
    route = respx_mock.get("http://ava/.well-known/agent-card.json").respond(
        status_code=200, json={"name": "ava"},
    )
    delegate = Delegate(
        name="ava", description="hi", type="a2a", url="http://ava/a2a",
        auth_scheme="bearer", a2a_credential="secret-token",
    )
    await probe(delegate)
    assert route.calls[0].request.headers["Authorization"] == "Bearer secret-token"


@pytest.mark.asyncio
async def test_probe_a2a_non_200_distinct_from_auth(respx_mock):
    """A 502 from the agent card endpoint is reported with the status
    code so the UI can distinguish "delegate is rebooting" from "key
    is wrong"."""
    respx_mock.get("http://ava/.well-known/agent-card.json").respond(status_code=502)
    delegate = Delegate(name="ava", description="hi", type="a2a", url="http://ava/a2a")
    r = await probe(delegate)
    assert r["ok"] is False
    assert r["status"] == 502
    assert "agent card HTTP" in r["error"]


# ---------------------------------------------------------------------------
# probe() — openai path delegated to ping_endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_openai_routes_through_ping_endpoint(respx_mock):
    respx_mock.post("https://gateway/v1/chat/completions").respond(
        status_code=200,
        json={"choices": [{"message": {"content": "pong"}}]},
    )
    delegate = Delegate(
        name="opus", description="hi", type="openai",
        url="https://gateway/v1", model="claude-opus-4-6",
        openai_api_key="sk-test",
    )
    r = await probe(delegate)
    assert r["ok"] is True


# ---------------------------------------------------------------------------
# DelegateRegistry health cache
# ---------------------------------------------------------------------------


def test_record_health_pass_resets_consecutive_failures():
    reg = DelegateRegistry(None)
    reg._items["ava"] = Delegate(name="ava", description="hi", type="a2a", url="http://x/a2a")
    reg.record_health("ava", ok=False, error="boom")
    reg.record_health("ava", ok=False, error="still boom")
    assert reg.health("ava").consecutive_failures == 2
    reg.record_health("ava", ok=True, latency_ms=42)
    h = reg.health("ava")
    assert h.consecutive_failures == 0
    assert h.ok is True
    assert h.latency_ms == 42


def test_record_health_increments_on_each_failure():
    reg = DelegateRegistry(None)
    reg._items["ava"] = Delegate(name="ava", description="hi", type="a2a", url="http://x/a2a")
    reg.record_health("ava", ok=False, error="first")
    assert reg.health("ava").consecutive_failures == 1
    reg.record_health("ava", ok=False, error="second")
    assert reg.health("ava").consecutive_failures == 2
    assert reg.health("ava").last_error == "second"


def test_health_returns_none_for_unprobed():
    reg = DelegateRegistry(None)
    assert reg.health("ava") is None


def test_all_health_snapshot_is_independent():
    """all_health() returns a copy; mutating it must NOT touch the live
    cache."""
    reg = DelegateRegistry(None)
    reg._items["ava"] = Delegate(name="ava", description="hi", type="a2a", url="http://x/a2a")
    reg.record_health("ava", ok=True, latency_ms=10)
    snap = reg.all_health()
    snap.pop("ava")
    assert reg.health("ava") is not None  # live cache untouched


# ---------------------------------------------------------------------------
# Reload prunes stale health
# ---------------------------------------------------------------------------


def test_reload_drops_health_for_removed_delegate(tmp_path):
    """Deleting a delegate from disk + reload() must drop its cached
    health — otherwise /healthz would report stale entries the user
    can't even see in the registry anymore."""
    p = tmp_path / "delegates.yaml"
    p.write_text(
        "delegates:\n"
        "  - name: ava\n    type: a2a\n    description: hi.\n    url: http://ava/a2a\n"
        "  - name: opus\n    type: openai\n    description: hi.\n    url: http://gw/v1\n    model: m\n",
    )
    reg = DelegateRegistry(p)
    reg.record_health("ava", ok=True, latency_ms=10)
    reg.record_health("opus", ok=True, latency_ms=12)

    p.write_text(
        "delegates:\n"
        "  - name: ava\n    type: a2a\n    description: hi.\n    url: http://ava/a2a\n",
    )
    reg.reload()
    assert reg.health("ava") is not None  # unchanged → kept
    assert reg.health("opus") is None     # removed → dropped


def test_reload_drops_health_when_url_changes(tmp_path):
    """Editing a delegate's URL invalidates its health cache — the
    previous green probe doesn't tell us anything about the new
    endpoint."""
    p = tmp_path / "delegates.yaml"
    p.write_text(
        "delegates:\n"
        "  - name: ava\n    type: a2a\n    description: hi.\n    url: http://old/a2a\n",
    )
    reg = DelegateRegistry(p)
    reg.record_health("ava", ok=True, latency_ms=10)

    p.write_text(
        "delegates:\n"
        "  - name: ava\n    type: a2a\n    description: hi.\n    url: http://new/a2a\n",
    )
    reg.reload()
    # Force re-probe — url changed
    assert reg.health("ava") is None


def test_reload_keeps_health_when_only_description_changes(tmp_path):
    """Description / system_prompt edits don't affect reachability,
    so the cache should survive — otherwise the SPA banner would
    flicker on every typo correction in the LLM-facing schema."""
    p = tmp_path / "delegates.yaml"
    p.write_text(
        "delegates:\n"
        "  - name: ava\n    type: a2a\n    description: original.\n    url: http://ava/a2a\n",
    )
    reg = DelegateRegistry(p)
    reg.record_health("ava", ok=True, latency_ms=10)

    p.write_text(
        "delegates:\n"
        "  - name: ava\n    type: a2a\n    description: revised.\n    url: http://ava/a2a\n",
    )
    reg.reload()
    h = reg.health("ava")
    assert h is not None
    assert h.ok is True  # description-only change preserves cache


# ---------------------------------------------------------------------------
# health_loop — integration over the registry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loop_records_results_and_can_be_cancelled(respx_mock):
    respx_mock.get("http://ava/.well-known/agent-card.json").respond(
        status_code=200, json={"name": "ava"},
    )
    reg = DelegateRegistry(None)
    reg._items["ava"] = Delegate(name="ava", description="hi", type="a2a", url="http://ava/a2a")

    task = asyncio.create_task(
        health_loop(reg, interval_secs=10.0, initial_delay_secs=0.0),
    )
    # Yield enough times for the first probe iteration to run.
    for _ in range(5):
        await asyncio.sleep(0.01)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    h = reg.health("ava")
    assert h is not None
    assert h.ok is True


@pytest.mark.asyncio
async def test_loop_survives_one_delegate_crashing(respx_mock, monkeypatch):
    """If probing one delegate raises, the loop keeps going for the
    others and records the failure for the broken one."""
    metrics.reset()
    respx_mock.get("http://good/.well-known/agent-card.json").respond(
        status_code=200, json={"name": "good"},
    )
    reg = DelegateRegistry(None)
    reg._items["good"] = Delegate(
        name="good", description="ok", type="a2a", url="http://good/a2a",
    )
    reg._items["bad"] = Delegate(
        name="bad", description="err", type="a2a", url="http://bad/a2a",
    )

    real_probe = __import__("agent.delegates", fromlist=["probe"]).probe

    async def _flaky_probe(delegate, *, timeout=8.0):
        if delegate.name == "bad":
            raise RuntimeError("simulated crash")
        return await real_probe(delegate, timeout=timeout)

    monkeypatch.setattr("agent.delegates.probe", _flaky_probe)

    task = asyncio.create_task(
        health_loop(reg, interval_secs=10.0, initial_delay_secs=0.0),
    )
    for _ in range(10):
        await asyncio.sleep(0.01)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert reg.health("good") is not None and reg.health("good").ok is True
    assert reg.health("bad") is not None and reg.health("bad").ok is False
    assert "simulated crash" in (reg.health("bad").last_error or "")
    assert metrics.snapshot()["counters"]["delegate_probe_crashed"] == 1


@pytest.mark.asyncio
async def test_default_loop_preserves_fleet_boot_grace(respx_mock):
    """The normal fleet loop must not touch delegates during startup."""
    respx_mock.get("http://ava/.well-known/agent-card.json").respond(
        status_code=503, text="warming up",
    )
    reg = DelegateRegistry(None)
    reg._items["ava"] = Delegate(name="ava", description="hi", type="a2a", url="http://ava/a2a")

    task = asyncio.create_task(health_loop(reg, interval_secs=10.0))
    await asyncio.sleep(0.05)
    assert reg.health("ava") is None
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass



@pytest.mark.asyncio
async def test_startup_probe_confirms_only_local_named_hub(monkeypatch):
    calls: list[str] = []
    timeouts: list[float] = []
    results = iter([
        {"ok": False, "error": "starting"},
        {"ok": False, "error": "still down"},
    ])

    async def _probe(delegate, *, timeout=8.0):
        calls.append(delegate.name)
        timeouts.append(timeout)
        return next(results)

    monkeypatch.setattr("agent.delegates.probe", _probe)
    reg = DelegateRegistry(None)
    reg._items["hub"] = Delegate(
        name="hub", description="brain", type="a2a",
        url="http://127.0.0.1:7870/a2a",
    )
    reg._items["coder"] = Delegate(
        name="coder", description="code", type="acp", command="codex",
    )
    reg._items["remote"] = Delegate(
        name="remote", description="fleet", type="a2a",
        url="https://agent.example/a2a",
    )

    await probe_local_hub_at_boot(reg, attempts=2, retry_delay_secs=0)

    assert calls == ["hub", "hub"]
    assert timeouts == [2.0, 2.0]
    assert reg.health("hub").consecutive_failures == 2
    assert reg.health("coder") is None
    assert reg.health("remote") is None


@pytest.mark.asyncio
async def test_startup_probe_publishes_only_final_confirmed_health(monkeypatch):
    results = iter([
        {"ok": False, "error": "starting"},
        {"ok": False, "error": "still down"},
    ])
    published = []

    async def _probe(delegate, *, timeout=8.0):
        return next(results)

    async def _publish(delegate, health):
        published.append((delegate.name, health))

    monkeypatch.setattr("agent.delegates.probe", _probe)
    reg = DelegateRegistry(None)
    reg._items["hub"] = Delegate(
        name="hub", description="brain", type="a2a",
        url="http://127.0.0.1:7870/a2a",
    )

    await probe_local_hub_at_boot(
        reg, attempts=2, retry_delay_secs=0, on_confirmed=_publish,
    )

    assert len(published) == 1
    name, health = published[0]
    assert name == "hub"
    assert health.ok is False
    assert health.consecutive_failures == 2


@pytest.mark.asyncio
async def test_startup_probe_suppresses_launch_race_after_recovery(monkeypatch):
    results = iter([
        {"ok": False, "error": "starting"},
        {"ok": True, "latency_ms": 4},
    ])

    async def _probe(delegate, *, timeout=8.0):
        return next(results)

    monkeypatch.setattr("agent.delegates.probe", _probe)
    reg = DelegateRegistry(None)
    reg._items["hub"] = Delegate(
        name="hub", description="brain", type="a2a",
        url="http://localhost:7870/a2a",
    )

    await probe_local_hub_at_boot(reg, attempts=2, retry_delay_secs=0)

    health = reg.health("hub")
    assert health.ok is True
    assert health.consecutive_failures == 0


@pytest.mark.asyncio
async def test_startup_probe_bounds_hung_attempts(monkeypatch):
    calls = 0

    async def _hung_probe(delegate, *, timeout=8.0):
        nonlocal calls
        calls += 1
        await asyncio.Event().wait()

    monkeypatch.setattr("agent.delegates.probe", _hung_probe)
    reg = DelegateRegistry(None)
    reg._items["hub"] = Delegate(
        name="hub", description="brain", type="a2a",
        url="http://127.0.0.1:7870/a2a",
    )
    started = asyncio.get_running_loop().time()

    await probe_local_hub_at_boot(
        reg, attempts=2, retry_delay_secs=0, timeout_secs=0.01,
    )

    elapsed = asyncio.get_running_loop().time() - started
    assert calls == 2
    assert elapsed < 0.1
    assert reg.health("hub").consecutive_failures == 2
    assert reg.health("hub").last_error == "startup probe timed out"


@pytest.mark.asyncio
async def test_startup_probe_recovers_after_delayed_first_attempt(monkeypatch):
    calls = 0

    async def _delayed_then_ready(delegate, *, timeout=8.0):
        nonlocal calls
        calls += 1
        if calls == 1:
            await asyncio.sleep(timeout * 2)
        return {"ok": True, "latency_ms": 3}

    monkeypatch.setattr("agent.delegates.probe", _delayed_then_ready)
    reg = DelegateRegistry(None)
    reg._items["hub"] = Delegate(
        name="hub", description="brain", type="a2a",
        url="http://127.0.0.1:7870/a2a",
    )

    await probe_local_hub_at_boot(
        reg, attempts=2, retry_delay_secs=0, timeout_secs=0.01,
    )

    assert calls == 2
    assert reg.health("hub").ok is True
    assert reg.health("hub").consecutive_failures == 0


@pytest.mark.asyncio
async def test_startup_probe_skips_remote_named_hub(monkeypatch):
    async def _unexpected_probe(delegate, *, timeout=8.0):
        raise AssertionError("remote hub must retain the fleet grace period")

    monkeypatch.setattr("agent.delegates.probe", _unexpected_probe)
    reg = DelegateRegistry(None)
    reg._items["hub"] = Delegate(
        name="hub", description="remote brain", type="a2a",
        url="https://hub.example/a2a",
    )

    await probe_local_hub_at_boot(reg, attempts=2, retry_delay_secs=0)

    assert reg.health("hub") is None


@pytest.mark.asyncio
async def test_loop_initial_delay_blocks_first_probe(respx_mock):
    """A non-zero initial delay means the cache stays empty until it
    elapses — operators can opt into this for a large remote fleet."""
    respx_mock.get("http://ava/.well-known/agent-card.json").respond(
        status_code=200, json={"name": "ava"},
    )
    reg = DelegateRegistry(None)
    reg._items["ava"] = Delegate(name="ava", description="hi", type="a2a", url="http://ava/a2a")

    task = asyncio.create_task(
        health_loop(reg, interval_secs=10.0, initial_delay_secs=10.0),
    )
    # Without enough time to clear the delay, no probe should have run.
    await asyncio.sleep(0.05)
    assert reg.health("ava") is None

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
# ---------------------------------------------------------------------------
# Fast-retry / jitter scheduling — _next_probe_delay
# ---------------------------------------------------------------------------


def test_next_probe_delay_uses_retry_steps_on_consecutive_failures():
    """Failing delegates skip the base interval for the first few retries
    so a transient blip recovers fast instead of staying red for 5min."""
    from agent.delegates import _RETRY_STEPS_SECS, _next_probe_delay

    assert _next_probe_delay(1, base_interval=300.0) == _RETRY_STEPS_SECS[0]
    assert _next_probe_delay(2, base_interval=300.0) == _RETRY_STEPS_SECS[1]
    assert _next_probe_delay(3, base_interval=300.0) == _RETRY_STEPS_SECS[2]


def test_next_probe_delay_falls_back_to_base_after_retry_budget():
    """Beyond the fast-retry window, settle into the base interval — a
    genuinely-down service shouldn't get hammered every 30 seconds."""
    from agent.delegates import _RETRY_STEPS_SECS, _next_probe_delay

    # consecutive_failures > len(retry_steps) → jittered base
    delay = _next_probe_delay(len(_RETRY_STEPS_SECS) + 1, base_interval=300.0)
    assert 270.0 <= delay <= 330.0  # 300 ± 10%


def test_next_probe_delay_jitters_healthy_interval():
    """Healthy delegates get the base interval ±10% to break up
    same-instant probe stampedes against shared backends."""
    from agent.delegates import _next_probe_delay

    # consecutive_failures = 0 → jittered base
    samples = [_next_probe_delay(0, base_interval=300.0) for _ in range(20)]
    assert all(270.0 <= s <= 330.0 for s in samples)
    # Samples should vary — if all identical, jitter isn't applied.
    assert len(set(samples)) > 1
