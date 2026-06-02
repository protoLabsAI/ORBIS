"""End-to-end test of the A2A 1.0 migration (ORBIS#340).

Closed loop: ORBIS's outbound ``A2AClient.send()`` → the a2a-sdk proto wire →
ORBIS's inbound SDK server (``register_a2a_routes`` + ``OrbisAgentExecutor``) →
back as an ``A2AResult``. Verifies the whole 1.0 stack (card with the 4 declared
extensions, executor bridging the producer-event stream, the SDK transport, and
the outbound adapter) round-trips through the real SDK + protolabs_a2a — no
network, via an ASGI transport.
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI

import a2a_outbound
from a2a_server import register_a2a_routes


async def _fake_stream(text, context_id, *, resume=False, caller_trace=None):
    # Exercises all 4 extensions: cost (usage), tool-call, worldstate-delta,
    # confidence — the executor must map each onto the wire without error.
    yield ("usage", {"input_tokens": 5, "output_tokens": 3, "model": "test-llm"})
    yield ("tool_start", {"id": "t1", "name": "schedule_reminder", "input": {"text": "call mom"}})
    yield ("tool_end", {"id": "t1", "name": "schedule_reminder", "output": "Reminder set."})
    yield ("delta", {"domain": "reminders", "path": "call mom", "op": "add", "value": {"text": "call mom"}})
    yield ("confidence", {"confidence": 0.9, "explanation": "resolved within the step budget"})
    yield ("done", f"echo: {text}")


def _mounted_app(monkeypatch) -> FastAPI:
    monkeypatch.setenv("A2A_ALLOW_UNAUTH", "1")  # open the gate for the test
    app = FastAPI()
    register_a2a_routes(app, text_stream_factory=_fake_stream, version="1.0.0")
    return app


@pytest.mark.asyncio
async def test_agent_card_declares_four_extensions(monkeypatch):
    app = _mounted_app(monkeypatch)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/.well-known/agent-card.json")
        assert r.status_code == 200
        body = json.dumps(r.json())
        for ext in ("cost-v1", "confidence-v1", "worldstate-delta-v1", "tool-call-v1"):
            assert ext in body, f"card must declare {ext}"


@pytest.mark.asyncio
async def test_closed_loop_send_returns_answer(monkeypatch):
    app = _mounted_app(monkeypatch)
    hc = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    client = a2a_outbound.A2AClient(
        "http://test/a2a", httpx_client=hc, card_origin="http://test", name="self"
    )
    try:
        res = await client.send("hello orbis")
    finally:
        await hc.aclose()

    assert res.text == "echo: hello orbis"
    assert res.state == "completed"
    assert res.input_required is False
    assert res.task_id and res.context_id


def test_terminal_parts_emits_cost_worldstate_confidence():
    """The terminal artifact carries the cost / worldstate-delta / confidence
    DataParts when there's data for them (the now-wired extensions)."""
    import protolabs_a2a as pa
    from a2a_executor import _terminal_parts

    parts = _terminal_parts(
        text="hi",
        deltas=[{"domain": "reminders", "path": "call mom", "op": "add", "value": {}}],
        usage={"input_tokens": 5, "output_tokens": 3},
        cost_usd=0.0,
        confidence=0.9,
        confidence_expl="resolved",
        success=True,
    )
    mimes = {p.metadata[pa.MIME_KEY] for p in parts if p.HasField("data")}
    assert pa.WORLDSTATE_DELTA_MIME in mimes
    assert pa.COST_MIME in mimes
    assert pa.CONFIDENCE_MIME in mimes
    # text part is present too (not a data part)
    assert any(p.HasField("text") for p in parts)
