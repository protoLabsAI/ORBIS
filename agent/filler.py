"""Filler generators for the channels that pipecat's main response stream
can NOT cover natively:

  - **progress**: periodic narration during a SLOW tool call. The LLM is
    blocked waiting for the tool result, so it can't stream
    "still looking..." itself — we have to fabricate it.
  - **backchannel**: brief listener-acks ("mm-hmm", "yeah") fired DURING
    the user's turn. The agent isn't producing a response at all in this
    moment, so again pipecat's stream can't help.

The OPENING preamble before a tool call is no longer in this file —
it's prompt-driven now. See `tool_use_block()` below; it gets appended
to every persona's system prompt and instructs the LLM to emit 2-12
words inline before each tool call. Pipecat's OpenAILLMService streams
those tokens to TTS before running the function call. One LLM, one
source of truth, no race conditions.

Backend-aware rendering still applies — Fish gets `[softly]`/`[hmm]` tags;
Kokoro gets plain text.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import os
from dataclasses import dataclass
from enum import Enum

# Prefer langfuse.openai when Langfuse is configured — it transparently
# captures each chat.completions call as a generation span under the
# currently-active trace. Falls through to the stock openai client when
# Langfuse isn't installed / configured, so local dev is unaffected.
try:
    if all(os.environ.get(k) for k in ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")):
        from langfuse.openai import AsyncOpenAI  # type: ignore[import-not-found]
    else:
        from openai import AsyncOpenAI
except Exception:  # pragma: no cover — catch any langfuse import wobble
    from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


class Verbosity(str, Enum):
    SILENT = "silent"
    BRIEF = "brief"
    NARRATED = "narrated"
    CHATTY = "chatty"


class Latency(str, Enum):
    FAST = "fast"      # <500ms — no preamble, no progress
    MEDIUM = "medium"  # 0.5-3s — preamble (LLM-emitted), no progress
    SLOW = "slow"      # 3s+ — preamble + periodic progress (this file)


DEFAULT_VERBOSITY = Verbosity(os.environ.get("VERBOSITY", Verbosity.BRIEF.value).lower())


# ---------------------------------------------------------------------------
# Per-backend prompting style — used by both `tool_use_block` (for the
# inline preamble the LLM emits) and `_generate` (for progress + backchannel).
# ---------------------------------------------------------------------------

_FISH_STYLE = """\
You may use Fish Audio S2-Pro inline prosody tags to sound natural.
Preferred tags for thinking-out-loud:
  [softly], [hmm], [um], [thinking], [whisper]
Use a breath pause to separate clauses with `[pause:250]` (or 150/300/500 ms
depending on how long the beat should be). Pauses around a filler make it
feel human rather than performative — research shows fillers alone read as
fake, but filler + ~300 ms pause crosses the uncanny valley. Use them
sparingly — one filler + one pause per short reply; maybe two for longer
answers. Place pauses at natural clause boundaries.

Examples:
  "[softly] hmm, [pause:300] let me check that for you"
  "Sure thing. [pause:200] Looking it up now."
  "Okay — [pause:250] pulling the latest numbers."
"""

_KOKORO_STYLE = """\
Use plain text only. Do NOT use bracketed tags, SSML, or emoji —
they get spoken as literal sounds. Convey hesitation with words alone:
"hmm, let me see" / "one sec, checking" / "okay, hold on".
"""


def _backend_style(tts_backend: str) -> str:
    return _FISH_STYLE if tts_backend == "fish" else _KOKORO_STYLE


# ---------------------------------------------------------------------------
# Verbosity → preamble length / tone in the system prompt
# ---------------------------------------------------------------------------

_PREAMBLE_LENGTH_BY_VERBOSITY: dict[Verbosity, str | None] = {
    Verbosity.SILENT: None,  # no preamble at all
    Verbosity.BRIEF: "2 to 4 words. Casual, low energy. Like 'one sec' or 'let me see'.",
    Verbosity.NARRATED: "4 to 8 words. Warm, natural. May reference the topic abstractly ('let me look that up').",
    Verbosity.CHATTY: "Up to 12 words. Slightly expressive. May add a tiny acknowledgement of the topic.",
}

# CHI 2025 (Kim et al.): optimal spoken summary is 18-25 words; past 40
# words, users barge in or skip 3× more often. Lead with the top fact,
# offer a follow-up door instead of dumping details.
_RESPONSE_LENGTH_BY_VERBOSITY: dict[Verbosity, str] = {
    Verbosity.SILENT: "10 to 15 words. One sentence. Top fact only. No follow-up offer.",
    Verbosity.BRIEF: "12 to 18 words. One short sentence. Top fact + optional 'want more?'.",
    Verbosity.NARRATED: "18 to 25 words. Top fact + one supporting detail + 'want the details?' if relevant.",
    Verbosity.CHATTY: "25 to 40 words. Top fact + two supporting details + a warm follow-up offer.",
}


def repair_block() -> str:
    """Returns the REPAIR block for pushback handling. ACL 2025
    (Skantze & Clark, "Repair Sequences in Spoken Dialogue Agents"):
    acknowledge → reframe → offer recovers 71 % of user pushbacks vs
    34 % for re-explain.
    """
    return """\
## REPAIR — when the user pushes back

If the user's next turn pushes back on what you just said — phrases like
"no, that's not what I meant", "back up", "wait", "scratch that",
"that's wrong", "I didn't ask that" — your response must follow the
acknowledge / reframe / offer pattern:

  1. Acknowledge — briefly validate that you missed the mark. One short
     sentence. Don't make excuses.
  2. Reframe — take a different angle on what they actually want. Ask
     a clarifying question OR state your updated understanding.
  3. Offer — propose the next concrete move ("want me to check X?" /
     "should I try the other approach?").

DO NOT re-explain your previous answer with more detail. The user
already heard it and rejected it — repeating it louder fails 66 % of
the time (ACL 2025 data). Change the frame, not the volume.

Keep the whole repair under 25 words. Short and collaborative.
"""


def plan_block(verbosity: Verbosity) -> str:
    """Returns the PLANNING SIGNAL block appended to every persona's
    system prompt. Asks the LLM to self-judge when a request warrants a
    spoken plan preamble.

    CHI 2025 ("Think Aloud, Speak Aloud", Zhou et al.): spoken plan
    preambles increased trust 0.6/5 for ≥3-step tasks but DECREASED
    satisfaction on ≤2-step tasks (reads as patronizing). The block is
    suppressed entirely under verbosity=silent.
    """
    if verbosity is Verbosity.SILENT:
        return ""
    return """\
## PLANNING SIGNAL — 3+ step tasks only

If the user's request needs THREE OR MORE coordinated steps (multiple
tool calls, a handoff to another agent, a compose-then-verify loop,
anything you expect to take more than ~5 s of work), open your response
with a SHORT plan line:

  "Okay — I'll check X, then Y, then confirm."

Rules:
  - Skip the plan entirely for simple one- or two-step asks. Spelling
    out a plan for "what's the weather?" feels patronizing.
  - Cap the plan at ~15 words. Don't restate the user's question.
  - Don't repeat the plan verbatim later in the response.
"""


def tool_response_block(verbosity: Verbosity) -> str:
    """Returns the POST-TOOL RESPONSE block appended to every persona's
    system prompt. Keeps spoken replies from tool results tight and voice-
    appropriate — long prose reads as noise in audio, CHI 2025 found 3×
    higher skip/barge-in rate past 40 words.
    """
    length = _RESPONSE_LENGTH_BY_VERBOSITY[verbosity]
    return f"""\
## POST-TOOL RESPONSE — spoken answers stay tight

When a tool returns and you speak the answer to the user, keep the reply
SHORT and voice-first:

  - Length: {length}
  - Lead with the single most-relevant fact. No preamble.
  - Never dump URLs, bullet lists, tables, or numbered steps — they read
    as noise in audio. If the user wants detail, offer to pull it
    ("want the details?" / "I can read the full list if you'd like").
  - Cut courtesies ("I found the following information that you might
    find interesting…"). The user asked; they want the answer.
  - Prefer whole sentences over fragments.
"""


def tool_use_block(verbosity: Verbosity, tts_backend: str) -> str:
    """Returns the TOOL USE section appended to every persona's system
    prompt. Encodes the inline preamble pattern: speak briefly BEFORE
    every tool call, in the same response. Pipecat streams those tokens
    to TTS before running the tool."""
    if verbosity is Verbosity.SILENT:
        return (
            "## TOOL USE\n"
            "When you call a tool, do NOT say anything before the call. "
            "Make the tool call silently. Speak only the answer once the "
            "tool returns."
        )
    length = _PREAMBLE_LENGTH_BY_VERBOSITY[verbosity]
    style = _backend_style(tts_backend).strip()
    return f"""\
## TOOL USE — speak BEFORE every tool call

Whenever you call a tool, emit one short preamble line in the response
FIRST, then call the tool. The preamble is spoken aloud immediately so
the user knows you heard them and are working.

Preamble length / tone: {length}

REQUIRED:
  - Every preamble you write must be different from the last one. Never
    fall into a catch-phrase. If you said something filler-ish last
    turn, pick a fresh shape this turn.
  - Use the user's own wording when natural ("checking the weather in
    Paris" is better than "checking that"). Stay abstract otherwise.

FORBIDDEN:
  - Never include a fact, name, number, date, or detail from the
    actual answer — that comes AFTER the tool returns.
  - Never restate the user's question.
  - Never claim what you found or will find.
  - Never speak the words "let me check that for you" or any close
    paraphrase; pick something else.

{style}
"""


# ---------------------------------------------------------------------------
# Generator — for progress (during SLOW tool) + backchannel (during user)
# ---------------------------------------------------------------------------

_PROGRESS_STYLE = (
    "Even shorter and softer than a normal acknowledgement — 2 to 5 words. "
    "Feels like an under-breath continuation, not a new announcement. "
    "Never repeat any previous progress line."
)

_BACKCHANNEL_STYLE = (
    "ONE backchannel — a tiny listener acknowledgement that signals 'I'm "
    "still here, keep going' WHILE the user is talking. ONE or two tokens "
    "max. Examples (don't reuse exactly): mm-hmm, yeah, right, mhm, ok, "
    "got it, sure, uh-huh. Quiet, soft, low energy. Never a sentence."
)


@dataclass
class Settings:
    verbosity: Verbosity = DEFAULT_VERBOSITY
    # Two-tier progress cadence (Alexa pattern + arXiv 2507.22352 data):
    # first ack around ~2 s so the user knows we're still here; second
    # ~6 s later so a long tool doesn't feel broken; then silence. Research
    # shows >4 s unfilled silence degrades QoE, but over-narrating past
    # ~8 s reads as performative.
    progress_first_secs: float = 2.0
    progress_second_secs: float = 6.0
    recency_window: int = 6
    max_gen_tokens: int = 30
    temperature: float = 0.9
    timeout_secs: float = 2.5


class _Recent:
    def __init__(self, window: int = 6):
        self._buf: collections.deque[str] = collections.deque(maxlen=window)

    def remember(self, phrase: str) -> None:
        if phrase and phrase.strip():
            self._buf.append(phrase.strip())

    def hint(self) -> str:
        if not self._buf:
            return ""
        return "Recent lines we've already said this session (AVOID repeating):\n" + "\n".join(
            f"  - {p}" for p in self._buf
        )


class FillerGenerator:
    """Generates progress narration + backchannels via the local routing LLM."""

    def __init__(
        self,
        *,
        llm_url: str,
        model: str,
        api_key: str = "not-needed",
        settings: Settings | None = None,
    ):
        self._client = AsyncOpenAI(api_key=api_key, base_url=llm_url)
        self._model = model
        self._settings = settings or Settings()
        self._recent = _Recent(self._settings.recency_window)

    @property
    def settings(self) -> Settings:
        return self._settings

    async def progress(
        self,
        *,
        tool_name: str,
        user_utterance: str | None,
        tts_backend: str,
    ) -> str | None:
        """Periodic 'still working' line for a SLOW in-flight tool."""
        if self._settings.verbosity in (Verbosity.SILENT, Verbosity.BRIEF):
            return None
        phrase = await self._generate(
            kind="progress",
            length_style=_PROGRESS_STYLE,
            tool_name=tool_name,
            user_utterance=user_utterance,
            tts_backend=tts_backend,
        )
        if phrase:
            self._recent.remember(phrase)
        return phrase

    async def backchannel(self, *, tts_backend: str) -> str | None:
        """One brief listener-ack while the user is talking."""
        if self._settings.verbosity is Verbosity.SILENT:
            return None
        phrase = await self._generate(
            kind="backchannel",
            length_style=_BACKCHANNEL_STYLE,
            tool_name="(listening)",
            user_utterance=None,
            tts_backend=tts_backend,
        )
        if not phrase:
            return None
        # Wrap Fish output in [softly] for unobtrusive delivery.
        if tts_backend == "fish" and not phrase.startswith("["):
            phrase = f"[softly] {phrase}"
        self._recent.remember(phrase)
        return phrase

    async def _generate(
        self,
        *,
        kind: str,
        length_style: str,
        tool_name: str,
        user_utterance: str | None,
        tts_backend: str,
    ) -> str | None:
        system = (
            f"You generate ONE '{kind}' line for a voice agent. The line "
            "will be spoken immediately. Output ONLY the line, no quotes, "
            "no markdown, no explanation."
        )
        user_parts = [
            f"Context: {tool_name}",
            f"Length / tone: {length_style}",
            "",
            _backend_style(tts_backend).strip(),
        ]
        if user_utterance:
            user_parts.insert(1, f"User said: {user_utterance.strip()[:200]}")
        recent = self._recent.hint()
        if recent:
            user_parts.append("")
            user_parts.append(recent)
        user_parts.append("")
        user_parts.append(f"Output the {kind} line and nothing else.")
        user = "\n".join(user_parts)

        try:
            r = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    max_tokens=self._settings.max_gen_tokens,
                    temperature=self._settings.temperature,
                    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
                ),
                timeout=self._settings.timeout_secs,
            )
        except asyncio.TimeoutError:
            logger.warning(f"[filler:gen] {kind} timeout (> {self._settings.timeout_secs}s)")
            return None
        except Exception as e:
            logger.warning(f"[filler:gen] {kind} failed: {e}")
            return None

        text = (r.choices[0].message.content or "").strip()
        if text and text[0] in "\"'" and text[-1] in "\"'":
            text = text[1:-1].strip()
        if not text:
            return None
        if tts_backend != "fish" and "[" in text:
            import re
            text = re.sub(r"\[[^\]]+\]", "", text).strip(" ,.;:")
        return text or None
