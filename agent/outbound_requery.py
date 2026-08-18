"""Reconnect requery of the durable outbound-task registry (#678 Phase B).

The registry (memory/outbound.py) records a handle for every task
dispatched to an A2A delegate. This module closes the loop on the ways a
result used to be lost: on session connect (and app boot behind it) every
live handle is requeried via A2A ``tasks/get`` —

  - finished while we were away  → deliver the answer through the
    DeliveryController ("<delegate> finished while you were away — …")
    and mark the row terminal;
  - waiting on input             → surface the question the same way;
  - still running                → touch the row (keeps the TTL honest)
    and leave it for the next requery or push-back;
  - unreachable / unknown        → log and leave it; the DAL's prune TTL
    expires handles whose remote never comes back.

Follows the push-only doctrine: this never blocks the connect path —
callers fire it as a background task — and each recovered RESULT is
delivered exactly once (the row goes terminal before delivery is
attempted, so a crash mid-delivery can't double-speak on the next
requery). An input-required handle deliberately stays live, so its
question re-surfaces on every reconnect until it's answered.
"""

from __future__ import annotations

import logging
from typing import Any

from a2a_outbound import A2ADispatchError

logger = logging.getLogger(__name__)

_PREVIEW = 350


async def requery_outbound(registry: Any, delivery: Any | None = None) -> int:
    """Requery every live outbound task against its delegate. Returns the
    number of rows resolved to a terminal state. Never raises."""
    from agent.delegate_adapters import _outbound_dal, get_adapter

    dal = _outbound_dal()
    if dal is None:
        return 0
    try:
        rows = dal.live()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[outbound] requery: live() failed: {e}")
        return 0
    if not rows:
        return 0
    logger.info(f"[outbound] requerying {len(rows)} live task(s)")
    resolved = 0
    for row in rows:
        name = row["delegate"]
        task_id = row["task_id"]
        delegate = registry.get(name)
        if delegate is None or delegate.type != "a2a":
            logger.warning(
                f"[outbound] {task_id}: delegate {name!r} gone/non-a2a; leaving for TTL"
            )
            continue
        try:
            client = get_adapter("a2a").client_for(delegate)
            res = await client.get_task(task_id)
        except (A2ADispatchError, Exception) as e:  # noqa: BLE001
            logger.warning(f"[outbound] {task_id}: requery via {name} failed: {e}")
            continue
        state = res.state or "working"
        try:
            dal.update(task_id, status=state, result=res.text or None)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[outbound] {task_id}: update failed: {e}")
            continue
        if res.is_terminal or res.input_required:
            resolved += 1
            logger.info(f"[outbound] {task_id}: {name} → {state}")
            if delivery is not None and res.text:
                lead = (
                    f"{name} needs input on the task from earlier — "
                    if res.input_required
                    else f"{name} finished while you were away — "
                )
                try:
                    from agent.delivery import Priority
                    await delivery.deliver(
                        lead + res.text[:_PREVIEW],
                        priority=Priority.TIME_SENSITIVE,
                        source=name,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[outbound] {task_id}: delivery failed: {e}")
    return resolved
