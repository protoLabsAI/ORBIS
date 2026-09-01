"""ORBIS-specific LLM failover policy."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from pipecat.frames.frames import (
    ErrorFrame,
    Frame,
    FunctionCallsStartedFrame,
    LLMContextFrame,
    LLMRunFrame,
    LLMTextFrame,
    UserStartedSpeakingFrame,
)
from pipecat.pipeline.llm_switcher import LLMSwitcher
from pipecat.pipeline.service_switcher import ServiceSwitcherStrategyFailover
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.llm_service import LLMService
from pipecat.utils.errors import ErrorCategory


_FALLBACK_ERRORS = frozenset(
    {
        ErrorCategory.AUTHENTICATION,
        ErrorCategory.AUTHORIZATION,
        ErrorCategory.CONNECTIVITY,
        ErrorCategory.SERVER,
        ErrorCategory.RATE_LIMIT,
        ErrorCategory.QUOTA,
    }
)
_RECOVERY_FRAMES = (LLMTextFrame, FunctionCallsStartedFrame)


class OrbisLLMFailoverStrategy(ServiceSwitcherStrategyFailover):
    """Extend Pipecat failover to selected provider and capacity errors.

    Pipecat handles permanent failures after a service marks itself unusable.
    ORBIS additionally tries each configured backup once for provider
    availability or capacity failures. Application errors, invalid requests,
    and unclassified errors propagate without switching.
    """

    def __init__(self, services: list[FrameProcessor]):
        super().__init__(services)
        self._attempted_services: set[FrameProcessor] = set()
        self._retry_callback: Callable[[], Awaitable[None]] | None = None
        self._retry_context_pending = False

    def begin_turn(self) -> None:
        """Begin a user-initiated LLM run with a fresh attempt budget."""
        self._attempted_services.clear()
        self._retry_context_pending = False

    def begin_llm_run(self) -> None:
        """Reset for a new run, except the context produced by our own retry."""
        if self._retry_context_pending:
            self._retry_context_pending = False
        else:
            self.begin_turn()

    def mark_recovered(self) -> None:
        """End the current failure incident after substantive LLM output."""
        self._attempted_services.clear()
        self._retry_context_pending = False

    def set_retry_callback(self, callback: Callable[[], Awaitable[None]]) -> None:
        """Install the task-level same-turn retry queue before processing starts."""
        self._retry_callback = callback

    async def handle_error(self, error: ErrorFrame) -> FrameProcessor | None:
        """Switch once per usable backup during the current LLM turn."""
        failed_service = error.processor or self.active_service
        self._attempted_services.add(failed_service)

        # Category policy wins over Pipecat's permanent/unusable flag. In
        # particular, INVALID_REQUEST must propagate even though Pipecat marks
        # that processor unusable before the strategy sees the ErrorFrame.
        if error.category not in _FALLBACK_ERRORS:
            return None

        # A switch is recoverable only if the unanswered turn can actually be
        # queued against the new member. Without this hook, let the error pass.
        if self._retry_callback is None:
            return None

        # Preserve Pipecat's permanent-error contract. The incident guard
        # prevents super() from wrapping to a member already tried here.
        if not failed_service.is_usable:
            candidate = self._next_usable_service()
            if candidate is None or candidate in self._attempted_services:
                return None
            switched = await super().handle_error(error)
        else:
            candidate = self._next_unattempted_usable_service()
            if candidate is None:
                return None
            switched = await self._set_active_if_available(candidate)
        if switched is not None:
            self._attempted_services.add(switched)
            self._retry_context_pending = True
            await self._retry_callback()
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


class OrbisLLMSwitcher(LLMSwitcher):
    """LLM switcher that scopes failover attempts to real LLM runs."""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        if direction is FrameDirection.DOWNSTREAM:
            if isinstance(frame, UserStartedSpeakingFrame):
                self.strategy.begin_turn()
            elif isinstance(frame, LLMContextFrame):
                self.strategy.begin_llm_run()
        await super().process_frame(frame, direction)

    async def push_frame(
        self,
        frame: Frame,
        direction: FrameDirection = FrameDirection.DOWNSTREAM,
    ):
        if direction is FrameDirection.DOWNSTREAM and isinstance(frame, _RECOVERY_FRAMES):
            self.strategy.mark_recovered()
        await super().push_frame(frame, direction)


def make_orbis_llm_switcher(llms: list[LLMService]) -> OrbisLLMSwitcher:
    """Build the production switcher with ORBIS's guarded failover policy."""
    return OrbisLLMSwitcher(llms=llms, strategy_type=OrbisLLMFailoverStrategy)


async def queue_failover_retry(
    *,
    note_failover: Callable[[], None],
    queue_frame: Callable[[Frame], Awaitable[None]],
) -> None:
    """Record a real switch and retry the unanswered turn exactly once."""
    note_failover()
    await queue_frame(LLMRunFrame())
