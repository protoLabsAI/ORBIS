"""A dedicated, persistent credential for inbound A2A result callbacks.

Peers receive this scoped token, never ORBIS's operator credential. It only
opens POST /a2a/callback and survives app restarts so long-running tasks can
still deliver their results. A2A_PUSH_TOKEN remains an explicit override.
"""
from __future__ import annotations

import os
import secrets
import tempfile
from pathlib import Path

from agent.paths import get_db_path


def callback_token() -> str:
    override = os.environ.get("A2A_PUSH_TOKEN", "").strip()
    if override:
        return override
    path = get_db_path().with_name("a2a-callback-token")
    if path.exists():
        token = path.read_text().strip()
        if not token:
            raise ValueError("Empty A2A callback credential file")
        return token
    path.parent.mkdir(parents=True, exist_ok=True)
    # Publish a complete 0600 file without overwriting another process's key.
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".callback-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(32))
        try:
            os.link(tmp, path)
        except FileExistsError:
            pass
    finally:
        Path(tmp).unlink(missing_ok=True)
    return path.read_text().strip()
