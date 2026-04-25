"""Tests for the bid-resolution parser in DeliveryController.

The parser decides whether the user's transcript accepts, declines, or is
neutral toward a held "I've got updates — want to hear them?" bid. The
bug we fix here: bare "what" used to be in the YES list with substring
matching, so "what time is it?" during a held bid resolved as accept and
flushed the queue. Word-boundary matching also blocks substrings like
"okayama" reading as "okay".
"""

from __future__ import annotations

import pytest

from agent.delivery import (
    _BID_NO_RE,
    _BID_YES_RE,
    DeliveryController,
    DeliveryPolicy,
    Priority,
    _Pending,
)


def _enqueue_two(ctrl: DeliveryController) -> None:
    """Stash two NEXT_SILENCE items so a flush is observable."""
    ctrl._pending.extend([
        _Pending(
            phrase="alice says — hello",
            policy=DeliveryPolicy.NEXT_SILENCE,
            priority=Priority.ACTIVE,
        ),
        _Pending(
            phrase="bob says — hi",
            policy=DeliveryPolicy.NEXT_SILENCE,
            priority=Priority.ACTIVE,
        ),
    ])
    ctrl._bid_issued = True


# --- regex sanity ----------------------------------------------------------


@pytest.mark.parametrize("text", [
    "yes",
    "Yes please",
    "yeah, go ahead",
    "okay tell me",
    "ok",
    "go ahead",
    "tell me",
    "what are they",
])
def test_yes_regex_accepts_real_affirmations(text: str) -> None:
    assert _BID_YES_RE.search(text) is not None


@pytest.mark.parametrize("text", [
    "no",
    "Nope, later",
    "skip it",
    "not now",
    "never mind",
    "drop it",
])
def test_no_regex_accepts_real_negations(text: str) -> None:
    assert _BID_NO_RE.search(text) is not None


@pytest.mark.parametrize("text", [
    # bare "what" is no longer accept (the R6 regression)
    "what time is it?",
    "what's the weather like?",
    "what do you mean",
    # substring traps the old code would have hit
    "yesterday I was at the park",   # "yes" inside "yesterday"
    "I'm in okayama",                # "okay" inside "okayama"
    "the okra is fresh",             # "ok" inside "okra"
    # neutral chatter unrelated to acceptance
    "hello there",
    "I'm thinking about it",
])
def test_yes_regex_rejects_false_positives(text: str) -> None:
    assert _BID_YES_RE.search(text) is None


@pytest.mark.parametrize("text", [
    "snowfall is heavy today",       # "no" inside "snowfall"
    "I know what to do",             # "know" contains "no"
    "the lateness was unusual",      # "later" inside "lateness"
])
def test_no_regex_rejects_false_positives(text: str) -> None:
    assert _BID_NO_RE.search(text) is None


# --- end-to-end via _resolve_bid ------------------------------------------
#
# Stub _drain_eligible on the instance so we can observe whether the YES
# branch was actually taken without entangling with the drain loop's own
# behavior (which is out of scope for R6).


def _stub_drain(ctrl: DeliveryController) -> list[bool]:
    """Replace _drain_eligible with a recorder. Returns the call log."""
    calls: list[bool] = []
    async def _fake(new_transcript=None):
        calls.append(True)
    ctrl._drain_eligible = _fake  # type: ignore[method-assign]
    return calls


@pytest.mark.asyncio
async def test_what_time_is_it_does_not_flush_held_bid() -> None:
    """The R6 regression — 'what time is it?' must not accept a bid."""
    ctrl = DeliveryController()
    _enqueue_two(ctrl)
    drain_calls = _stub_drain(ctrl)
    await ctrl._resolve_bid("what time is it?")
    assert ctrl._bid_issued is True
    assert len(ctrl._pending) == 2
    assert drain_calls == []


@pytest.mark.asyncio
async def test_real_yes_takes_accept_branch() -> None:
    ctrl = DeliveryController()
    _enqueue_two(ctrl)
    drain_calls = _stub_drain(ctrl)
    await ctrl._resolve_bid("yes please")
    assert drain_calls == [True], "yes should trigger _drain_eligible"


@pytest.mark.asyncio
async def test_real_no_clears_bid_and_drops_next_silence() -> None:
    ctrl = DeliveryController()
    _enqueue_two(ctrl)
    drain_calls = _stub_drain(ctrl)
    await ctrl._resolve_bid("no, not now")
    assert ctrl._bid_issued is False
    assert ctrl._pending == []
    assert drain_calls == [], "no should NOT trigger drain"


@pytest.mark.asyncio
async def test_neutral_holds_bid() -> None:
    """Genuinely ambiguous transcript (no keywords from either list)
    leaves the bid held."""
    ctrl = DeliveryController()
    _enqueue_two(ctrl)
    drain_calls = _stub_drain(ctrl)
    await ctrl._resolve_bid("hmm hold on a moment")
    assert ctrl._bid_issued is True
    assert len(ctrl._pending) == 2
    assert drain_calls == []
