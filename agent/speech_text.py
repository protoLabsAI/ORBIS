"""Conservative Markdown cleanup at the aggregated TTS text boundary."""

from __future__ import annotations

import re

from pipecat.utils.text.base_text_filter import BaseTextFilter


_LINK = re.compile(r"!?\[([^\]]+)\]\((?:[^()]|\([^()]*\))*\)")
_BOLD = re.compile(r"(?<!\w)(\*\*|__)(\S(?:.*?\S)?)\1(?!\w)", re.DOTALL)
_ITALIC = re.compile(
    r"(?<!\w)([*_])(?=\S)(.+?)(?<=\S)\1(?!\w)",
    re.DOTALL,
)
# Bold can straddle sentence boundaries. Remove its unmatched markers too,
# without eating operators such as `2 ** 3` or word-internal underscores.
_BOLD_EDGE = re.compile(r"(?<!\w)(?:\*\*|__)(?=\S)|(?<=\S)(?:\*\*|__)(?!\w)")
_STAR_EDGE = re.compile(r"(?<!\w)\*(?=[A-Za-z])|(?<=[A-Za-z.!?])\*(?!\w)")
_FENCE = re.compile(r"(?m)^[ \t]{0,3}(?:`{3,}|~{3,})[^\n]*(?:\n|$)")
_HEADER = re.compile(r"(?m)^[ \t]{0,3}#{1,6}[ \t]+")
_BULLET = re.compile(r"(?m)^[ \t]*[-*+•][ \t]+")
_ORDERED = re.compile(r"(?m)^[ \t]*\d+[.)][ \t]+")
_QUOTE = re.compile(r"(?m)^[ \t]*>[ \t]?")
_RULE = re.compile(r"(?m)^[ \t]*(?:-{3,}|\*{2,}|_{2,})[ \t]*$")
_INLINE_CODE = re.compile(r"(`+)([^`\n]+)\1")
_STRIKE = re.compile(r"~~(\S(?:.*?\S)?)~~", re.DOTALL)
_TABLE_DIVIDER = re.compile(
    r"(?m)^[ \t]*\|?[ \t]*:?-{3,}:?[ \t]*(?:\|[ \t]*:?-{3,}:?[ \t]*)+\|?[ \t]*$"
)
_TABLE_ROW = re.compile(r"(?m)^[ \t]*\|(.+?)\|[ \t]*$")


def strip_markdown_for_speech(text: str) -> str:
    """Keep the words, identifiers and arithmetic; drop display formatting."""
    if not text:
        return text
    text = _FENCE.sub("", text)
    text = _RULE.sub("", text)
    # Inline code can contain meaningful stars or underscores (`*args`,
    # `__init__`). Strip its backticks, not the contents.
    code: list[str] = []

    def keep_code(match: re.Match) -> str:
        code.append(match.group(2))
        return f"\x00{len(code) - 1}\x00"

    text = _INLINE_CODE.sub(keep_code, text)
    text = _LINK.sub(r"\1", text)
    if _TABLE_DIVIDER.search(text):
        text = _TABLE_DIVIDER.sub("", text)
        text = _TABLE_ROW.sub(
            lambda match: ", ".join(cell.strip() for cell in match.group(1).split("|")),
            text,
        )
    text = _STRIKE.sub(r"\1", text)
    text = _BOLD.sub(r"\2", text)
    text = _ITALIC.sub(r"\2", text)
    text = _BOLD_EDGE.sub("", text)
    text = _STAR_EDGE.sub("", text)
    text = text.replace("`", "")
    text = _HEADER.sub("", text)
    text = _BULLET.sub("", text)
    text = _ORDERED.sub("", text)
    text = _QUOTE.sub("", text)
    text = re.sub(r"\n[ \t]*\n+", "\n", text)
    for i, literal in enumerate(code):
        text = text.replace(f"\x00{i}\x00", literal)
    return text.strip()


class SpeechTextFilter(BaseTextFilter):
    """Clean after sentence aggregation, so split LLM tokens stay intact.

    Preserve outer whitespace for TTS services that forward text fragments.
    Fish prosody tags are deliberately left intact; non-Fish adapters strip
    those separately. Each call is independent, including after interruption.
    """

    async def filter(self, text: str) -> str:
        cleaned = strip_markdown_for_speech(text)
        if not cleaned:
            return ""
        leading = text[:len(text) - len(text.lstrip())]
        trailing = text[len(text.rstrip()):]
        return leading + cleaned + trailing
