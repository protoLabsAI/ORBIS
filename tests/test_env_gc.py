"""Unit tests for stale pyapp-env garbage collection (#489).

Builds a temp tree that mimics the real layout —
``base/<hash>/<version>/python/bin/python3`` — and proves the reaper keeps the
env it's *running from* and deletes only genuine sibling envs. No real 1.8 GB
install is touched; rmtree runs on the temp tree.
"""

from __future__ import annotations

from pathlib import Path

from agent import env_gc


def _make_env(base: Path, hash_id: str, version: str) -> Path:
    """Create base/<hash>/<version>/python/bin/python3 and return the exe path."""
    env = base / hash_id / version
    bindir = env / "python" / "bin"
    bindir.mkdir(parents=True)
    exe = bindir / "python3"
    exe.write_text("#!/bin/sh\n")
    return exe


# --- current_env_dir --------------------------------------------------------


def test_current_env_dir_locates_running_env(tmp_path):
    base = tmp_path / "pyapp" / "orbis"
    exe = _make_env(base, "hashA", "0.2.159")
    assert env_gc.current_env_dir(base, exe) == base / "hashA" / "0.2.159"


def test_current_env_dir_none_when_outside_base(tmp_path):
    base = tmp_path / "pyapp" / "orbis"
    base.mkdir(parents=True)
    # A dev .venv executable is nowhere near base → no GC.
    assert env_gc.current_env_dir(base, tmp_path / ".venv" / "bin" / "python") is None


def test_current_env_dir_none_when_too_shallow(tmp_path):
    base = tmp_path / "pyapp" / "orbis"
    base.mkdir(parents=True)
    # Something directly under base with no <version> component.
    assert env_gc.current_env_dir(base, base / "loose-file") is None


# --- stale_env_dirs ---------------------------------------------------------


def test_stale_env_dirs_excludes_keep_and_non_envs(tmp_path):
    base = tmp_path / "pyapp" / "orbis"
    keep_exe = _make_env(base, "hashA", "0.2.159")
    _make_env(base, "hashB", "0.2.158")
    _make_env(base, "hashA", "0.2.157")
    # A stray dir under base that is NOT an env (no python/) must be ignored.
    (base / "hashC" / "junk").mkdir(parents=True)

    keep = env_gc.current_env_dir(base, keep_exe)
    stale = set(env_gc.stale_env_dirs(base, keep))

    assert stale == {
        base / "hashB" / "0.2.158",
        base / "hashA" / "0.2.157",
    }
    assert keep not in stale
    assert base / "hashC" / "junk" not in stale


# --- gc_stale_envs ----------------------------------------------------------


def test_gc_removes_siblings_keeps_running_env(tmp_path):
    base = tmp_path / "pyapp" / "orbis"
    keep_exe = _make_env(base, "hashA", "0.2.159")
    _make_env(base, "hashB", "0.2.158")
    _make_env(base, "hashOld", "0.2.100")

    removed = env_gc.gc_stale_envs(base=base, executable=keep_exe)

    assert set(removed) == {base / "hashB" / "0.2.158", base / "hashOld" / "0.2.100"}
    # The running env survives; the stale ones are gone from disk.
    assert (base / "hashA" / "0.2.159").exists()
    assert not (base / "hashB").exists()  # empty <hash> parent pruned too
    assert not (base / "hashOld").exists()


def test_gc_noops_in_dev_venv(tmp_path):
    base = tmp_path / "pyapp" / "orbis"
    _make_env(base, "hashA", "0.2.159")  # an env exists...
    dev_exe = tmp_path / ".venv" / "bin" / "python"
    dev_exe.parent.mkdir(parents=True)
    dev_exe.write_text("")

    # ...but we're not running from it, so nothing is touched.
    removed = env_gc.gc_stale_envs(base=base, executable=dev_exe)
    assert removed == []
    assert (base / "hashA" / "0.2.159").exists()


def test_gc_never_removes_running_env(tmp_path):
    # The env the sidecar is executing from must never be handed to remove(),
    # no matter how many siblings exist.
    base = tmp_path / "pyapp" / "orbis"
    keep_exe = _make_env(base, "hashA", "0.2.159")
    _make_env(base, "hashB", "0.2.158")
    calls: list[Path] = []

    env_gc.gc_stale_envs(base=base, executable=keep_exe, remove=lambda p: calls.append(Path(p)))
    assert base / "hashA" / "0.2.159" not in calls
    assert calls == [base / "hashB" / "0.2.158"]  # only the sibling


def test_gc_best_effort_on_remove_error(tmp_path):
    base = tmp_path / "pyapp" / "orbis"
    keep_exe = _make_env(base, "hashA", "0.2.159")
    _make_env(base, "hashB", "0.2.158")

    def _boom(_p):
        raise OSError("device busy")

    # A dir that won't delete is skipped, not raised — boot must survive.
    removed = env_gc.gc_stale_envs(base=base, executable=keep_exe, remove=_boom)
    assert removed == []
