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

from agent.delegates import (
    Delegate,
    DelegateRegistry,
    health_loop,
    probe,
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


@pytest.mark.asyncio
async def test_loop_initial_delay_blocks_first_probe(respx_mock):
    """A non-zero initial delay means the cache stays empty until it
    elapses — protects boot from probe stampedes when the LLM is
    still loading."""
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
