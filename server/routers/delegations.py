"""Delegation task listing — hydrates the delegations widget on open/reconnect.

Live updates reach the frontend over SSE (delegate.status / delegate.tool);
this endpoint is the snapshot half: live plus recently-finished rows from the
durable outbound-task registry (memory/outbound.py).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
import app

from auth import require_user
from auth.users import User

router = APIRouter()

_QUERY_PREVIEW = 160
_RESULT_PREVIEW = 160


def _clip(text: str | None, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


@router.get("/api/delegations")
async def list_delegations(
    limit: int = Query(20, ge=1, le=100),
    user: User = Depends(require_user),
):
    """Live + recently-terminal delegated tasks, newest first (bounded)."""
    rows = app.get_memory().outbound.recent(limit)
    return {
        "tasks": [
            {
                "task_id": r["task_id"],
                "delegate": r["delegate"],
                "query": _clip(r["query"], _QUERY_PREVIEW),
                "status": r["status"],
                "result": _clip(r["result"], _RESULT_PREVIEW) or None,
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]
    }
