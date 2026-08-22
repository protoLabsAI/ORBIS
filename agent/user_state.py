"""Per-user process state.

Replaces the module-level singletons (filler, delivery controller,
Langfuse tracer) with a dict keyed on ``user_id``. ORBIS is
single-user in practice (one owner per install) but the registry
shape is retained because it's cheap and makes the multi-device
flow — same user across phone + laptop — naturally work.

Usage:

    state = user_state_for(user_id)
    state.active_delivery = delivery_controller
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .delivery import DeliveryController
from .filler import FillerGenerator, Settings as FillerSettings

logger = logging.getLogger(__name__)


DEFAULT_USER_ID = "default"


@dataclass
class PendingAsk:
    """A paused orchestration run waiting on the user's spoken answer.

    When set, the voice AskGate resolves ``future`` with the user's NEXT
    transcript and swallows that frame, so the answer feeds the paused run
    instead of starting a normal LLM turn. Cleared when the run resumes (or the
    wait times out)."""
    question: str
    future: asyncio.Future


# How long a delegate's input-required question stays armed for answer
# routing. After this the ask expires and transcripts flow normally — the
# task itself stays input-required in the outbound registry, so the requery
# re-surfaces the question (and re-arms routing) on the next connect.
DELEGATE_ASK_TTL_SECS = 180.0


@dataclass
class DelegateAsk:
    """A delegated A2A task parked on ``input-required`` — the remote agent
    asked a question and the user's next transcript should answer THE TASK,
    not start a fresh LLM turn (#681, protoAgent-brain epic #678).

    Unlike the single-slot orchestrate ``PendingAsk``, these are keyed by
    task id — concurrent delegations can each park a question without
    clobbering; answers resolve oldest-first."""
    task_id: str
    delegate: str
    question: str
    context_id: str | None
    created_at: float  # time.time() — for the TTL

    def expired(self, *, now: float | None = None) -> bool:
        return ((now if now is not None else time.time()) - self.created_at
                > DELEGATE_ASK_TTL_SECS)


@dataclass
class UserState:
    """Everything that used to be a module-level singleton, per user."""
    user_id: str
    filler_settings: FillerSettings = field(default_factory=FillerSettings)
    filler_generator: FillerGenerator | None = None  # lazy via filler_gen()

    # Live voice session state. Populated in on_client_connected,
    # cleared in on_client_disconnected.
    active_delivery: DeliveryController | None = None
    active_tracer: Any | None = None
    active_session_id: str | None = None
    active_tts: Any | None = None  # live TTS service (for runtime voice switch)
    active_llm: Any | None = None  # live LLM service (for runtime model swap)

    # HITL: a background orchestration run waiting on the user's spoken answer.
    pending_ask: PendingAsk | None = None

    # HITL: delegated A2A tasks parked on input-required, keyed by task id
    # (insertion-ordered → answers resolve oldest-first). See DelegateAsk.
    pending_delegate_asks: dict[str, DelegateAsk] = field(default_factory=dict)

    # Hot-swap: re-render the live session's delegate roster (fleet-block prompt
    # + delegate_to/orchestrate tool schema) from the current registry, so a
    # delegate added/removed in Settings takes effect without a restart. Set by
    # run_bot when the pipeline is built; called by the /api/delegates endpoints.
    refresh_delegates: Any = None


class UserStateRegistry:
    def __init__(self) -> None:
        self._by_user: dict[str, UserState] = {}

    def get(self, user_id: str) -> UserState:
        st = self._by_user.get(user_id)
        if st is None:
            st = UserState(user_id=user_id)
            self._by_user[user_id] = st
        return st

    def drop(self, user_id: str) -> None:
        self._by_user.pop(user_id, None)

    def all(self) -> list[UserState]:
        return list(self._by_user.values())

    def active_sessions(self) -> list[UserState]:
        return [s for s in self._by_user.values() if s.active_delivery is not None]


_REGISTRY = UserStateRegistry()


def user_state_for(user_id: str) -> UserState:
    return _REGISTRY.get(user_id)


def all_user_states() -> list[UserState]:
    return _REGISTRY.all()


def active_user_states() -> list[UserState]:
    return _REGISTRY.active_sessions()


# --- HITL pending-ask routing (single-user: one active session, one ask) -----


def set_pending_ask_on_active(ask: PendingAsk) -> bool:
    """Park ``ask`` on the active voice session so the next user transcript
    answers the paused run. Returns False if there's no live session."""
    for st in _REGISTRY.active_sessions():
        st.pending_ask = ask
        return True
    return False


def has_pending_ask_on_active() -> bool:
    """True if the active session is parked on a HITL ``ask_user`` question.
    Presence narration checks this so the "still working" loop stays quiet while
    the agent is actually waiting on the USER to answer — not working."""
    for st in _REGISTRY.active_sessions():
        return st.pending_ask is not None
    return False


def take_pending_ask() -> PendingAsk | None:
    """Return + CLEAR the active session's pending ask (None if none). The voice
    AskGate calls this on a user transcript to route the answer to the run."""
    for st in _REGISTRY.all():
        if st.pending_ask is not None:
            pa, st.pending_ask = st.pending_ask, None
            return pa
    return None


# Turn-cancel signal — set when the user dismisses a turn ("cancel" / "stop
# listening", caught by CancelGate). The stall watchdog checks this before
# speaking its recovery line: a swallowed turn is intentionally empty, not a
# stall. Module-level (single active turn) and reliable — frame propagation
# from CancelGate to the end-of-pipeline watchdog isn't (system frames get
# consumed by the LLM/aggregators in between).
_last_turn_cancel: float = 0.0


def note_turn_cancel() -> None:
    """Record that the user just dismissed the current turn."""
    global _last_turn_cancel
    _last_turn_cancel = time.monotonic()


def turn_cancelled_since(t: float) -> bool:
    """True if a turn-cancel was signalled at or after monotonic time ``t``."""
    return _last_turn_cancel >= t


def clear_pending_ask() -> None:
    """Drop any pending ask (e.g. on timeout / run teardown)."""
    for st in _REGISTRY.all():
        st.pending_ask = None


# --- delegate input-required asks (keyed by task id, oldest-first) ----------


def register_delegate_ask_on_active(ask: DelegateAsk) -> bool:
    """Arm answer routing for a delegate's input-required question on the
    active session. Re-registering the same task id refreshes the question +
    TTL. Returns False when no session is live (the question still lives in
    the outbound-task registry; the reconnect requery re-arms it)."""
    for st in _REGISTRY.active_sessions():
        st.pending_delegate_asks.pop(ask.task_id, None)  # re-insert at the end
        st.pending_delegate_asks[ask.task_id] = ask
        logger.info(
            f"[delegate-ask] armed task={ask.task_id} delegate={ask.delegate} "
            f"question={ask.question[:80]!r}"
        )
        return True
    return False


def take_oldest_delegate_ask() -> DelegateAsk | None:
    """Pop the oldest non-expired delegate ask (None if none). Expired asks
    are pruned as they're encountered — after the TTL a transcript must flow
    to the LLM normally, not answer a question from minutes ago."""
    now = time.time()
    for st in _REGISTRY.all():
        while st.pending_delegate_asks:
            task_id = next(iter(st.pending_delegate_asks))
            ask = st.pending_delegate_asks.pop(task_id)
            if ask.expired(now=now):
                from agent import metrics
                metrics.inc("delegate_ask_timeout_total")
                metrics.inc(f"delegate_ask_timeout_total:{ask.delegate}")
                logger.info(f"[delegate-ask] expired task={task_id}; dropping")
                continue
            return ask
    return None


def clear_delegate_ask(task_id: str) -> None:
    """Drop the ask for ``task_id`` everywhere (e.g. the task was cancelled)."""
    for st in _REGISTRY.all():
        st.pending_delegate_asks.pop(task_id, None)
