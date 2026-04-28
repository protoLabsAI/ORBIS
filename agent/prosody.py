"""Prosody tag handling — Fish S2-Pro supports inline control tags
(`[softly]`, `[pause:300]`, `[hmm]`, `[thinking]`, `[whisper]`, etc.) and
SSML-style `<break time="300ms"/>`. These improve perceived naturalness
of the spoken output but are backend-specific:

  - **Fish Audio**: consumes the tags as prosody control.
  - **Kokoro**: strips them (speaks plain text, no tag support).
  - **OpenAI TTS**: strips them (plain text input).

Three consumers:

  - `strip_tags(text)` — pure function; keep as utility.
  - `ProsodyTextFilter` — pipecat `BaseTextFilter` passed to non-Fish TTS
    services via their `text_filters=` kwarg. Strips tags from the text
    handed to the synthesizer so Kokoro/OpenAI don't speak brackets.
  - `ProsodyTagStripper` — FrameProcessor placed after transport.output
    so TextFrames flowing to `assistant_agg` are clean, regardless of
    backend. Without it the LLM would see its own prosody markup in
    history and start riffing on it.
"""

from __future__ import annotations

import logging
import re

from pipecat.frames.frames import Frame, TextFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.utils.text.base_text_filter import BaseTextFilter

logger = logging.getLogger(__name__)


# Bracket tags: `[softly]`, `[pause:300]`, `[hmm]`, etc.
# Conservative — only lowercase ASCII words so we don't accidentally eat
# legitimate user text like `[Dr. Seuss]`.
_BRACKET_TAG_RE = re.compile(r"\[[a-z][a-z0-9_-]*(?::[^\]]*)?\]")

# SSML break tags: `<break time="300ms"/>` or `<break/>`.
_SSML_BREAK_RE = re.compile(r"<break\b[^/>]*/?>", re.IGNORECASE)

# Thinking / chain-of-thought blocks emitted by some models (Qwen3,
# DeepSeek-R1, etc.) that leak into the voice pipeline when
# enable_thinking is not suppressed at the model layer.
# Matches <think>…</think> including multi-line and partial open tags
# that haven't been closed yet.
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
# Partial open tag at the very end of a buffer (not yet closed).
_THINK_OPEN_RE = re.compile(r"<think>.*$", re.IGNORECASE | re.DOTALL)


def strip_tags(text: str) -> str:
    """Remove bracket prosody tags + SSML breaks from text. Safe for all
    TTS backends that don't speak tags — they'll just say plain words."""
    if not text:
        return text
    out = _BRACKET_TAG_RE.sub("", text)
    out = _SSML_BREAK_RE.sub("", out)
    # Collapse whitespace introduced by removed tags — but preserve single
    # newlines. Multiple spaces around a stripped tag → one space.
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out.strip(" \t")


def strip_think_blocks(text: str) -> str:
    """Remove complete <think>…</think> blocks and any unclosed <think>…
    tail. Used as a safety net regardless of enable_thinking setting."""
    if not text or "<think" not in text.lower():
        return text
    out = _THINK_BLOCK_RE.sub("", text)
    out = _THINK_OPEN_RE.sub("", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out.strip()


class ProsodyTextFilter(BaseTextFilter):
    """Strips prosody tags from text headed INTO a TTS service. Plug into
    non-Fish TTS services via the `text_filters=` kwarg so Kokoro / OpenAI
    never see brackets or SSML."""

    async def filter(self, text: str) -> str:
        return strip_tags(text)


class ProsodyTagStripper(FrameProcessor):
    """Strips Fish-style prosody tags from TextFrames so they don't end up
    in the LLM's context via the assistant aggregator. Pipeline must place
    this AFTER transport.output and BEFORE assistant_agg — TTS has already
    consumed the tagged text by then, and downstream we want clean text
    in the LLM's conversation history."""

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TextFrame) and frame.text:
            cleaned = strip_tags(frame.text)
            if cleaned != frame.text:
                frame.text = cleaned
        await self.push_frame(frame, direction)


class ThinkTagStripper(FrameProcessor):
    """Drops <think>…</think> blocks from LLM TextFrames before they reach
    TTS or the assistant aggregator.

    Some models (Qwen3, DeepSeek-R1, groq reasoning variants) emit
    chain-of-thought inside <think> tags even when thinking is nominally
    disabled. This processor accumulates text across frames so a block
    that spans multiple streamed chunks is also caught.

    Place BEFORE ProsodyTagStripper and TTS in the pipeline."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._buf = ""
        self._in_think = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if not (isinstance(frame, TextFrame) and frame.text):
            await self.push_frame(frame, direction)
            return

        self._buf += frame.text
        out = ""

        while self._buf:
            if self._in_think:
                end = self._buf.lower().find("</think>")
                if end == -1:
                    # Still inside think block, consume everything so far.
                    self._buf = ""
                    break
                # Found closing tag — skip past it.
                self._buf = self._buf[end + len("</think>"):]
                self._in_think = False
            else:
                start = self._buf.lower().find("<think>")
                if start == -1:
                    out += self._buf
                    self._buf = ""
                    break
                out += self._buf[:start]
                self._buf = self._buf[start + len("<think>"):]
                self._in_think = True

        if out:
            frame.text = out
            await self.push_frame(frame, direction)
        # else: frame was entirely think content — drop it silently
