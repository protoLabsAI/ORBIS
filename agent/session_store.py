"""Cross-session persistence — summary + orphaned deliveries.

Two things survive a WebRTC disconnect:

  1. Rolling conversation summary (pipecat LLMContextSummarizer output) —
     used for session-open callbacks (Sesame "presence" finding).
  2. Pending push deliveries that couldn't land because the session ended
     — slow_research completing after disconnect, a2a push arriving with
     no active voice session, etc.

Both are file-backed per ``(user_id, skill_slug)``.

Layout:
  {STORE_DIR}/{user_id}/{skill_slug}.txt          ← plain text summary
  {STORE_DIR}/{user_id}/{skill_slug}.pending.json ← list of serialized _Pending

Legacy single-user paths ({STORE_DIR}/{skill_slug}.txt) are still read
on first access and auto-migrated to the default user's directory.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_DIR = Path(os.environ.get("SESSION_STORE_DIR", "/tmp/protovoice_sessions"))
_DEFAULT_USER_ID = "default"


def _safe(token: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in (token or ""))


def _user_dir(user_id: str) -> Path:
    return _DEFAULT_DIR / _safe(user_id or _DEFAULT_USER_ID)


def _summary_path(user_id: str, skill_slug: str) -> Path:
    return _user_dir(user_id) / f"{_safe(skill_slug)}.txt"


def _pending_path(user_id: str, skill_slug: str) -> Path:
    return _user_dir(user_id) / f"{_safe(skill_slug)}.pending.json"


def _legacy_summary_path(skill_slug: str) -> Path:
    return _DEFAULT_DIR / f"{_safe(skill_slug)}.txt"


def _legacy_pending_path(skill_slug: str) -> Path:
    return _DEFAULT_DIR / f"{_safe(skill_slug)}.pending.json"


def _migrate_legacy_if_present(
    user_id: str, skill_slug: str, legacy: Path, current: Path,
) -> None:
    """Auto-migrate pre-multi-tenant files into the default user's dir
    on first access. Only runs for the default user, so per-user paths
    for real users aren't polluted by a shared-history file."""
    if user_id != _DEFAULT_USER_ID:
        return
    if not legacy.exists() or current.exists():
        return
    try:
        current.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(legacy), str(current))
        logger.info(f"[session_store] migrated legacy {legacy.name} → {current}")
    except Exception as e:
        logger.warning(f"[session_store] legacy migration failed: {e}")


# --- Summary -----------------------------------------------------------------

def load_last_summary(user_id: str, skill_slug: str) -> str | None:
    p = _summary_path(user_id, skill_slug)
    _migrate_legacy_if_present(user_id, skill_slug, _legacy_summary_path(skill_slug), p)
    if not p.exists():
        return None
    try:
        text = p.read_text(encoding="utf-8").strip()
        return text or None
    except Exception as e:
        logger.warning(f"[session_store] failed to read {p}: {e}")
        return None


def save_summary(user_id: str, skill_slug: str, summary: str) -> None:
    if not summary or not summary.strip():
        return
    p = _summary_path(user_id, skill_slug)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(summary.strip(), encoding="utf-8")
        logger.info(
            f"[session_store] saved summary for ({user_id!r}, {skill_slug!r}) "
            f"({len(summary)} chars)"
        )
    except Exception as e:
        logger.warning(f"[session_store] failed to write {p}: {e}")


# --- Orphan deliveries -------------------------------------------------------

def stash_delivery(user_id: str, skill_slug: str, item: dict[str, Any]) -> None:
    """Append a single delivery (phrase + priority + keywords + source) to
    the user's orphan queue for this skill. Called when a push arrives
    with no active session OR when a live session shuts down with
    pending items."""
    p = _pending_path(user_id, skill_slug)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        existing: list[dict[str, Any]] = []
        if p.exists():
            try:
                existing = json.loads(p.read_text(encoding="utf-8"))
                if not isinstance(existing, list):
                    existing = []
            except Exception:
                existing = []
        existing.append(item)
        p.write_text(json.dumps(existing), encoding="utf-8")
        logger.info(
            f"[session_store] stashed delivery for ({user_id!r}, {skill_slug!r}) "
            f"— now {len(existing)} pending"
        )
    except Exception as e:
        logger.warning(f"[session_store] failed to stash {p}: {e}")


def drain_stashed_deliveries(user_id: str, skill_slug: str) -> list[dict[str, Any]]:
    """Load all orphan deliveries for this user+skill, delete the file,
    return the list. Called at session-connect time to replay what was
    missed."""
    p = _pending_path(user_id, skill_slug)
    _migrate_legacy_if_present(user_id, skill_slug, _legacy_pending_path(skill_slug), p)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            data = []
    except Exception as e:
        logger.warning(f"[session_store] failed to read {p}: {e}")
        data = []
    try:
        p.unlink()
    except Exception:
        pass
    logger.info(
        f"[session_store] drained {len(data)} stashed deliveries for "
        f"({user_id!r}, {skill_slug!r})"
    )
    return data
