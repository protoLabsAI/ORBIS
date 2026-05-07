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
