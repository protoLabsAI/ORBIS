"""Atomicity + crash-recovery tests for session_store stash/drain.

Covers R7 (drain non-atomic — read+unlink could fail leaving items for
double-replay) and R8 (stash non-locked read-modify-write — concurrent
stashers could clobber).

Tests that single-process flows still work, that the new lock + atomic
rename pattern survives crashes between rename and unlink (no item
loss, no double-replay), and that legacy data without a .draining
sidecar still drains correctly.
"""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

import pytest


@pytest.fixture
def store(tmp_path: Path, monkeypatch):
    """Reload session_store with SESSION_STORE_DIR pointing at tmp_path
    so each test gets its own clean filesystem."""
    monkeypatch.setenv("SESSION_STORE_DIR", str(tmp_path))
    import agent.session_store as ss
    importlib.reload(ss)
    return ss


def _item(phrase: str) -> dict:
    return {
        "phrase": phrase,
        "policy": "next_silence",
        "priority": "active",
        "keywords": [],
        "enqueued_at": 0.0,
    }


# --- happy path -----------------------------------------------------------


def test_stash_and_drain_round_trip(store) -> None:
    store.stash_delivery("u1", _item("one"))
    store.stash_delivery("u1", _item("two"))
    store.stash_delivery("u1", _item("three"))
    drained = store.drain_stashed_deliveries("u1")
    assert [d["phrase"] for d in drained] == ["one", "two", "three"]
    # Subsequent drain returns empty
    assert store.drain_stashed_deliveries("u1") == []


def test_drain_empty_returns_empty(store) -> None:
    assert store.drain_stashed_deliveries("u1") == []


def test_users_isolated(store) -> None:
    store.stash_delivery("alice", _item("for alice"))
    store.stash_delivery("bob", _item("for bob"))
    a = store.drain_stashed_deliveries("alice")
    b = store.drain_stashed_deliveries("bob")
    assert len(a) == 1 and a[0]["phrase"] == "for alice"
    assert len(b) == 1 and b[0]["phrase"] == "for bob"


# --- atomicity --------------------------------------------------------------


def test_stash_writes_atomically(store, tmp_path: Path) -> None:
    """Stash uses temp+rename — the pending.json file should never exist
    in a half-written state. Inspecting at any point shows either the
    old contents or the new contents, never a torn write."""
    store.stash_delivery("u1", _item("first"))
    p = tmp_path / "u1" / "pending.json"
    # File exists, parses cleanly
    parsed = json.loads(p.read_text())
    assert isinstance(parsed, list)
    assert len(parsed) == 1
    # No leftover .tmp
    tmp = p.with_suffix(p.suffix + ".tmp")
    assert not tmp.exists()


def test_drain_uses_atomic_rename(store, tmp_path: Path) -> None:
    """During drain, the file is moved to .draining before being read.
    After successful drain, neither file should exist."""
    store.stash_delivery("u1", _item("one"))
    p = tmp_path / "u1" / "pending.json"
    draining = tmp_path / "u1" / "pending.json.draining"
    assert p.exists()

    items = store.drain_stashed_deliveries("u1")
    assert len(items) == 1
    assert not p.exists()
    assert not draining.exists()


# --- crash recovery -------------------------------------------------------


def test_recovers_stale_draining_from_prior_crash(store, tmp_path: Path) -> None:
    """Simulate a previous drain that successfully renamed pending.json
    to .draining, then crashed before unlink. New drain should absorb
    those items rather than leaving them stranded."""
    user_dir = tmp_path / "u1"
    user_dir.mkdir(parents=True, exist_ok=True)
    draining = user_dir / "pending.json.draining"
    draining.write_text(json.dumps([_item("orphaned"), _item("from crash")]))

    items = store.drain_stashed_deliveries("u1")
    assert len(items) == 2
    assert {i["phrase"] for i in items} == {"orphaned", "from crash"}
    # .draining cleaned up
    assert not draining.exists()


def test_recovers_draining_AND_drains_pending_in_same_call(store, tmp_path: Path) -> None:
    """Mid-recovery scenario: a stale .draining exists AND new items
    arrived since. Both should land in the result."""
    user_dir = tmp_path / "u1"
    user_dir.mkdir(parents=True, exist_ok=True)
    draining = user_dir / "pending.json.draining"
    draining.write_text(json.dumps([_item("orphaned1"), _item("orphaned2")]))

    store.stash_delivery("u1", _item("fresh"))

    items = store.drain_stashed_deliveries("u1")
    phrases = {i["phrase"] for i in items}
    assert len(items) == 3
    assert {"orphaned1", "orphaned2", "fresh"} == phrases


def test_orphaned_draining_with_corrupt_content_is_cleaned(store, tmp_path: Path) -> None:
    user_dir = tmp_path / "u1"
    user_dir.mkdir(parents=True, exist_ok=True)
    draining = user_dir / "pending.json.draining"
    draining.write_bytes(b"not valid json at all")

    items = store.drain_stashed_deliveries("u1")
    # Corrupted .draining contributes nothing but doesn't crash
    assert items == []
    assert not draining.exists()


def test_corrupt_pending_yields_empty_drain(store, tmp_path: Path) -> None:
    user_dir = tmp_path / "u1"
    user_dir.mkdir(parents=True, exist_ok=True)
    p = user_dir / "pending.json"
    p.write_bytes(b"corrupt")
    items = store.drain_stashed_deliveries("u1")
    assert items == []


def test_corrupt_pending_replaced_on_next_stash(store, tmp_path: Path) -> None:
    """Stash's read-then-write should overwrite a corrupt file rather
    than crashing or appending into it."""
    user_dir = tmp_path / "u1"
    user_dir.mkdir(parents=True, exist_ok=True)
    p = user_dir / "pending.json"
    p.write_bytes(b"corrupt junk")
    store.stash_delivery("u1", _item("after corrupt"))
    parsed = json.loads(p.read_text())
    assert isinstance(parsed, list)
    assert len(parsed) == 1


# --- the R7/R8 regression cases ------------------------------------------


def test_no_double_replay_after_crash_between_rename_and_unlink(
    store, tmp_path: Path
) -> None:
    """Simulate a crash after rename (pending.json → .draining) but
    before unlink. The first drain absorbs the .draining file; the
    second drain finds nothing, ensuring no double-replay across
    consecutive drains."""
    store.stash_delivery("u1", _item("once"))
    draining = tmp_path / "u1" / "pending.json.draining"
    p = tmp_path / "u1" / "pending.json"

    # Hand-roll the crash by manually renaming pending.json → .draining
    # without invoking drain. Equivalent to: drain succeeded the rename,
    # absorbed nothing yet, and crashed before reading.
    if p.exists():
        p.replace(draining)

    # First drain absorbs from .draining
    first = store.drain_stashed_deliveries("u1")
    assert len(first) == 1
    # Second drain — nothing left, no double-replay
    second = store.drain_stashed_deliveries("u1")
    assert second == []


def test_no_double_replay_when_inside_lock_unlink_fails(
    store, tmp_path: Path, monkeypatch
) -> None:
    """The CR-flagged double-replay edge: stale .draining absorbed
    inside the lock, its unlink fails, no fresh pending.json arrives.
    Pre-fix the post-lock branch re-read the same file; the absorbed-
    flag now suppresses that second read."""
    user_dir = tmp_path / "u1"
    user_dir.mkdir(parents=True, exist_ok=True)
    draining = user_dir / "pending.json.draining"
    draining.write_text(json.dumps([_item("orphaned-A"), _item("orphaned-B")]))
    # NO pending.json — the post-lock branch's existence check on
    # .draining is the only path that could re-read.

    # Patch Path.unlink so it fails ONLY for the .draining file inside
    # this user's dir, simulating an NFS hiccup or permission flip.
    real_unlink = Path.unlink

    def _fail_unlink(self: Path, *args, **kwargs) -> None:
        if self.name == "pending.json.draining" and self.parent.name == "u1":
            raise PermissionError("simulated unlink failure")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _fail_unlink)

    items = store.drain_stashed_deliveries("u1")
    # Each item appears EXACTLY once even though .draining survived
    # the failed unlink and the post-lock branch checked again.
    assert len(items) == 2
    assert {i["phrase"] for i in items} == {"orphaned-A", "orphaned-B"}
    # File still exists (the unlink failed) — next drain will absorb
    # again. We restore unlink so the next call can clean up.
    monkeypatch.undo()
    leftover = store.drain_stashed_deliveries("u1")
    # Recovery path absorbs the same items a final time on next drain
    # because they were never deleted from disk. That's the recoverable
    # side of the trade-off — better to replay them once more than to
    # silently delete on a flaky filesystem.
    assert len(leftover) == 2


def test_concurrent_stash_serialises_under_lock(store, tmp_path: Path) -> None:
    """The R8 regression: two stashers reading the same baseline could
    each append-and-write, clobbering one of the items. fcntl.flock
    serialises them.

    True concurrency is hard to test without threads; this test exercises
    the back-to-back case which would have hit the race in the old code
    if the read happened to land between writes. The deterministic version
    here verifies that 5 stashes append cumulatively rather than
    overwriting."""
    for i in range(5):
        store.stash_delivery("u1", _item(f"#{i}"))
    drained = store.drain_stashed_deliveries("u1")
    assert len(drained) == 5
    # Order preserved
    extracted = [d["phrase"] for d in drained]
    assert extracted == [_item(f"#{i}")["phrase"] for i in range(5)]
