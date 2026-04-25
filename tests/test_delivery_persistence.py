"""Tests for snapshot/replay round-trip of DeliveryController state.

Covers R9 — bid_issued used to be lost across reconnect because
snapshot_pending only emitted item rows, not the controller flag. Mid-
bid disconnect → reconnect would re-bid the user immediately.
"""

from __future__ import annotations

import pytest

from agent.delivery import (
    DeliveryController,
    DeliveryPolicy,
    Priority,
    _Pending,
)


def _enqueue_two_held_bid(ctrl: DeliveryController) -> None:
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


def test_snapshot_includes_bid_issued_flag() -> None:
    ctrl = DeliveryController()
    _enqueue_two_held_bid(ctrl)
    snap = ctrl.snapshot_pending()
    assert len(snap) == 2
    assert all(item["bid_issued"] is True for item in snap)


def test_snapshot_with_no_bid_records_false() -> None:
    ctrl = DeliveryController()
    ctrl._pending.append(
        _Pending(
            phrase="hi",
            policy=DeliveryPolicy.WHEN_ASKED,
            priority=Priority.PASSIVE,
        ),
    )
    snap = ctrl.snapshot_pending()
    assert snap[0]["bid_issued"] is False


def test_snapshot_empty_when_no_pending() -> None:
    ctrl = DeliveryController()
    ctrl._bid_issued = True  # nonsense state but tests the edge
    assert ctrl.snapshot_pending() == []


@pytest.mark.asyncio
async def test_replay_restores_bid_flag(monkeypatch) -> None:
    """The R9 regression — mid-bid reconnect must not re-bid."""
    src = DeliveryController()
    _enqueue_two_held_bid(src)
    snap = src.snapshot_pending()

    # Fresh controller (post-reconnect).
    dst = DeliveryController()
    # Don't actually drain — focus on the flag restore.
    drain_calls: list[bool] = []
    async def _fake_drain(new_transcript=None):
        drain_calls.append(True)
    dst._drain_eligible = _fake_drain  # type: ignore[method-assign]

    await dst.replay_stashed(snap)

    assert dst._bid_issued is True, \
        "bid_issued must survive snapshot/replay round-trip"
    assert len(dst._pending) == 2


@pytest.mark.asyncio
async def test_replay_without_bid_keeps_flag_false() -> None:
    src = DeliveryController()
    src._pending.append(
        _Pending(
            phrase="solo",
            policy=DeliveryPolicy.NEXT_SILENCE,
            priority=Priority.ACTIVE,
        ),
    )
    snap = src.snapshot_pending()

    dst = DeliveryController()
    drain_calls: list[bool] = []
    async def _fake_drain(new_transcript=None):
        drain_calls.append(True)
    dst._drain_eligible = _fake_drain  # type: ignore[method-assign]

    await dst.replay_stashed(snap)
    assert dst._bid_issued is False


@pytest.mark.asyncio
async def test_replay_handles_legacy_snapshot_without_bid_field() -> None:
    """Items written by a pre-R9 build don't carry bid_issued. Replay
    should treat the absence as False rather than crash."""
    legacy = [{
        "phrase": "old says — pending",
        "policy": "next_silence",
        "priority": "active",
        "keywords": [],
        "enqueued_at": 0.0,
        # no bid_issued field
    }]
    dst = DeliveryController()
    async def _fake_drain(new_transcript=None):
        pass
    dst._drain_eligible = _fake_drain  # type: ignore[method-assign]

    await dst.replay_stashed(legacy)

    assert dst._bid_issued is False
    assert len(dst._pending) == 1


@pytest.mark.asyncio
async def test_replay_skips_malformed_items_but_still_restores_flag() -> None:
    """If one item is malformed, the bid flag still restores from the
    valid items in the same batch."""
    snap = [
        {"this": "is broken"},  # missing required fields
        {
            "phrase": "good says — ok",
            "policy": "next_silence",
            "priority": "active",
            "keywords": [],
            "enqueued_at": 0.0,
            "bid_issued": True,
        },
    ]
    dst = DeliveryController()
    async def _fake_drain(new_transcript=None):
        pass
    dst._drain_eligible = _fake_drain  # type: ignore[method-assign]

    await dst.replay_stashed(snap)

    assert dst._bid_issued is True
    assert len(dst._pending) == 1
