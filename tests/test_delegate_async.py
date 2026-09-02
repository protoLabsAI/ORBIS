"""delegate_to as a Pipecat-native async function call.

After the native-async refactor (docs/internal/delegation-native-async-refactor.md)
delegate_to is registered ``cancel_on_interruption=False``. The handler:
  - streams the delegate's REAL progress as intermediate is_final=False results,
  - returns the answer as the final result (is_final=True),
  - and never returns an immediate ack string (that was the out-of-order microack).

Pipecat handles the rest natively: the LLM continues immediately (the opening
filler is the single ack), narrates intermediate progress + the final answer
in-context, gated on not-user/not-bot-speaking.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import agent.tools as tools
from agent.delegates import DelegateError
from agent.tools import ASYNC_TOOL_NAMES, _delegate_to_handler, _orchestrate_handler


class FakeParams:
    def __init__(self, arguments):
        self.arguments = arguments
        # (result, is_final) for every result_callback the handler makes.
        self.results: list[tuple] = []

    async def result_callback(self, result, *, properties=None) -> None:
        is_final = getattr(properties, "is_final", True) if properties else True
        self.results.append((result, is_final))


class FakeRegistry:
    def __init__(self, delegate):
        self._d = delegate

    def get(self, name):
        return self._d if name == self._d.name else None

    def names(self):
        return [self._d.name]


class FakeDelivery:
    """Captures note_progress calls (the visual pill / delegation-progress SSE)."""

    def __init__(self):
        self.progress: list[tuple] = []
        self.events: list[dict] = []
        self._barge_epoch = 0
        self._owner_seq = 0
        self._owners: set[int] = set()

    async def note_progress(self, text, *, source=None):
        self.progress.append((text, source))

    @property
    def barge_epoch(self) -> int:
        return self._barge_epoch

    def bump_barge(self) -> None:
        self._barge_epoch += 1
        self._owners.clear()

    def begin_delegate_event_scope(self):
        self._owner_seq += 1
        self._owners.add(self._owner_seq)
        return self._barge_epoch, self._owner_seq

    def owns_delegate_event_scope(self, lease):
        return lease[0] == self._barge_epoch and lease[1] in self._owners

    def end_delegate_event_scope(self, lease):
        self._owners.discard(lease[1])

    async def note_delegate_event(self, event):
        self.events.append(event)


def _delegate():
    return SimpleNamespace(name="ava", type="a2a", description="chief of staff")


def test_delegate_to_is_async_tool() -> None:
    # Native async → cancel_on_interruption=False is keyed off this set.
    assert "delegate_to" in ASYNC_TOOL_NAMES


@pytest.mark.asyncio
async def test_returns_answer_as_single_final_result(monkeypatch) -> None:
    async def fake_dispatch(d, query, *, progress_callback=None, **kw):
        assert query == "are you online?"
        return "Yes, online."

    monkeypatch.setattr(tools, "delegate_dispatch", fake_dispatch)
    handler = _delegate_to_handler(FakeRegistry(_delegate()))
    params = FakeParams({"target": "ava", "query": "are you online?"})
    await handler(params)

    # Exactly one result, final, the answer — NO immediate ack string.
    assert params.results == [("Yes, online.", True)]


@pytest.mark.asyncio
async def test_progress_goes_to_the_pill_not_spoken(monkeypatch) -> None:
    # Real progress → the VISUAL pill (note_progress), NOT a spoken result. The
    # ONLY spoken result is the final answer → no double-narration, glanceable
    # progress while you wait.
    delivery = FakeDelivery()

    async def fake_dispatch(d, query, *, progress_callback=None, **kw):
        await progress_callback("routing to Quinn")
        await progress_callback("")  # blank → ignored
        await progress_callback("checking CI")
        return "Here's the roster."

    monkeypatch.setattr(tools, "delegate_dispatch", fake_dispatch)
    handler = _delegate_to_handler(FakeRegistry(_delegate()), delivery=delivery)
    params = FakeParams({"target": "ava", "query": "sit rep?"})
    await handler(params)

    # Progress on the pill, attributed to the target.
    assert delivery.progress == [("routing to Quinn", "ava"), ("checking CI", "ava")]
    # Only the answer is a spoken result.
    assert params.results == [("Here's the roster.", True)]


@pytest.mark.asyncio
async def test_progress_without_delivery_noops(monkeypatch) -> None:
    # Text mode (no DeliveryController): progress no-ops; just the answer.
    async def fake_dispatch(d, query, *, progress_callback=None, **kw):
        await progress_callback("routing to Quinn")
        return "done"

    monkeypatch.setattr(tools, "delegate_dispatch", fake_dispatch)
    handler = _delegate_to_handler(FakeRegistry(_delegate()))  # delivery=None
    params = FakeParams({"target": "ava", "query": "q"})
    await handler(params)

    assert params.results == [("done", True)]


@pytest.mark.asyncio
async def test_delegate_error_is_returned_as_final_result(monkeypatch) -> None:
    async def boom(d, query, *, progress_callback=None, **kw):
        raise DelegateError("connection refused")

    monkeypatch.setattr(tools, "delegate_dispatch", boom)
    handler = _delegate_to_handler(FakeRegistry(_delegate()))
    params = FakeParams({"target": "ava", "query": "x"})
    await handler(params)

    assert len(params.results) == 1
    text, is_final = params.results[0]
    assert is_final and "Couldn't reach ava" in text and "connection refused" in text


@pytest.mark.asyncio
async def test_missing_args_and_unknown_target(monkeypatch) -> None:
    called = False

    async def fake_dispatch(*a, **k):
        nonlocal called
        called = True
        return "x"

    monkeypatch.setattr(tools, "delegate_dispatch", fake_dispatch)
    handler = _delegate_to_handler(FakeRegistry(_delegate()))

    p1 = FakeParams({"target": "ava", "query": ""})
    await handler(p1)
    assert "need both" in p1.results[0][0].lower()

    p2 = FakeParams({"target": "nope", "query": "hi"})
    await handler(p2)
    assert "don't know a delegate" in p2.results[0][0].lower()
    assert called is False  # never dispatched on a bad request


@pytest.mark.asyncio
async def test_no_immediate_ack_before_dispatch_completes(monkeypatch) -> None:
    """The handler must NOT emit an ack result before the answer — that double-ack
    (filler + ack string) was the out-of-order microack. The only results are
    progress + the final answer."""
    started = asyncio.Event()

    async def slow_dispatch(d, query, *, progress_callback=None, **kw):
        started.set()
        await asyncio.sleep(0.02)
        return "done"

    monkeypatch.setattr(tools, "delegate_dispatch", slow_dispatch)
    handler = _delegate_to_handler(FakeRegistry(_delegate()))
    params = FakeParams({"target": "ava", "query": "q"})
    await handler(params)

    # No "on it / asking ava" ack string anywhere — just the final answer.
    assert params.results == [("done", True)]


@pytest.mark.asyncio
async def test_answer_dropped_when_barged_in_midflight(monkeypatch) -> None:
    """If the user talks over us while the delegate is in flight, the barge epoch
    advances and the (now-stale) answer is dropped rather than narrated into a
    turn that already moved on — delegate_dispatch can't be cancelled, so dropping
    the result is the gate."""
    delivery = FakeDelivery()

    async def fake_dispatch(d, query, *, progress_callback=None, **kw):
        delivery.bump_barge()  # user barges in mid-delegation
        return "stale answer the user already talked over"

    monkeypatch.setattr(tools, "delegate_dispatch", fake_dispatch)
    handler = _delegate_to_handler(FakeRegistry(_delegate()), delivery=delivery)
    params = FakeParams({"target": "ava", "query": "q"})
    await handler(params)

    # No spoken result — the superseded answer is dropped.
    assert params.results == []


@pytest.mark.asyncio
async def test_error_dropped_when_barged_in_midflight(monkeypatch) -> None:
    """The error narration is suppressed after a barge-in too — you don't hear
    'couldn't reach ava' for a delegation you already abandoned."""
    delivery = FakeDelivery()

    async def boom(d, query, *, progress_callback=None, **kw):
        delivery.bump_barge()
        raise DelegateError("connection refused")

    monkeypatch.setattr(tools, "delegate_dispatch", boom)
    handler = _delegate_to_handler(FakeRegistry(_delegate()), delivery=delivery)
    params = FakeParams({"target": "ava", "query": "x"})
    await handler(params)

    assert params.results == []


@pytest.mark.asyncio
async def test_delegate_events_and_progress_drop_after_cancellation(monkeypatch) -> None:
    delivery = FakeDelivery()

    async def fake_dispatch(
        d, query, *, progress_callback=None, event_callback=None, **kw,
    ):
        await event_callback({"type": "delegate.status", "task_id": "old"})
        delivery.bump_barge()
        await progress_callback("late progress")
        await event_callback({"type": "delegate.tool", "task_id": "old"})
        return "late answer"

    monkeypatch.setattr(tools, "delegate_dispatch", fake_dispatch)
    handler = _delegate_to_handler(FakeRegistry(_delegate()), delivery=delivery)
    params = FakeParams({"target": "ava", "query": "q"})
    await handler(params)

    assert delivery.events == [{"type": "delegate.status", "task_id": "old"}]
    assert delivery.progress == []
    assert params.results == []


@pytest.mark.asyncio
async def test_answer_kept_when_no_bargein(monkeypatch) -> None:
    """Sanity: epoch unchanged → the gate doesn't false-positive; the fresh
    answer still speaks exactly once."""
    delivery = FakeDelivery()

    async def fake_dispatch(d, query, *, progress_callback=None, **kw):
        return "fresh answer"

    monkeypatch.setattr(tools, "delegate_dispatch", fake_dispatch)
    handler = _delegate_to_handler(FakeRegistry(_delegate()), delivery=delivery)
    params = FakeParams({"target": "ava", "query": "q"})
    await handler(params)

    assert params.results == [("fresh answer", True)]


@pytest.mark.asyncio
async def test_delegate_event_owner_rejects_callback_after_handler_finishes(
    monkeypatch,
) -> None:
    delivery = FakeDelivery()
    callback = None

    async def fake_dispatch(d, query, *, event_callback=None, **kw):
        nonlocal callback
        callback = event_callback
        return "done"

    monkeypatch.setattr(tools, "delegate_dispatch", fake_dispatch)
    handler = _delegate_to_handler(FakeRegistry(_delegate()), delivery=delivery)
    params = FakeParams({"target": "ava", "query": "q"})
    await handler(params)
    assert callback is not None

    await callback({"type": "delegate.status", "task_id": "finished"})
    assert delivery.events == []


# --- orchestrate: the same barge-in gate on the multi-step loop ------------
# orchestrate runs a bounded ReAct loop that can't be torn down mid-flight, so
# the gate is identical to delegate_to: own an epoch-bound callback lease and
# drop progress, events, and synthesis if the user talked over the loop.


@pytest.mark.asyncio
async def test_orchestrate_answer_dropped_when_barged_in(monkeypatch) -> None:
    delivery = FakeDelivery()

    async def fake_runner(
        goal, *, progress=None, delegate_event=None, ask_user=None,
    ):
        await progress("working on step 1")  # visual pill — fine mid-flow
        delivery.bump_barge()  # user barges in while the loop runs
        return "the synthesized multi-step answer"

    handler = _orchestrate_handler(
        FakeRegistry(_delegate()), delivery=delivery, runner=fake_runner
    )
    params = FakeParams({"goal": "do the multi-step thing"})
    await handler(params)

    # Synthesis dropped; progress emitted before cancellation remains visible.
    assert params.results == []
    assert ("working on step 1", "orchestrator") in delivery.progress


@pytest.mark.asyncio
async def test_orchestrate_hitl_cancellation_drops_late_delegate_events() -> None:
    delivery = FakeDelivery()

    async def fake_runner(
        goal, *, progress=None, delegate_event=None, ask_user=None,
    ):
        await delegate_event({"type": "delegate.status", "task_id": "step-1"})
        # Mirrors Pipecat cancelling the tool while an orchestration/HITL pause
        # is outstanding. The durable remote task may still call back later.
        delivery.bump_barge()
        await delegate_event({"type": "delegate.tool", "task_id": "step-1"})
        await progress("late HITL progress")
        return "late synthesis"

    handler = _orchestrate_handler(
        FakeRegistry(_delegate()), delivery=delivery, runner=fake_runner
    )
    params = FakeParams({"goal": "ask then continue"})
    await handler(params)

    assert delivery.events == [{"type": "delegate.status", "task_id": "step-1"}]
    assert delivery.progress == []
    assert params.results == []


@pytest.mark.asyncio
async def test_orchestrate_answer_kept_when_no_bargein(monkeypatch) -> None:
    delivery = FakeDelivery()

    async def fake_runner(
        goal, *, progress=None, delegate_event=None, ask_user=None,
    ):
        return "the synthesized multi-step answer"

    handler = _orchestrate_handler(
        FakeRegistry(_delegate()), delivery=delivery, runner=fake_runner
    )
    params = FakeParams({"goal": "do the multi-step thing"})
    await handler(params)

    assert params.results == [("the synthesized multi-step answer", True)]
