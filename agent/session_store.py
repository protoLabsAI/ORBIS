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

import json
import logging
import os
from pathlib import Path
from typing import Any

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
    session OR when a live session shuts down with pending items."""
    p = _pending_path(user_id)
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
            f"[session_store] stashed delivery for {user_id!r} "
            f"— now {len(existing)} pending"
        )
    except Exception as e:
        logger.warning(f"[session_store] failed to stash {p}: {e}")


def drain_stashed_deliveries(user_id: str) -> list[dict[str, Any]]:
    """Load all orphan deliveries for this user, delete the file, return
    the list. Called at session-connect time to replay what was missed."""
    p = _pending_path(user_id)
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
        f"[session_store] drained {len(data)} stashed deliveries for {user_id!r}"
    )
    return data
