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

    # HITL: a background orchestration run waiting on the user's spoken answer.
    pending_ask: PendingAsk | None = None

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


def take_pending_ask() -> PendingAsk | None:
    """Return + CLEAR the active session's pending ask (None if none). The voice
    AskGate calls this on a user transcript to route the answer to the run."""
    for st in _REGISTRY.all():
        if st.pending_ask is not None:
            pa, st.pending_ask = st.pending_ask, None
            return pa
    return None


def clear_pending_ask() -> None:
    """Drop any pending ask (e.g. on timeout / run teardown)."""
    for st in _REGISTRY.all():
        st.pending_ask = None
