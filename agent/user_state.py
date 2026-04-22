"""Per-user process state.

Replaces the module-level singletons (_ACTIVE_SKILL_SLUG, _ACTIVE_DELIVERY,
_ACTIVE_TRACER, _FILLER) with a dict keyed on ``user_id``. Each known
user gets their own:

  - active skill selection (the dropdown choice, per-user)
  - filler verbosity + generator (ambient chat level is per-user)
  - live voice session state (delivery controller, Langfuse tracer)

Usage:

    state = user_state_for(user_id)
    state.skill_slug = "chef"
    state.active_delivery = delivery_controller
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .delivery import DeliveryController
from .filler import FillerGenerator, Settings as FillerSettings

logger = logging.getLogger(__name__)


DEFAULT_USER_ID = "default"


@dataclass
class UserState:
    """Everything that used to be a module-level singleton, per user."""
    user_id: str
    skill_slug: str = ""                   # "" = use the global default
    filler_settings: FillerSettings = field(default_factory=FillerSettings)
    filler_generator: FillerGenerator | None = None  # lazy via filler_gen()

    # Live voice session state. Populated in on_client_connected,
    # cleared in on_client_disconnected.
    active_delivery: DeliveryController | None = None
    active_tracer: Any | None = None
    active_session_id: str | None = None


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
