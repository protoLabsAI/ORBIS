"""Tests for the D1 orchestration loop (agent/orchestrate.py).

The LLM client and the A2A clients are faked — no network, no real model. We
assert the loop's control flow: delegate steps run through a per-run sticky
client, the final synthesis is returned, the step cap holds, progress is
narrated, input-required is surfaced, and a failing step doesn't abort the run.
"""

import json
from types import SimpleNamespace

import pytest

import agent.orchestrate as orch
from a2a.client import A2AResult
from agent.delegates import Delegate, DelegateRegistry


# --- fake LLM client -------------------------------------------------------

def _tool_call(call_id, name, args):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )


def _msg(content=None, tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls or None)


class _FakeLLM:
    """Returns scripted assistant messages in order from chat.completions.create."""

    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.calls = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        msg = self._scripted.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


# --- fake A2A client (patches agent.orchestrate.A2AClient) ------------------

class _FakeA2AClient:
    constructed: list = []

    def __init__(self, url, *, headers=None, card_origin=None, name=None):
        self.name = name
        self.sends = []
        _FakeA2AClient.constructed.append(self)

    async def send(self, query, **kw):
        self.sends.append(query)
        return A2AResult(
            text=f"{self.name}::{query}", state="completed",
            task_id="t", context_id="ctx",
        )


@pytest.fixture(autouse=True)
def _reset_fake():
    _FakeA2AClient.constructed = []


@pytest.fixture
def registry():
    reg = DelegateRegistry()
    reg._items["ava"] = Delegate(
        name="ava", description="ops agent", type="a2a", url="http://ava:3008/a2a"
    )
    reg._items["max"] = Delegate(
        name="max", description="research agent", type="a2a", url="http://max:3009/a2a"
    )
    return reg


def _run(monkeypatch, registry, scripted):
    monkeypatch.setattr(orch, "A2AClient", _FakeA2AClient)
    llm = _FakeLLM(scripted)
    return llm


# --- tests -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_single_step_then_synthesis(monkeypatch, registry):
    llm = _run(monkeypatch, registry, [
        _msg(tool_calls=[_tool_call("c1", "delegate_to", {"target": "ava", "query": "fleet status?"})]),
        _msg(content="The fleet is green; no incidents."),
    ])
    steps = []
    out = await orch.run_orchestration(
        "check the fleet", delegates=registry, client=llm, model="m",
        progress=lambda t: steps.append(t) or _noop(),
    )
    assert out == "The fleet is green; no incidents."
    assert len(_FakeA2AClient.constructed) == 1
    assert _FakeA2AClient.constructed[0].sends == ["fleet status?"]
    assert steps and "ava" in steps[0]


@pytest.mark.asyncio
async def test_same_delegate_reuses_one_sticky_client(monkeypatch, registry):
    # Two delegate_to calls to ava across two steps → ONE A2AClient (sticky ctx).
    llm = _run(monkeypatch, registry, [
        _msg(tool_calls=[_tool_call("c1", "delegate_to", {"target": "ava", "query": "q1"})]),
        _msg(tool_calls=[_tool_call("c2", "delegate_to", {"target": "ava", "query": "q2"})]),
        _msg(content="done"),
    ])
    out = await orch.run_orchestration(
        "two-parter", delegates=registry, client=llm, model="m",
    )
    assert out == "done"
    ava_clients = [c for c in _FakeA2AClient.constructed if c.name == "ava"]
    assert len(ava_clients) == 1                  # reused, not reconstructed
    assert ava_clients[0].sends == ["q1", "q2"]   # both turns same client


@pytest.mark.asyncio
async def test_two_different_delegates_get_separate_clients(monkeypatch, registry):
    llm = _run(monkeypatch, registry, [
        _msg(tool_calls=[
            _tool_call("c1", "delegate_to", {"target": "ava", "query": "a"}),
            _tool_call("c2", "delegate_to", {"target": "max", "query": "b"}),
        ]),
        _msg(content="compared"),
    ])
    out = await orch.run_orchestration("compare", delegates=registry, client=llm, model="m")
    assert out == "compared"
    assert {c.name for c in _FakeA2AClient.constructed} == {"ava", "max"}


@pytest.mark.asyncio
async def test_step_limit_returns_graceful_message(monkeypatch, registry):
    # Always calls a tool → never synthesises → hits the cap.
    forever = _msg(tool_calls=[_tool_call("c", "delegate_to", {"target": "ava", "query": "x"})])
    llm = _FakeLLM([forever] * 10)
    monkeypatch.setattr(orch, "A2AClient", _FakeA2AClient)
    out = await orch.run_orchestration(
        "endless", delegates=registry, client=llm, model="m", max_iter=3,
    )
    assert "didn't reach a clean answer" in out or "keep going" in out
    assert len(llm.calls) == 3  # capped


@pytest.mark.asyncio
async def test_unknown_delegate_is_reported_not_fatal(monkeypatch, registry):
    llm = _run(monkeypatch, registry, [
        _msg(tool_calls=[_tool_call("c1", "delegate_to", {"target": "ghost", "query": "?"})]),
        _msg(content="recovered"),
    ])
    out = await orch.run_orchestration("x", delegates=registry, client=llm, model="m")
    assert out == "recovered"  # loop continued past the bad step


@pytest.mark.asyncio
async def test_input_required_is_surfaced_into_the_loop(monkeypatch, registry):
    class _AskingClient(_FakeA2AClient):
        async def send(self, query, **kw):
            self.sends.append(query)
            return A2AResult(text="which environment?", state="input-required",
                             task_id="t", context_id="ctx", input_required=True)

    monkeypatch.setattr(orch, "A2AClient", _AskingClient)
    captured_tool_results = {}

    scripted = [
        _msg(tool_calls=[_tool_call("c1", "delegate_to", {"target": "ava", "query": "deploy"})]),
        _msg(content="asked for clarification"),
    ]
    llm = _FakeLLM(scripted)
    out = await orch.run_orchestration("deploy", delegates=registry, client=llm, model="m")
    # Second LLM call must have seen the input-required note as a tool message.
    second_call_msgs = llm.calls[1]["messages"]
    tool_msg = [m for m in second_call_msgs if m.get("role") == "tool"][-1]
    assert "needs more" in tool_msg["content"] and "which environment?" in tool_msg["content"]
    assert out == "asked for clarification"
    captured_tool_results.clear()


async def _noop():
    return None
