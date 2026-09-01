"""ORBIS-specific LLM failover policy."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from pipecat.frames.frames import ErrorFrame, Frame, LLMRunFrame
from pipecat.pipeline.llm_switcher import LLMSwitcher
from pipecat.pipeline.service_switcher import ServiceSwitcherStrategyFailover
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.services.llm_service import LLMService
from pipecat.utils.errors import ErrorCategory


FAILOVER_INCIDENT_SECS = 15.0
_AVAILABILITY_ERRORS = frozenset({ErrorCategory.CONNECTIVITY, ErrorCategory.SERVER})


class OrbisLLMFailoverStrategy(ServiceSwitcherStrategyFailover):
    """Extend Pipecat failover to transient provider-availability errors.

    Pipecat handles permanent failures after a service marks itself unusable.
    ORBIS additionally tries each configured backup once for connectivity and
    provider-server failures. Rate limits, quota failures, bad requests,
    application errors, and unclassified errors propagate without switching.
    """

    def __init__(self, services: list[FrameProcessor]):
        super().__init__(services)
        self._incident_started_at = float("-inf")
        self._attempted_services: set[FrameProcessor] = set()

    async def handle_error(self, error: ErrorFrame) -> FrameProcessor | None:
        """Switch once per usable backup within a bounded failure incident."""
        failed_service = error.processor or self.active_service
        now = time.monotonic()
        if now - self._incident_started_at > FAILOVER_INCIDENT_SECS:
            self._incident_started_at = now
            self._attempted_services = {failed_service}
        else:
            self._attempted_services.add(failed_service)

        # Preserve Pipecat's permanent-error contract. The incident guard
        # prevents super() from wrapping to a member already tried here.
        if not failed_service.is_usable:
            candidate = self._next_usable_service()
            if candidate is None or candidate in self._attempted_services:
                return None
            switched = await super().handle_error(error)
        elif error.category in _AVAILABILITY_ERRORS:
            candidate = self._next_unattempted_usable_service()
            if candidate is None:
                return None
            switched = await self._set_active_if_available(candidate)
        else:
            return None

        if switched is not None:
            self._attempted_services.add(switched)
        return switched

    def _next_unattempted_usable_service(self) -> FrameProcessor | None:
        current_idx = self.services.index(self.active_service)
        for offset in range(1, len(self.services)):
            candidate = self.services[(current_idx + offset) % len(self.services)]
            if candidate.is_usable and candidate not in self._attempted_services:
                return candidate
        return None

    def _next_usable_service(self) -> FrameProcessor | None:
        current_idx = self.services.index(self.active_service)
        for offset in range(1, len(self.services)):
            candidate = self.services[(current_idx + offset) % len(self.services)]
            if candidate.is_usable:
                return candidate
        return None


def make_orbis_llm_switcher(llms: list[LLMService]) -> LLMSwitcher:
    """Build the production switcher with ORBIS's guarded failover policy."""
    return LLMSwitcher(llms=llms, strategy_type=OrbisLLMFailoverStrategy)


async def queue_failover_retry(
    *,
    note_failover: Callable[[], None],
    queue_frame: Callable[[Frame], Awaitable[None]],
) -> None:
    """Record a real switch and retry the unanswered turn exactly once."""
    note_failover()
    await queue_frame(LLMRunFrame())
