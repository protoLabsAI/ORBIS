"""Inbox / say / reminders routes — extracted from app.py (#app.py-decomposition).

Library names import from their origin module; app-defined names via
`from app import`; the monkeypatched/mutable set as `app.<name>` at call
time (so live values + test monkeypatches win).
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, HTTPException, Request
import app

from agent.delivery import Priority
from auth import require_user, user_registry
from auth.users import User
from app import _A2A_USER_ID, logger


router = APIRouter()


def _inbox_writer_ok(request: Request) -> bool:
    """Owner key OR scoped ingest token."""
    x_api_key = request.headers.get("X-API-Key", "") or ""
    bearer = request.headers.get("Authorization", "") or ""
    if bearer.lower().startswith("bearer "):
        x_api_key = x_api_key or bearer[7:].strip()
    if user_registry.single_user_mode():
        return True
    if x_api_key and user_registry.resolve(x_api_key):
        return True
    if app.INBOX_INGEST_TOKEN and hmac.compare_digest(x_api_key, app.INBOX_INGEST_TOKEN):
        return True
    return False


@router.post("/api/inbox")
async def post_inbox(body: dict, request: Request):
    """Ingest a message into the agent inbox."""
    if not _inbox_writer_ok(request):
        raise HTTPException(status_code=401, detail="unauthorized")
    sender = (body.get("sender") or "").strip()
    subject = (body.get("subject") or "").strip()
    msg_body = body.get("body") or ""
    if not sender or not subject:
        raise HTTPException(
            status_code=400, detail="sender and subject are required",
        )
    channel = body.get("channel")
    created_at = body.get("created_at")
    priority = (body.get("priority") or "next").strip().lower()
    if priority not in ("now", "next", "later"):
        raise HTTPException(
            status_code=400,
            detail=f"priority must be one of now|next|later (got {priority!r})",
        )
    try:
        msg_id = app.get_memory().inbox.add(
            sender=sender,
            subject=subject,
            body=str(msg_body),
            channel=str(channel) if channel else None,
            priority=priority,  # type: ignore[arg-type]
            created_at=str(created_at) if created_at else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "id": msg_id}


_SAY_URGENCY_TO_PRIORITY = {
    "urgent": Priority.CRITICAL,       # interrupt now, even mid-speech
    "normal": Priority.TIME_SENSITIVE, # speak at the next natural pause
    "low": Priority.ACTIVE,            # surface when the topic comes up
}


@router.post("/api/say")
async def post_say(body: dict, request: Request):
    """Make the orb speak an externally-supplied message (orbis-wox).

    The external "ping ORBIS to speak / inhabit / message" primitive. Same
    auth as /api/inbox (owner key or ingest token). Unlike /api/inbox —
    which is pull-only and never proactively spoken — this routes straight
    into the DeliveryController so the orb actually voices it: immediately
    if a session is live (urgency-gated), else stashed and spoken on next
    connect.

    Body:
      text     — what to say (required)
      urgency  — urgent | normal (default) | low
      source   — optional attribution; if set, spoken as "<source> says — …",
                 if omitted the orb speaks it in its own voice (inhabit).
      voice    — optional Kokoro voice id (e.g. "af_bella"); speaks THIS
                 message in that voice and reverts after — so notifications
                 from different agents can each have their own voice without
                 changing the orb's own. Ignored if the voice is unknown.
    """
    if not _inbox_writer_ok(request):
        raise HTTPException(status_code=401, detail="unauthorized")
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    voice = (body.get("voice") or "").strip()
    voice_err = None
    if voice:
        from voice.tts.kokoro import KOKORO_VOICES, download_voice
        if voice not in KOKORO_VOICES:
            voice_err = f"unknown voice: {voice!r}"
            logger.warning(f"[say] {voice_err} — speaking in the current voice")
            voice = ""
        else:
            download_voice(voice)  # warm the tensor so the line doesn't stall
    urgency = (body.get("urgency") or "normal").strip().lower()
    if urgency not in _SAY_URGENCY_TO_PRIORITY:
        raise HTTPException(
            status_code=400,
            detail=f"urgency must be urgent|normal|low (got {urgency!r})",
        )
    priority = _SAY_URGENCY_TO_PRIORITY[urgency]
    source = (body.get("source") or "").strip() or None

    delivery = app.user_state_for(_A2A_USER_ID).active_delivery
    if delivery is None:
        # No live voice session — stash for replay on next connect (mirrors
        # the /a2a/push path: pre-attribute since replay passes source=None).
        from agent.session_store import stash_delivery
        attributed = f"{source} says — {text}" if source else text
        stash_delivery(_A2A_USER_ID, {
            "phrase": attributed,
            "policy": "now" if priority is Priority.CRITICAL else "next_silence",
            "priority": priority.value,
            "keywords": [],
        })
        return {"ok": True, "delivered": False, "stashed": True}

    await delivery.deliver(
        text, priority=priority, source=source, kind="ping",
        voice=voice or None,
    )
    resp = {"ok": True, "delivered": True}
    if voice:
        resp["voice"] = voice
    if voice_err:
        resp["voice_error"] = voice_err
    return resp


@router.get("/api/inbox")
async def get_inbox(
    user: User = Depends(require_user),
    unread_only: bool = False,
    priority_floor: str = "later",
    limit: int = 50,
):
    """List inbox messages, newest-first."""
    n = max(1, min(int(limit), 200))
    mem = app.get_memory()
    if unread_only:
        if priority_floor not in ("now", "next", "later"):
            raise HTTPException(
                status_code=400,
                detail=f"priority_floor must be now|next|later (got {priority_floor!r})",
            )
        msgs = mem.inbox.list_unread(
            priority_floor=priority_floor,  # type: ignore[arg-type]
            limit=n,
        )
    else:
        msgs = mem.inbox.list_all(limit=n)
    return {
        "messages": msgs,
        "unread_count": mem.inbox.count_unread(),
    }


@router.post("/api/inbox/deliver")
async def post_inbox_deliver(body: dict, user: User = Depends(require_user)):
    """Mark message ids as delivered. Body: ``{"ids": [1, 2, 3]}``."""
    raw_ids = body.get("ids") or []
    if not isinstance(raw_ids, list):
        raise HTTPException(status_code=400, detail="ids must be a list")
    ids: list[int] = []
    for v in raw_ids:
        try:
            ids.append(int(v))
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400, detail=f"invalid id: {v!r}",
            )
    n = app.get_memory().inbox.mark_delivered(ids)
    return {"ok": True, "delivered": n}


@router.get("/api/reminders")
async def get_reminders(user: User = Depends(require_user)):
    """List the user's pending reminders (one-time + recurring), soonest
    first. Powers the reminders UI and lets scripts inspect what's scheduled."""
    pend = app.get_memory().reminders.pending()
    return {"ok": True, "reminders": [
        {
            "id": r["id"],
            "text": r["text"],
            "fire_at": r["fire_at"],
            "recurring": bool(r.get("repeat_secs")),
            "repeat_secs": r.get("repeat_secs"),
        }
        for r in pend
    ]}


@router.post("/api/reminders/cancel")
async def cancel_reminders(body: dict, user: User = Depends(require_user)):
    """Cancel reminders. Body: ``{"id": 3}`` | ``{"match": "water"}`` |
    ``{"all": true}``. Cancelling stops recurring reminders too."""
    dal = app.get_memory().reminders
    if body.get("all"):
        return {"ok": True, "cancelled": dal.cancel_all()}
    if body.get("id") is not None:
        try:
            rid = int(body["id"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="id must be an integer")
        ok = dal.cancel(rid)
        return {"ok": ok, "cancelled": 1 if ok else 0}
    match = (body.get("match") or "").strip()
    if match:
        cancelled = dal.cancel_matching(match)
        return {"ok": True, "cancelled": len(cancelled),
                "items": [{"id": r["id"], "text": r["text"]} for r in cancelled]}
    raise HTTPException(status_code=400, detail="pass id, match, or all")
