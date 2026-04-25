"""Cross-session persistence for ORBIS.

Two things survive a WebRTC disconnect:

  1. Rolling conversation summary (pipecat ``LLMContextSummarizer``
     output) — used for session-open memory callbacks.
  2. Pending push deliveries that couldn't land because the session
     ended — async tool results completing after disconnect, A2A push
     arriving with no active voice session, etc.

Both are file-backed per user. ORBIS has one owner per install so in
practice there's one set of files; the per-user keying is retained so
multi-device / future multi-owner remains cheap to reach.

Layout::

    {STORE_DIR}/{user_id}/summary.txt          ← plain text summary
    {STORE_DIR}/{user_id}/pending.json          ← list of orphan deliveries

The SQLite memory backend (see ``agent/memory``) is the future home for
session summaries. This module stays for the orphan-delivery path and
as the text-summary fallback until the SQLite migration lands.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from collections.abc import Generator
from pathlib import Path
from typing import Any

# fcntl is POSIX-only. ORBIS desktop ships on macOS + Linux Docker so it's
# always present in supported environments; the import-guard keeps the
# module loadable on Windows for tests / ad-hoc dev where the locking
# becomes a no-op.
try:
    import fcntl as _fcntl
    _HAS_FCNTL = True
except ImportError:
    _fcntl = None  # type: ignore[assignment]
    _HAS_FCNTL = False

logger = logging.getLogger(__name__)

_DEFAULT_DIR = Path(os.environ.get("SESSION_STORE_DIR", "/tmp/orbis_sessions"))
_DEFAULT_USER_ID = "default"


def _safe(token: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in (token or ""))


def _user_dir(user_id: str) -> Path:
    return _DEFAULT_DIR / _safe(user_id or _DEFAULT_USER_ID)


def _summary_path(user_id: str) -> Path:
    return _user_dir(user_id) / "summary.txt"


def _pending_path(user_id: str) -> Path:
    return _user_dir(user_id) / "pending.json"


def _draining_path(user_id: str) -> Path:
    """Sidecar where pending.json gets atomic-renamed during drain so
    concurrent stashers can't observe a half-state."""
    return _user_dir(user_id) / "pending.json.draining"


def _lock_path(user_id: str) -> Path:
    return _user_dir(user_id) / ".queue.lock"


@contextlib.contextmanager
def _queue_lock(user_id: str) -> Generator[None, None, None]:
    """Cross-process advisory lock guarding stash/drain on a single
    user's queue. fcntl.flock is BSD-style on macOS/Linux — both writers
    must opt in for serialization to hold. ORBIS controls all writers
    (run_bot's disconnect handler + a2a/server's push handler), so this
    is sufficient to prevent the stash-during-drain race that otherwise
    duplicates or drops items.

    No-op on platforms without fcntl. Single-owner ORBIS makes that
    fallback safe in practice; the lock is defensive."""
    lp = _lock_path(user_id)
    lp.parent.mkdir(parents=True, exist_ok=True)
    if not _HAS_FCNTL:
        yield
        return
    with open(lp, "w") as f:
        try:
            _fcntl.flock(f, _fcntl.LOCK_EX)
            yield
        finally:
            with contextlib.suppress(Exception):
                _fcntl.flock(f, _fcntl.LOCK_UN)


# --- Summary -----------------------------------------------------------------

def load_last_summary(user_id: str) -> str | None:
    p = _summary_path(user_id)
    if not p.exists():
        return None
    try:
        text = p.read_text(encoding="utf-8").strip()
        return text or None
    except Exception as e:
        logger.warning(f"[session_store] failed to read {p}: {e}")
        return None


def save_summary(user_id: str, summary: str) -> None:
    if not summary or not summary.strip():
        return
    p = _summary_path(user_id)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(summary.strip(), encoding="utf-8")
        logger.info(
            f"[session_store] saved summary for {user_id!r} "
            f"({len(summary)} chars)"
        )
    except Exception as e:
        logger.warning(f"[session_store] failed to write {p}: {e}")


# --- Orphan deliveries -------------------------------------------------------

def stash_delivery(user_id: str, item: dict[str, Any]) -> None:
    """Append a single delivery (phrase + priority + keywords + source) to
    the user's orphan queue. Called when a push arrives with no active
    session OR when a live session shuts down with pending items.

    Uses fcntl-locked read-modify-write + atomic-rename so concurrent
    stashers (e.g. disconnect + a2a-push race) can't clobber each
    other. Single-owner makes this defensive in practice but POSIX-safe
    is the right shape."""
    p = _pending_path(user_id)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with _queue_lock(user_id):
            existing: list[dict[str, Any]] = []
            if p.exists():
                try:
                    existing = json.loads(p.read_text(encoding="utf-8"))
                    if not isinstance(existing, list):
                        existing = []
                except Exception:
                    existing = []
            existing.append(item)
            tmp = p.with_suffix(p.suffix + ".tmp")
            tmp.write_text(json.dumps(existing), encoding="utf-8")
            tmp.replace(p)  # atomic on POSIX
            logger.info(
                f"[session_store] stashed delivery for {user_id!r} "
                f"— now {len(existing)} pending"
            )
    except Exception as e:
        logger.warning(f"[session_store] failed to stash {p}: {e}")


def drain_stashed_deliveries(user_id: str) -> list[dict[str, Any]]:
    """Load all orphan deliveries for this user, delete the file, return
    the list. Called at session-connect time to replay what was missed.

    Uses atomic-rename (pending.json -> pending.json.draining) so the
    rename and unlink can fail independently without double-replay or
    losing items. Recovers stale .draining files from a previous drain
    that crashed mid-flight, so no items get permanently orphaned."""
    p = _pending_path(user_id)
    draining = _draining_path(user_id)
    result: list[dict[str, Any]] = []
    # Tracks whether stale .draining items were already absorbed inside
    # the lock. If unlink of that file fails AND no fresh pending.json
    # arrives to replace it, the file persists post-lock. Without this
    # flag, the post-lock branch would re-read it and double-replay
    # the stale items. Set False the moment the file is replaced (via
    # successful unlink OR via the pending.json atomic rename) so the
    # post-lock read picks up genuinely fresh content.
    absorbed_inside_lock = False
    try:
        with _queue_lock(user_id):
            # Recover items from a previous drain that crashed between
            # rename and unlink. They're already off the active queue;
            # absorbing them here just preserves them across restarts.
            if draining.exists():
                try:
                    stale = json.loads(draining.read_text(encoding="utf-8"))
                    if isinstance(stale, list):
                        result.extend(stale)
                        absorbed_inside_lock = True
                        logger.info(
                            f"[session_store] recovered {len(stale)} "
                            f"orphaned deliveries from prior drain for {user_id!r}"
                        )
                except Exception:
                    pass
                try:
                    draining.unlink()
                    # Unlink succeeded — the absorbed items are gone
                    # from disk; whatever appears at .draining next is
                    # genuinely new content (probably from the rename
                    # below).
                    absorbed_inside_lock = False
                except Exception:
                    pass
            if p.exists():
                try:
                    p.replace(draining)  # atomic on POSIX
                    # Fresh pending.json now lives at .draining and
                    # MUST be read post-lock — overrides any stale
                    # absorbed flag from above.
                    absorbed_inside_lock = False
                except FileNotFoundError:
                    pass
        # Outside the lock — we own .draining now via the atomic rename.
        # Only re-read if we didn't already absorb its contents inside
        # the lock; otherwise this would double-replay stale items
        # whose unlink failed.
        if draining.exists() and not absorbed_inside_lock:
            try:
                data = json.loads(draining.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    result.extend(data)
            except Exception as e:
                logger.warning(f"[session_store] failed to read {draining}: {e}")
            try:
                draining.unlink()
            except Exception:
                # Leaving .draining around is recoverable — next drain
                # will absorb it. Better than losing items by deleting
                # before we successfully extracted them.
                logger.warning(
                    f"[session_store] could not unlink {draining}; "
                    "next drain will recover"
                )
    except Exception as e:
        logger.warning(f"[session_store] drain failed for {user_id!r}: {e}")
    if result:
        logger.info(
            f"[session_store] drained {len(result)} stashed deliveries for {user_id!r}"
        )
    return result
