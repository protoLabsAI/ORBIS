"""Unit tests for the sidecar process-group guard (#485).

We never call the real os.setsid()/os.killpg() here — that would detach the
test runner or kill it. Everything is exercised through the injectable seams.
"""

from __future__ import annotations

import os

from agent import process_guard as pg


# --- establish_own_process_group --------------------------------------------


def test_establish_group_returns_pgid_on_success(monkeypatch):
    monkeypatch.setattr(os, "setsid", lambda: None)          # succeeds, no-op
    monkeypatch.setattr(os, "getpgrp", lambda: 4242)
    assert pg.establish_own_process_group() == 4242


def test_establish_group_returns_none_when_setsid_fails(monkeypatch):
    def _boom():
        raise OSError("already a process group leader")

    monkeypatch.setattr(os, "setsid", _boom)
    # None → caller must NOT advertise a pgid → shell keeps the old kill path.
    assert pg.establish_own_process_group() is None


# --- watchdog ---------------------------------------------------------------


def test_watchdog_reaps_when_parent_changes():
    reaped: list[int] = []
    # getppid returns the original once, then a different pid (reparented).
    ppids = iter([1000, 1])

    pg._watchdog_loop(
        original_ppid=1000,
        pgid=777,
        poll_secs=0,
        getppid=lambda: next(ppids),
        reap=reaped.append,
        sleep=lambda _s: None,
    )
    assert reaped == [777]


def test_watchdog_stays_quiet_while_parent_alive():
    reaped: list[int] = []
    pg._watchdog_loop(
        original_ppid=1000,
        pgid=777,
        poll_secs=0,
        getppid=lambda: 1000,          # parent never changes
        reap=reaped.append,
        sleep=lambda _s: None,
        max_iters=5,                   # bounded so the loop terminates
    )
    assert reaped == []


def test_reap_group_swallows_missing_group(monkeypatch):
    def _boom(_pgid, _sig):
        raise ProcessLookupError("no such process group")

    monkeypatch.setattr(os, "killpg", _boom)
    # Must not raise — the group may already be gone.
    pg._reap_group(12345)
