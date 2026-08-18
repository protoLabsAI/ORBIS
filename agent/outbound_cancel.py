"""Verbal cancel of delegated work (#681) — the long-open "layer-2" cancel.

Barge-in has always stopped the *narration*; the delegated work itself kept
running because an in-flight dispatch couldn't be cancelled. With durable
task handles (#678 Phase B) it can: when the user says a cancel phrase while
delegated work is live, ``cancel_latest_outbound`` cancels the most recent
live task via A2A ``tasks/cancel``, clears any pending input-required ask
for it, marks the local handle ``canceled``, and speaks a short confirm.

The LOCAL row is marked canceled even when the remote cancel fails — the
user's intent is authoritative for what ORBIS delivers later: a remote that
finishes anyway won't have its stale answer spoken by the reconnect requery
(which only processes live rows).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def cancel_latest_outbound(registry) -> str | None:
    """Cancel the most recently dispatched live outbound task, if any.
    Returns the delegate name when a cancel was performed, else None.
    Never raises; the confirm/failure is spoken via the active session's
    DeliveryController."""
    from .delegate_adapters import _outbound_dal, get_adapter
    from .delegate_ask import _speak
    from .user_state import clear_delegate_ask

    dal = _outbound_dal()
    if dal is None:
        return None
    try:
        rows = dal.live()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[outbound-cancel] live() failed: {e}")
        return None
    if not rows:
        return None
    row = rows[-1]  # live() is ordered by created_at — newest last
    task_id, name = row["task_id"], row["delegate"]
    logger.info(f"[outbound-cancel] cancelling task={task_id} delegate={name}")

    clear_delegate_ask(task_id)
    # User intent wins locally regardless of the remote outcome (see module
    # docstring) — mark first so nothing re-delivers while we wait.
    try:
        dal.update(task_id, status="canceled")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[outbound-cancel] {task_id}: local update failed: {e}")

    delegate = registry.get(name) if registry is not None else None
    if delegate is None or delegate.type != "a2a":
        await _speak(f"Okay — dropped the {name} task.", source=name)
        return name
    try:
        client = get_adapter("a2a").client_for(delegate)
        await client.cancel(task_id)
        await _speak(f"Okay — told {name} to stop.", source=name)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[outbound-cancel] {task_id}: remote cancel failed: {e}")
        await _speak(
            f"I've dropped it on my side, but {name} didn't confirm the stop.",
            source=name,
        )
    return name
