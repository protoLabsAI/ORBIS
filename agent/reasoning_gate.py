"""Reasoning-tag gate — last line of defense against the orb speaking its
own chain-of-thought.

Reasoning should never reach the speech path at all: every LLM backend
disables it at the source (``enable_thinking: false``, Ollama ``think:
false``, qwen preamble parsing — see #645). But that protection is
per-backend config, and history shows a new model/gateway combo can leak
``<think>`` blocks into the *content* channel (protoAgent hit exactly this
with MiniMax via LiteLLM and keeps an equivalent stripper on its storage
path). Spoken aloud, a leaked reasoning block is the worst voice failure
mode we have, so the gate is unconditional rather than riding any backend
flag.

Placement: immediately BEFORE ``SpokenTextLogger`` + the TTS service, so
the ``[speak]`` log records what was actually spoken. Only streamed
``LLMTextFrame`` text is gated — ``TTSSpeakFrame``s are ORBIS-authored
(fillers, acks, DeliveryController) and can't contain model reasoning.
Because the gate mutates the streamed text, the assistant aggregator
downstream also never stores the reasoning — same guardrail protoAgent
enforces at its knowledge-store boundary.

Tags can arrive split across streamed chunks (``"<thi"`` + ``"nk>"``), so
the gate is stateful: it holds back a frame-tail that could be a partial
tag until the next chunk resolves it, and while inside a block it swallows
frames entirely. State resets on response start/end and on interruption.
"""

from __future__ import annotations

import logging

from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    InterruptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

logger = logging.getLogger(__name__)

# Open/close pairs, lowercase. Matching is case-insensitive.
_TAGS: dict[str, str] = {
    "<think>": "</think>",
    "<thinking>": "</thinking>",
    "<scratch_pad>": "</scratch_pad>",
    "<scratchpad>": "</scratchpad>",
    "<reasoning>": "</reasoning>",
}
_ALL_TAGS = list(_TAGS) + list(_TAGS.values())
_MAX_TAG_LEN = max(len(t) for t in _ALL_TAGS)


def _partial_tag_suffix(text: str) -> int:
    """Length of the longest suffix of ``text`` that is a proper prefix of
    some tag (i.e. might complete into a tag with more chunks). 0 if none."""
    low = text.lower()
    limit = min(len(low), _MAX_TAG_LEN - 1)
    for n in range(limit, 0, -1):
        suffix = low[-n:]
        if any(tag.startswith(suffix) for tag in _ALL_TAGS):
            return n
    return 0


class ReasoningGate:
    """Pure streaming filter: feed chunks, get speakable text back.

    Kept free of pipecat types so tests can drive it directly (same split
    as ``agent/tool_loop.py`` / ``agent/presence.py``: policy is a pure,
    testable core; the pipeline processor is a thin shell).
    """

    def __init__(self) -> None:
        self._pending = ""
        self._open_tag: str | None = None  # lowercase open tag when inside
        self.suppressed_chars = 0

    @property
    def in_block(self) -> bool:
        return self._open_tag is not None

    def reset(self) -> str:
        """Reset for a new response. Returns any held-back text that turned
        out not to be a tag (flush before the response boundary)."""
        tail = "" if self._open_tag else self._pending
        self._pending = ""
        self._open_tag = None
        self.suppressed_chars = 0
        return tail

    def feed(self, chunk: str) -> str:
        buf = self._pending + chunk
        self._pending = ""
        out: list[str] = []
        while buf:
            low = buf.lower()
            if self._open_tag is not None:
                close = _TAGS[self._open_tag]
                idx = low.find(close)
                if idx < 0:
                    # Still inside the block — swallow, keeping only a tail
                    # that might be the start of the close tag.
                    keep = _partial_tag_suffix(buf)
                    self.suppressed_chars += len(buf) - keep
                    self._pending = buf[len(buf) - keep:] if keep else ""
                    buf = ""
                else:
                    self.suppressed_chars += idx + len(close)
                    buf = buf[idx + len(close):]
                    self._open_tag = None
            else:
                # Find the earliest open tag, if any.
                first_idx = -1
                first_tag = ""
                for tag in _TAGS:
                    idx = low.find(tag)
                    if idx >= 0 and (first_idx < 0 or idx < first_idx):
                        first_idx, first_tag = idx, tag
                if first_idx < 0:
                    keep = _partial_tag_suffix(buf)
                    emit_end = len(buf) - keep
                    out.append(buf[:emit_end])
                    self._pending = buf[emit_end:] if keep else ""
                    buf = ""
                else:
                    out.append(buf[:first_idx])
                    self.suppressed_chars += len(first_tag)
                    buf = buf[first_idx + len(first_tag):]
                    self._open_tag = first_tag
        return "".join(out)


class ReasoningTagGate(FrameProcessor):
    """Pipeline shell around :class:`ReasoningGate` — gates streamed
    ``LLMTextFrame`` text headed into TTS."""

    def __init__(self) -> None:
        super().__init__()
        self._gate = ReasoningGate()

    def _flush_reset(self) -> str:
        if self._gate.suppressed_chars:
            # One line per affected response — greppable, mirrors [speak].
            logger.warning(
                f"[reasoning-gate] suppressed {self._gate.suppressed_chars} "
                "chars of leaked reasoning before TTS — a backend is emitting "
                "think tags in the content channel (check enable_thinking / "
                "think:false for the active model)"
            )
        return self._gate.reset()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, LLMTextFrame):
            if frame.text:
                speak = self._gate.feed(frame.text)
                if not speak:
                    return  # fully suppressed or held back — swallow
                frame.text = speak
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, (LLMFullResponseStartFrame, LLMFullResponseEndFrame,
                              InterruptionFrame)):
            tail = self._flush_reset()
            if tail and isinstance(frame, LLMFullResponseEndFrame):
                # Held-back text that never became a tag — speak it before
                # the response closes.
                await self.push_frame(LLMTextFrame(text=tail), direction)
        await self.push_frame(frame, direction)
