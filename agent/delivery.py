"""Push-delivery controller for async tool results.

Pipecat's native async-tool path (`cancel_on_interruption=False`) already
implements a version of "next_silence" — when the tool returns, pipecat
injects the result as a developer message and the LLM speaks it at the
next pipeline opportunity (which is ~the next user-silence).

This module layers two richer behaviours on top:

  now          — speak the result IMMEDIATELY via TTSSpeakFrame, even
                 interrupting the user. Use sparingly.
  next_silence — wait for the next VAD-detected user silence, then speak.
                 Same effect as pipecat's default for async tools, but gives
                 us control over phrasing (e.g. "by the way, here's what I
                 found about X...").
  when_asked   — suppress until the user references the topic; keyword
                 match against the original query.

The controller is implemented as a FrameProcessor so it can observe
UserStartedSpeaking / UserStoppedSpeaking / TranscriptionFrame without
needing transport event hooks (which don't fire for these).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum

from pipecat.frames.frames import (
    Frame,
    TranscriptionFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

logger = logging.getLogger(__name__)

# Signature: pushes a Frame into the PipelineTask from outside process_frame.
# app.py wires `task.queue_frame` into this so deliveries fire reliably no
# matter which asyncio context calls controller.deliver().
FrameEmitter = Callable[[Frame], Awaitable[None]]


class DeliveryPolicy(str, Enum):
    NOW = "now"
    NEXT_SILENCE = "next_silence"
    WHEN_ASKED = "when_asked"


class Priority(str, Enum):
    """Apple UNNotificationInterruptionLevel-shaped urgency. Callers
    specify this at enqueue time; the controller auto-maps it to a
    delivery policy. Explicit `policy=` still wins if supplied."""

    CRITICAL = "critical"             # hard-interrupt anything
    TIME_SENSITIVE = "time_sensitive" # drain on the next natural pause
    ACTIVE = "active"                 # wait for the user to reference it
    PASSIVE = "passive"               # low-signal; hold for later


# Auto-map priority → policy when caller doesn't force one.
_PRIORITY_TO_POLICY: dict[Priority, DeliveryPolicy] = {
    Priority.CRITICAL: DeliveryPolicy.NOW,
    Priority.TIME_SENSITIVE: DeliveryPolicy.NEXT_SILENCE,
    Priority.ACTIVE: DeliveryPolicy.WHEN_ASKED,
    # TODO: PASSIVE → dedicated DIGEST policy once the digest surface
    # exists. For now treat as when-asked (so it emits if the user brings
    # it up) but don't drain on silence.
    Priority.PASSIVE: DeliveryPolicy.WHEN_ASKED,
}


@dataclass
class _Pending:
    phrase: str
    policy: DeliveryPolicy
    priority: Priority = Priority.ACTIVE
    keywords: tuple[str, ...] = ()
    enqueued_at: float = field(default_factory=time.time)


import os as _os

# Silence (wait after UserStoppedSpeaking) before draining `next_silence`.
# Keeps the filler from stepping on the tail of the user's utterance.
_SILENCE_SETTLE_SECS = 0.6

# NEXT_SILENCE fallback — if the user never produces UserStoppedSpeaking
# (muted mic, zero talking), still drain after this many seconds. Solves
# the "user mutes and result never lands" edge case.
_NEXT_SILENCE_FALLBACK_SECS = float(
    _os.environ.get("DELIVERY_NEXT_SILENCE_FALLBACK_SECS", "10")
)

# WHEN_ASKED TTL — items that never match a keyword expire after this long
# so the pending queue doesn't accumulate forever.
_WHEN_ASKED_TTL_SECS = float(
    _os.environ.get("DELIVERY_WHEN_ASKED_TTL_SECS", "600")
)

# How often the watchdog ticks to check fallback timers + TTLs.
_WATCHDOG_TICK_SECS = 1.0

# Beyond this many pending items, low-priority stale ones get dropped so
# the drain doesn't turn into a monologue. ProMemAssist (UIST '25)
# backpressure-by-discard pattern.
_MAX_PENDING_AT_DRAIN = 3

# Priority rank (higher = more important) for drop ordering.
_PRIORITY_RANK: dict[Priority, int] = {
    Priority.CRITICAL: 4,
    Priority.TIME_SENSITIVE: 3,
    Priority.ACTIVE: 2,
    Priority.PASSIVE: 1,
}

# When 2+ NEXT_SILENCE items would drain together, ask the user first
# instead of flushing. CHI '24 ("Better to Ask Than Assume", Zhang et al.):
# users strongly prefer proactive-VAs that announce availability to ones
# that barge in with multiple results back-to-back.
_BID_THRESHOLD = 2

# Affirmative / negative responses to the bid. Naive substring match.
_BID_YES = (
    "yes", "yeah", "yep", "sure", "okay", "ok", "please",
    "go ahead", "hear them", "what are they", "tell me", "what",
)
_BID_NO = (
    "no", "nope", "not now", "skip", "later", "never mind", "nevermind",
    "drop it", "forget it",
)


class DeliveryController(FrameProcessor):
    """Tracks VAD state + user transcripts, drains pending deliveries."""

    def __init__(self) -> None:
        super().__init__()
        self._user_speaking = False
        self._pending: list[_Pending] = []
        self._settle_task: asyncio.Task | None = None
        self._watchdog_task: asyncio.Task | None = None
        # True once we've asked "want to hear them?" — stays true until
        # the user affirms or declines. Suppresses further NEXT_SILENCE
        # drains while awaiting response.
        self._bid_issued = False
        # Set by app.py after PipelineTask exists. Required for correct
        # out-of-band emission (push_frame from another coroutine context
        # is unsafe).
        self._emitter: FrameEmitter | None = None

    def set_emitter(self, emitter: FrameEmitter) -> None:
        self._emitter = emitter

    async def speak_now(self, phrase: str, *, source: str | None = None) -> None:
        """Push a TTSSpeakFrame immediately without touching the pending
        queue. For in-flight progress narration during long delegated
        tasks — different semantic from `deliver()`, which implies a
        FINAL result gated by a policy."""
        from agent import tracing
        with tracing.span(
            "delivery.speak_now",
            input={"source": source, "preview": phrase[:120]},
        ):
            await self._emit(_attribute(phrase, source))

    # --- Cross-session persistence helpers --------------------------------

    def snapshot_pending(self) -> list[dict[str, object]]:
        """Return a JSON-serializable list of currently-pending items.
        Used by app.py on disconnect to stash what didn't land in time."""
        return [
            {
                "phrase": p.phrase,
                "policy": p.policy.value,
                "priority": p.priority.value,
                "keywords": list(p.keywords),
                "enqueued_at": p.enqueued_at,
            }
            for p in self._pending
        ]

    async def replay_stashed(self, items: list[dict[str, object]]) -> None:
        """Enqueue stashed items (from a prior session) via the normal
        deliver() path. Phrases are already attributed; pass source=None
        so we don't double-wrap."""
        for raw in items:
            try:
                priority = Priority(raw.get("priority", Priority.ACTIVE.value))
                policy = DeliveryPolicy(raw["policy"]) if "policy" in raw else None
                phrase = raw["phrase"]
                keywords = tuple(raw.get("keywords") or ())
            except Exception as e:
                logger.warning(f"[delivery] skipping malformed stashed item: {e}")
                continue
            await self.deliver(phrase, priority=priority, policy=policy, keywords=keywords)

    # Public API — tool handlers call this to schedule a push delivery.
    async def deliver(
        self,
        phrase: str,
        *,
        priority: Priority = Priority.ACTIVE,
        policy: DeliveryPolicy | None = None,
        keywords: tuple[str, ...] = (),
        source: str | None = None,
    ) -> None:
        """Deliver `phrase` according to its urgency.

        Typical call: pass only `priority=` and let the controller map to
        the right policy. Pass explicit `policy=` to override.

        Policy semantics:
          NOW           — emit immediately (interrupts user if they're speaking)
          NEXT_SILENCE  — queue; emit when the user pauses
          WHEN_ASKED    — queue; emit only when the user's transcript
                          references one of `keywords`

        `source` — if provided and not our own agent, the phrase is
        prefixed with an attribution ("ava says — …"). CHI '23 data: voice
        attribution boosts trust after errors and makes delegated replies
        feel less like ours to own.
        """
        priority = Priority(priority)
        effective_policy = DeliveryPolicy(policy) if policy is not None else _PRIORITY_TO_POLICY[priority]
        attributed = _attribute(phrase, source)
        logger.info(
            f"[delivery] enqueue priority={priority.value} policy={effective_policy.value}"
            f"{f' source={source}' if source else ''}: {attributed[:60]!r}"
        )
        if effective_policy is DeliveryPolicy.NOW:
            await self._emit(attributed)
            return
        self._pending.append(
            _Pending(phrase=attributed, policy=effective_policy, priority=priority, keywords=keywords)
        )
        # Kick the watchdog if it's not already running. Handles
        # NEXT_SILENCE fallbacks + WHEN_ASKED TTLs.
        self._ensure_watchdog()
        # If the user isn't currently speaking and there's something eligible
        # for NEXT_SILENCE, drain right away.
        if not self._user_speaking:
            await self._drain_eligible(new_transcript=None)

    async def _emit(self, phrase: str) -> None:
        logger.info(f"[delivery] emit {phrase[:60]!r}")
        # append_to_context=False — push deliveries are spoken directly;
        # they shouldn't show up as part of the assistant's LLM history
        # (the underlying tool already injected its result via the proper
        # channel — see app.py llm.supports_developer_role=False).
        frame = TTSSpeakFrame(phrase, append_to_context=False)
        if self._emitter is not None:
            await self._emitter(frame)
        else:
            logger.warning(
                "[delivery] no emitter wired; falling back to push_frame"
            )
            await self.push_frame(frame)

    async def _drain_eligible(self, *, new_transcript: str | None) -> None:
        """Pop + emit any pending entries whose policy conditions are met.

        If multiple NEXT_SILENCE items would drain together, ask first
        rather than flushing (bid-then-drain). The user's next transcript
        resolves the bid.
        """
        self._prune_overflow()

        # Bid-then-drain gate: if ≥ _BID_THRESHOLD NEXT_SILENCE items are
        # pending and we haven't bid yet, emit a single bid phrase and
        # hold. Exception: CRITICAL / TIME_SENSITIVE priorities bypass
        # the bid — they need to land now.
        next_silence_items = [
            p for p in self._pending if p.policy is DeliveryPolicy.NEXT_SILENCE
        ]
        high_urgency = [
            p for p in next_silence_items
            if _PRIORITY_RANK.get(p.priority, 0) >= _PRIORITY_RANK[Priority.TIME_SENSITIVE]
        ]
        if (
            not self._bid_issued
            and len(next_silence_items) >= _BID_THRESHOLD
            and not high_urgency
        ):
            await self._emit(self._bid_phrase(next_silence_items))
            self._bid_issued = True
            return  # hold items; next transcript resolves

        remaining: list[_Pending] = []
        for item in self._pending:
            if item.policy is DeliveryPolicy.NEXT_SILENCE:
                if self._bid_issued and item not in high_urgency:
                    # User hasn't answered the bid yet; keep holding non-
                    # urgent items. High-urgency fall through and emit.
                    remaining.append(item)
                    continue
                await self._emit(item.phrase)
                continue
            if item.policy is DeliveryPolicy.WHEN_ASKED:
                if new_transcript and _keyword_match(new_transcript, item.keywords):
                    await self._emit(item.phrase)
                    continue
                remaining.append(item)
                continue
            # Unknown policy — drop silently rather than blocking pipeline.
            logger.warning(f"[delivery] unknown policy on pending item: {item.policy!r}")
        self._pending = remaining

    def _bid_phrase(self, items: list[_Pending]) -> str:
        """One-line bid announcing the held items. Attributes by source
        when we can, falls back to a count."""
        sources = sorted({
            p.phrase.split(" says — ")[0]
            for p in items
            if " says — " in p.phrase
        })
        if len(sources) >= 2:
            names = f"{', '.join(sources[:-1])} and {sources[-1]}"
            return f"I've got updates from {names} — want to hear them?"
        if len(sources) == 1:
            return (
                f"I've got {len(items)} updates, one from {sources[0]} — "
                "want to hear them?"
            )
        return f"I've got {len(items)} updates pending — want to hear them?"

    async def _resolve_bid(self, transcript: str) -> None:
        """Check the user's transcript against the held bid.
        Affirmative → drain; negative → clear; neither → stay held."""
        low = transcript.lower()
        if any(token in low for token in _BID_NO):
            logger.info("[delivery] bid declined — clearing held items")
            self._pending = [
                p for p in self._pending if p.policy is not DeliveryPolicy.NEXT_SILENCE
            ]
            self._bid_issued = False
            return
        if any(token in low for token in _BID_YES):
            logger.info("[delivery] bid accepted — draining")
            self._bid_issued = False
            await self._drain_eligible(new_transcript=None)

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, UserStartedSpeakingFrame):
            self._user_speaking = True
            # Cancel any in-flight settle — if the user starts again before
            # the settle delay elapses, hold everything back.
            if self._settle_task and not self._settle_task.done():
                self._settle_task.cancel()
        elif isinstance(frame, UserStoppedSpeakingFrame):
            self._user_speaking = False
            # Schedule a settle-delay drain.
            self._settle_task = asyncio.create_task(self._settle_then_drain())
        elif isinstance(frame, TranscriptionFrame) and frame.text:
            # Resolve a pending bid first (yes / no / neither).
            if self._bid_issued:
                await self._resolve_bid(frame.text)
            # Give `when_asked` entries a chance to match the new utterance.
            await self._drain_eligible(new_transcript=frame.text)

        await self.push_frame(frame, direction)

    # --- Watchdog for fallback timers + TTLs ------------------------------

    def _ensure_watchdog(self) -> None:
        if self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = asyncio.create_task(self._watchdog_loop())

    async def _watchdog_loop(self) -> None:
        """Ticks every _WATCHDOG_TICK_SECS. Flushes NEXT_SILENCE items
        whose age exceeds the fallback; drops WHEN_ASKED items past TTL.
        Exits when the queue drains (re-armed next time something is
        enqueued)."""
        try:
            while self._pending:
                await asyncio.sleep(_WATCHDOG_TICK_SECS)
                now = time.time()
                # NEXT_SILENCE fallback — user may have muted; force-drain.
                stale_next_silence = [
                    p for p in self._pending
                    if p.policy is DeliveryPolicy.NEXT_SILENCE
                    and (now - p.enqueued_at) >= _NEXT_SILENCE_FALLBACK_SECS
                ]
                for item in stale_next_silence:
                    logger.info(
                        f"[delivery] NEXT_SILENCE fallback ({int(now - item.enqueued_at)}s): "
                        f"{item.phrase[:60]!r}"
                    )
                    if item in self._pending:
                        self._pending.remove(item)
                        await self._emit(item.phrase)
                # WHEN_ASKED TTL — silently drop items that aged out.
                expired = [
                    p for p in self._pending
                    if p.policy is DeliveryPolicy.WHEN_ASKED
                    and (now - p.enqueued_at) >= _WHEN_ASKED_TTL_SECS
                ]
                for item in expired:
                    logger.info(
                        f"[delivery] WHEN_ASKED TTL expired after "
                        f"{int(now - item.enqueued_at)}s: {item.phrase[:60]!r}"
                    )
                    if item in self._pending:
                        self._pending.remove(item)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.exception(f"[delivery] watchdog crashed: {e}")

    def _prune_overflow(self) -> None:
        """Before draining, drop low-priority stale items if we have more
        than _MAX_PENDING_AT_DRAIN pending. Keeps the post-silence drain
        from turning into a monologue when the queue has piled up.

        Sort key: priority rank DESC (critical first), then recency DESC
        (newest first). Keep top-k; drop the tail. CRITICAL and
        TIME_SENSITIVE items are never dropped regardless of count —
        they're the ones the user actively needs to hear.
        """
        if len(self._pending) <= _MAX_PENDING_AT_DRAIN:
            return

        def sort_key(p: _Pending) -> tuple[int, float]:
            return (_PRIORITY_RANK.get(p.priority, 0), p.enqueued_at)

        sorted_items = sorted(self._pending, key=sort_key, reverse=True)
        keep: list[_Pending] = []
        dropped: list[_Pending] = []
        for item in sorted_items:
            if len(keep) < _MAX_PENDING_AT_DRAIN:
                keep.append(item)
            elif _PRIORITY_RANK.get(item.priority, 0) >= _PRIORITY_RANK[Priority.TIME_SENSITIVE]:
                keep.append(item)  # always keep important ones
            else:
                dropped.append(item)

        if dropped:
            logger.info(
                f"[delivery] prune dropped {len(dropped)} low-priority item(s): "
                + ", ".join(f"{d.priority.value}:{d.phrase[:40]!r}" for d in dropped)
            )
        # Preserve original enqueue order among kept items (not sort order).
        kept_ids = {id(k) for k in keep}
        self._pending = [p for p in self._pending if id(p) in kept_ids]

    async def _settle_then_drain(self) -> None:
        try:
            await asyncio.sleep(_SILENCE_SETTLE_SECS)
        except asyncio.CancelledError:
            return
        if self._user_speaking:
            return
        await self._drain_eligible(new_transcript=None)


def _keyword_match(text: str, keywords: tuple[str, ...]) -> bool:
    """Naive substring match — good enough for M3 validation.

    Replace with a proper semantic match in M4+ if we want to be less dumb
    about synonyms ("hot dogs" vs "frankfurters" etc).
    """
    if not keywords:
        return False
    low = text.lower()
    return any(k.lower() in low for k in keywords)


def _attribute(phrase: str, source: str | None) -> str:
    """Prefix a delivery phrase with its source if one is given and it's
    not our own turn. Uses "{source} says — …" framing; the em-dash +
    space prompts a natural Fish/Kokoro pause."""
    if not source:
        return phrase
    # Avoid double-attributing if a caller already included the source.
    low = phrase.lower()
    if low.startswith(f"{source.lower()} "):
        return phrase
    return f"{source} says — {phrase}"
