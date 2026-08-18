"""Answer routing for delegated input-required tasks (#681).

When an A2A delegate (the protoAgent hub, a fleet agent) parks a task on
``input-required``, the adapter registers a :class:`~agent.user_state.
DelegateAsk` and the orb speaks the question. The voice AskGate then routes
the user's next transcript here instead of starting a fresh LLM turn:
``answer_delegate_ask`` sends the answer INTO THE SAME TASK (task id +
context preserved), updates the durable outbound-task row, and delivers the
delegate's follow-up out-of-band through the DeliveryController — the
original tool turn is long over, so there is no result_callback to feed.

If the delegate comes back with another question, the ask re-arms and the
loop continues; a terminal answer closes the row. Failures are spoken, not
swallowed — the user just answered a question and silence would read as the
answer having vanished.
"""

from __future__ import annotations

import logging
import time

from a2a_outbound import A2ADispatchError

from .user_state import DelegateAsk, register_delegate_ask_on_active

logger = logging.getLogger(__name__)

_ANSWER_TIMEOUT = 300.0
_PREVIEW = 500


def _active_delivery():
    from .user_state import active_user_states

    for st in active_user_states():
        return st.active_delivery
    return None


async def _speak(text: str, *, source: str) -> None:
    delivery = _active_delivery()
    if delivery is None:
        return
    try:
        from .delivery import Priority

        await delivery.deliver(text, priority=Priority.TIME_SENSITIVE, source=source)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[delegate-ask] delivery failed: {e}")


async def answer_delegate_ask(ask: DelegateAsk, answer: str, registry) -> None:
    """Send the user's spoken ``answer`` into ``ask``'s task. Never raises —
    every failure mode is spoken back."""
    from .delegate_adapters import _outbound_dal, get_adapter

    delegate = registry.get(ask.delegate) if registry is not None else None
    if delegate is None or delegate.type != "a2a":
        logger.warning(
            f"[delegate-ask] delegate {ask.delegate!r} gone; can't answer "
            f"task {ask.task_id}"
        )
        await _speak(
            f"I couldn't get that back to {ask.delegate} — it's not configured "
            "any more.",
            source=ask.delegate,
        )
        return
    logger.info(
        f"[delegate-ask] answering task={ask.task_id} delegate={ask.delegate} "
        f"answer={answer[:80]!r}"
    )
    try:
        client = get_adapter("a2a").client_for(delegate)
        res = await client.send(
            answer,
            task_id=ask.task_id,
            context_id=ask.context_id,
            timeout=_ANSWER_TIMEOUT,
        )
    except (A2ADispatchError, Exception) as e:  # noqa: BLE001
        logger.warning(f"[delegate-ask] answer to {ask.delegate} failed: {e}")
        await _speak(
            f"I couldn't get your answer through to {ask.delegate} — {e}",
            source=ask.delegate,
        )
        return

    dal = _outbound_dal()
    if dal is not None:
        try:
            dal.update(ask.task_id, status=res.state or "completed",
                       result=res.text or None)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[delegate-ask] outbound update failed: {e}")

    if res.input_required:
        # Another question — re-arm routing and speak it.
        register_delegate_ask_on_active(DelegateAsk(
            task_id=res.task_id or ask.task_id,
            delegate=ask.delegate,
            question=res.text or "",
            context_id=res.context_id or ask.context_id,
            created_at=time.time(),
        ))
        if res.text:
            await _speak(res.text[:_PREVIEW], source=ask.delegate)
        return
    if res.text:
        await _speak(res.text[:_PREVIEW], source=ask.delegate)


__all__ = ["answer_delegate_ask"]
