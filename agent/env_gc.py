"""Garbage-collect stale pyapp sidecar environments (#489).

pyapp unpacks the sidecar into a content-hashed env dir:

    ~/Library/Application Support/pyapp/orbis/<hash>/<version>/python/...

Each env is ~1.8 GB. Every in-app update (UpdateNotice → downloadAndInstall →
relaunch) lands a new version → a fresh full env → and the OLD env stays on
disk forever. With near-daily releases that's multiple GB of dead weight and,
eventually, a bloated ``~/Library`` no one ever cleans.

The safe way to reap them is NOT to guess "which version is current" from a
string — it's to notice that **the running sidecar already lives inside its own
env dir**. ``sys.executable`` resolves to
``…/orbis/<hash>/<version>/python/bin/python`` at runtime, so we can keep
exactly the dir we're executing from and delete its siblings. No version
matching, no chance of deleting the env we're running out of.

Everything here is injectable (``base`` / ``executable`` / ``remove``) so the
reaping logic is unit-tested against a temp tree without touching a real
1.8 GB install. In dev (running from ``.venv``) ``current_env_dir`` returns
``None`` and the whole thing no-ops.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# The pyapp cache root for this project. pyapp keys by the project name
# ("orbis"); the macOS Application Support base is stable for the app.
DEFAULT_BASE = (
    Path.home() / "Library/Application Support/pyapp/orbis"
)


def current_env_dir(base: Path, executable: Path) -> Path | None:
    """Return the ``base/<hash>/<version>`` env dir the given executable lives
    in, or ``None`` if it isn't under ``base`` (e.g. a dev ``.venv`` run — in
    which case the caller must NOT GC anything, since we can't prove which env
    is live)."""
    try:
        rel = executable.resolve().relative_to(base.resolve())
    except (ValueError, OSError):
        return None
    parts = rel.parts  # (<hash>, <version>, "python", "bin", "python3")
    if len(parts) < 2:
        return None
    return base / parts[0] / parts[1]


def stale_env_dirs(base: Path, keep: Path) -> list[Path]:
    """Every ``base/<hash>/<version>`` env dir except ``keep``. Defensive: only
    returns dirs that actually look like a pyapp env (contain a ``python``
    subdir), so a stray file or a half-written dir under ``base`` is never a
    deletion target."""
    keep_resolved = keep.resolve()
    out: list[Path] = []
    if not base.is_dir():
        return out
    for hash_dir in sorted(base.iterdir()):
        if not hash_dir.is_dir():
            continue
        for ver_dir in sorted(hash_dir.iterdir()):
            if not ver_dir.is_dir():
                continue
            try:
                if ver_dir.resolve() == keep_resolved:
                    continue
            except OSError:
                continue
            if not (ver_dir / "python").exists():
                continue  # doesn't look like an env — leave it alone
            out.append(ver_dir)
    return out


def gc_stale_envs(
    *,
    base: Path = DEFAULT_BASE,
    executable: str | os.PathLike = sys.executable,
    remove=shutil.rmtree,
) -> list[Path]:
    """Delete every stale sidecar env under ``base``, keeping the one we're
    running from. Returns the list of dirs actually removed. Best-effort: a
    dir that won't delete (permissions, in use) is logged and skipped, never
    raised — GC must never take down a boot. No-ops (returns ``[]``) when the
    running executable isn't under ``base`` (dev runs)."""
    base = Path(base)
    exe = Path(executable)
    keep = current_env_dir(base, exe)
    if keep is None:
        logger.debug("[env-gc] executable not under %s — skipping", base)
        return []

    exe_resolved = str(exe.resolve())
    removed: list[Path] = []
    for d in stale_env_dirs(base, keep):
        # Hard guard: never remove a dir that is an ancestor of the running
        # executable (belt-and-suspenders on top of the keep check).
        d_prefix = str(d.resolve()) + os.sep
        if exe_resolved.startswith(d_prefix):
            logger.warning("[env-gc] refusing to remove live env ancestor %s", d)
            continue
        try:
            remove(d)
            removed.append(d)
            logger.info("[env-gc] removed stale sidecar env %s", d)
        except OSError as e:
            logger.warning("[env-gc] could not remove %s: %s", d, e)

    # Prune now-empty <hash> parents so the tree doesn't accumulate husks.
    for hash_dir in {d.parent for d in removed}:
        try:
            if hash_dir.is_dir() and not any(hash_dir.iterdir()):
                hash_dir.rmdir()
        except OSError:
            pass

    if removed:
        logger.info("[env-gc] reclaimed %d stale sidecar env(s)", len(removed))
    return removed
