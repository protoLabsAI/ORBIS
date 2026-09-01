"""ORBIS-specific LLM failover policy.

Pipecat 1.8 only fails over when a service has marked itself permanently
unusable. ORBIS's optional fallback LLM is also meant to cover transient
provider outages, so it deliberately switches on every error emitted by the
active LLM while still avoiding services Pipecat knows are unusable.
"""

from __future__ import annotations

from pipecat.frames.frames import ErrorFrame
from pipecat.pipeline.service_switcher import ServiceSwitcherStrategyFailover
from pipecat.processors.frame_processor import FrameProcessor


class OrbisLLMFailoverStrategy(ServiceSwitcherStrategyFailover):
    """Switch to the next usable LLM after any active-service error."""

    async def handle_error(self, error: ErrorFrame) -> FrameProcessor | None:
        """Preserve ORBIS's transient-outage failover across Pipecat versions."""
        current_idx = self.services.index(self.active_service)
        for offset in range(1, len(self.services)):
            candidate = self.services[(current_idx + offset) % len(self.services)]
            # ``is_usable`` was added in Pipecat 1.8. Keep compatibility with
            # the repo's declared >=1.5 range while honoring it when present.
            if getattr(candidate, "is_usable", True):
                return await self._set_active_if_available(candidate)
        return None
