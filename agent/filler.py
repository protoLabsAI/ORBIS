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
import re
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

# Sentinel: distinguishes "extra_body not passed" (use gateway default) from
# "extra_body explicitly None" (local micro endpoint — send nothing).
_UNSET = object()

# Emoji / pictographs / dingbats / symbols / arrows / variation selectors —
# a small micro model (or any model) sometimes sprinkles these in, and Kokoro
# mangles them. Strip before TTS. (Fish handles its own [bracketed] prosody
# tags, so those are only stripped for non-fish backends.)
_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"  # emoji & supplemental symbols/pictographs
    "\U00002600-\U000027BF"  # misc symbols + dingbats
    "\U0001F1E6-\U0001F1FF"  # regional indicators
    "\U00002190-\U000021FF"  # arrows
    "\U00002B00-\U00002BFF"  # misc symbols & arrows
    "️‍"           # variation selector, ZWJ
    "]+"
)


def _clean_for_tts(text: str, tts_backend: str) -> str:
    """Make a generated micro-line safe to speak: strip emoji/symbols, and
    (for non-fish) markdown ``[brackets]``; collapse whitespace."""
    text = _EMOJI_RE.sub("", text)
    if tts_backend != "fish" and "[" in text:
        text = re.sub(r"\[[^\]]+\]", "", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" ,.;:—-")
    return text


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
# Verbosity → spoken response length in the system prompt
# ---------------------------------------------------------------------------
# (The per-tool spoken *preamble* was removed in the router-first decouple —
# see tool_use_block. Verbosity now shapes the post-tool / plain response
# length only; acknowledgement is the ack-layer's job, not the prompt's.)

# CHI 2025 (Kim et al.): optimal spoken summary is 18-25 words; past 40
# words, users barge in or skip 3× more often. Lead with the top fact,
# offer a follow-up door instead of dumping details.
_RESPONSE_LENGTH_BY_VERBOSITY: dict[Verbosity, str] = {
    Verbosity.SILENT: "10 to 15 words. One sentence. Top fact only. No follow-up offer.",
    Verbosity.BRIEF: "12 to 18 words. One short sentence. Top fact + optional 'want more?'.",
    Verbosity.NARRATED: "18 to 25 words. Top fact + one supporting detail + 'want the details?' if relevant.",
    Verbosity.CHATTY: "25 to 40 words. Top fact + two supporting details + a warm follow-up offer.",
}


def audio_context_block() -> str:
    """Returns the AUDIO CONTEXT block — explains the ``[audio]``
    annotation that AudioTagsTap (#66) injects before each
    ``TranscriptionFrame``. The line carries non-textual signal the LLM
    can adapt to without being told to "match the user's energy" — that
    instruction without grounding produces parroting.

    The annotation looks like::

        [audio] emotion=happy lang=en events=[] speaker=owner snr=high env=indoor rate=normal

    Returned as a static block (no verbosity branch) — the rules apply
    equally regardless of how chatty the persona is.
    """
    return """\
## AUDIO CONTEXT — what the [audio] line is

Every user turn arrives with a one-line audio annotation like:
  [audio] emotion=happy lang=en speaker=owner snr=high env=indoor rate=normal

This is signal you can't get from the transcript alone — the user's
emotional tone, the room they're in, whether it's the registered owner
versus someone else. Use it to shape your response naturally:

  - emotion=sad/fearful → soften your tone, slow down, leave space
  - emotion=happy/surprised → match the energy without performing
  - speaker=stranger → don't reference owner-only memories or facts
  - snr=low or env=noisy → keep your reply short; they may not catch
    a long answer
  - rate=fast → match the pace; they're moving quickly

DO NOT parrot the annotation back ("I can tell you sound happy!").
That makes the user feel observed, not understood. Let the signal
inform tone and length; never name it explicitly.

If the annotation is missing or fields are empty, ignore them —
the transcript stands alone.
"""


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


def grounding_block() -> str:
    """Returns the GROUNDING block — honesty guardrail against fabrication.

    ORBIS is a fast voice router, not a knowledge base, and in voice a confident
    wrong answer sounds identical to a right one. Two real failure modes this
    guards: (1) asserting unverifiable specifics as fact ("made that up"), and
    (2) force-fitting a request onto an unrelated agent when none fits (a design
    job routed to the research agent) instead of admitting the gap. Framed with
    GOOD examples to pattern-match — the routing model is the small/fast tier,
    which copies examples better than it absorbs prohibitions — plus an explicit
    "answering what you know is fine" clause so it doesn't over-hedge.
    """
    return """\
## GROUNDING — don't make things up

You're a fast voice companion and router, not a knowledge base. A confident
wrong answer sounds exactly like a right one, so when you don't actually know
something, find out or say so — never guess and state it as fact.

For specifics you can't verify from this conversation (current events, numbers,
someone's status, personal history): hand it to an agent that fits, or admit it
and offer to check. Good — "I don't have that off-hand, want me to have <agent>
pull it?" / "Not sure — I don't want to guess."

If NO agent on your fleet fits the request, say plainly you can't do that. Don't
route it to an unrelated agent just to produce an answer: handing a design job
to a research agent is worse than saying you don't have a designer.

Answering what you genuinely know is always fine — how you work, chit-chat,
reasoning the user can follow. This is only about asserting specifics you'd be
guessing at.
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
    prompt.

    **Router-first (D1, 2026-05-30):** the tool *decision* turn is
    preamble-free. We used to instruct "emit a short preamble line FIRST,
    then call the tool" — but asking one streamed completion from a fast
    model to (1) narrate, then (2) emit a ``tool_calls`` block reliably
    ends the turn after step 1 (``finish_reason=stop``, no tool call). The
    spoken sentence reads, to the model, as a complete answer, so the
    action silently never happens. Decoupling the acknowledgement from the
    decision is the fix: the tool call goes out clean, and the "I heard
    you" lives in the separate ack / progress-narration layer (which fires
    on user-stop / during a slow tool, not gated on tool emission). See
    ``docs/internal/duplex-orchestration-direction.md``.

    The ``tts_backend`` arg is retained for signature stability (callers
    pass it); the block is now backend-agnostic since it no longer carries
    a spoken preamble to style."""
    del tts_backend  # no spoken preamble here anymore → nothing to style
    return """\
## TOOL USE — call the tool, don't narrate first

When the user asks for something a tool does, CALL THE TOOL. Do not speak
a preamble, a filler line, or "let me check that" before the call — just
make the call. Saying you'll do it without emitting the tool call means
the action never happens.

  - Call the tool directly, with no spoken text before it.
  - A brief acknowledgement is handled for you separately — you do not
    need to voice one.
  - Speak only AFTER the tool returns, describing the actual result.
  - Need several tools? Call the first; you'll be invoked again with its
    result to decide the next. Don't narrate the chain up front.
"""


# ---------------------------------------------------------------------------
# Generator — for progress (during SLOW tool) + backchannel (during user)
# ---------------------------------------------------------------------------

_PROGRESS_STYLE = (
    "A quick, warm check-in while you WAIT for someone else to get back to "
    "you — 3 to 7 words. You're waiting on them, not doing it yourself; name "
    "who or what you're waiting on when you can. Examples of the feel:\n"
    "  'still waiting to hear back from Ava'\n"
    "  'Ava's still looking into that'\n"
    "  'hang on, she's still digging'\n"
    "  'still checking on those incidents'\n"
    "Keep each one about the waiting, warm and a little different. It's fine "
    "that it's taking a moment — just acknowledge you're still on it; you "
    "don't have the answer yet."
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
    # Progress cadence, AFTER the opening ack (which fires at ~1 s with a
    # contextual line). The opening covers "I heard you", so the progress loop
    # must NOT pile on behind it — the first line waits ~6 s (a comfortable gap).
    # Then it repeats every ~interval until the tool finishes, so a long delegate
    # never dead-airs past one interval (the old fixed two-line loop went silent
    # after ~12 s — see agent/presence.py and evals/presence.py). The interval is
    # sparse on purpose: over-narrating back-to-back reads as spam (arXiv
    # 2507.22352), but a multi-second silent gap is the "where'd you go" dead air.
    # Each line grounds in the delegate's latest streamed status when present.
    progress_first_secs: float = 6.0
    progress_interval_secs: float = 10.0
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
        extra_body: dict | None | object = _UNSET,
        settings: Settings | None = None,
    ):
        self._client = AsyncOpenAI(api_key=api_key, base_url=llm_url)
        self._model = model
        # extra_body for the micro endpoint. The gateway wants
        # chat_template_kwargs={enable_thinking:False}; a local Ollama/MLX
        # endpoint rejects it, so the caller passes None to send nothing.
        # Unset (the sentinel) keeps the gateway default for back-compat.
        self._extra_body = (
            {"chat_template_kwargs": {"enable_thinking": False}}
            if extra_body is _UNSET else extra_body
        )
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
        status_hint: str | None = None,
    ) -> str | None:
        """Periodic 'still working' line for a SLOW in-flight tool.

        Fires at every verbosity except SILENT. A multi-second tool wait
        (a slow delegate can take 15-60s) left unfilled is *dead air* — far
        worse than a brief "still working" line — so this is not gated to the
        chatty tiers the way conversational length is; only a fully SILENT
        persona suppresses it. The line itself is already tiny (_PROGRESS_STYLE
        = 2-5 words).

        ``status_hint`` (optional) is the delegate's latest *real* streamed
        status — when present the check-in is grounded in what's actually
        happening ("sounds like Ava's deep in the incident log") instead of a
        generic line."""
        if self._settings.verbosity is Verbosity.SILENT:
            return None
        phrase = await self._generate(
            kind="progress",
            length_style=_PROGRESS_STYLE,
            tool_name=tool_name,
            user_utterance=user_utterance,
            tts_backend=tts_backend,
            status_hint=status_hint,
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

    async def micro_ack(self, *, tts_backend: str) -> str | None:
        """ONE tiny natural 'taking a beat' ack — the agent heard the user
        and is about to answer ('one sec', 'let me see', 'hmm'). For the
        occasional LLM-generated micro-ack (orbis-29e) instead of a canned
        one. Returns None on failure or if the model over-produces, so the
        caller falls back to the fixed pool."""
        if self._settings.verbosity is Verbosity.SILENT:
            return None
        system = (
            "You generate ONE tiny spoken acknowledgement for a voice agent "
            "that just heard the user and is taking a beat before it answers. "
            "1 to 3 words, natural and low-key — like 'one sec', 'let me see', "
            "'hmm', 'right', 'okay so', 'hang on'. Not a question, not a "
            "sentence, no promise about what you'll do. Output only the words."
        )
        user = "\n".join([
            _backend_style(tts_backend).strip(),
            self._recent.hint() or "",
            "Output the tiny ack and nothing else.",
        ])
        phrase = await self._complete(
            kind="micro_ack", system=system, user=user, tts_backend=tts_backend,
        )
        if not phrase or len(phrase.split()) > 4:
            return None  # guard against the model rambling
        if tts_backend == "fish" and not phrase.startswith("["):
            phrase = f"[softly] {phrase}"
        self._recent.remember(phrase)
        return phrase

    async def opening(
        self, *, user_utterance: str | None, tts_backend: str,
    ) -> str | None:
        """ONE short opening acknowledgement for a tool that's ABOUT to run —
        the occasional LLM-generated 'surprise' alternative to the canned
        opening pool (router-first D1). Said the instant a tool call starts,
        so it must NOT promise a result or state any fact/number/name — the
        work hasn't happened yet. May nod to the user's topic abstractly
        ('ooh, let me dig into that'). Returns None on failure / over-produce
        so the caller falls back to the canned pool."""
        if self._settings.verbosity is Verbosity.SILENT:
            return None
        system = (
            "You're a warm, quick-witted voice companion. The user just asked "
            "for something and you're about to go do it — look it up, hand it "
            "to another agent, run a tool. Say ONE short spoken opener (3 to 8 "
            "words) that reacts to THEIR specific request in your own fresh "
            "words, the way a sharp friend would. Notice how each example names "
            "what they actually asked and sounds different:\n"
            "  'okay, pulling up the fleet status now'\n"
            "  'sure, let me ask Ava about those incidents'\n"
            "  'good question — digging into that repo'\n"
            "  'alright, tracking that down for you'\n"
            "  'ooh, give me a sec on that one'\n"
            "Make yours specific to their ask and fresh. You haven't done it "
            "yet, so leave out any result, number, or name you'd only know "
            "after. Output only the words you'd say."
        )
        user = "\n".join([
            f"They asked: {user_utterance.strip()[:200]}" if user_utterance
            else "(you didn't catch the exact words — keep it generic but fresh)",
            _backend_style(tts_backend).strip(),
            self._recent.hint() or "",
            "Give one fresh opener, different from anything above. Words only.",
        ])
        phrase = await self._complete(
            kind="opening", system=system, user=user, tts_backend=tts_backend,
        )
        # Allow up to ~9 words (the prompt asks for 3-8); reject only true
        # rambling so a normal 7-8 word opener isn't dropped to the canned pool.
        if not phrase or len(phrase.split()) > 9:
            return None
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
        status_hint: str | None = None,
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
        if status_hint:
            # The delegate's real latest status — ground the check-in in it
            # ("sounds like they're still digging through the logs") rather
            # than narrating it verbatim. Paraphrase, stay short.
            user_parts.insert(1, f"What's actually happening now: {status_hint.strip()[:200]}")
        if user_utterance:
            user_parts.insert(1, f"User said: {user_utterance.strip()[:200]}")
        recent = self._recent.hint()
        if recent:
            user_parts.append("")
            user_parts.append(recent)
        user_parts.append("")
        user_parts.append(f"Output the {kind} line and nothing else.")
        user = "\n".join(user_parts)
        return await self._complete(kind=kind, system=system, user=user, tts_backend=tts_backend)

    async def _complete(
        self, *, kind: str, system: str, user: str, tts_backend: str,
    ) -> str | None:
        """One micro-LLM generation: bounded by the filler timeout, cleaned
        up for speech, None on any failure. Shared by the filler kinds and
        the proactive announcer."""
        try:
            create_kwargs: dict = {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": self._settings.max_gen_tokens,
                "temperature": self._settings.temperature,
            }
            if self._extra_body:
                create_kwargs["extra_body"] = self._extra_body
            r = await asyncio.wait_for(
                self._client.chat.completions.create(**create_kwargs),
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
        text = _clean_for_tts(text, tts_backend)
        return text or None

    async def announce(
        self,
        content: str,
        *,
        kind: str = "update",
        source: str | None = None,
        tts_backend: str = "kokoro",
    ) -> str | None:
        """Phrase a proactive delivery (reminder, ping, delegate result) as
        ONE natural in-character line that BRINGS IT UP — 'oh hey, quick
        reminder —' then the thing — instead of speaking the raw text. The
        content is preserved; the orb just surfaces it in its own voice.

        Returns None on timeout/failure so the caller falls back to the raw
        text — a flaky micro-LLM must never block or delay a delivery. Higher
        max_tokens than a filler since this carries real content."""
        content = (content or "").strip()
        if not content:
            return None
        what = {
            "reminder": "a reminder the user earlier asked you to give them",
            "delegate": f"a reply that just came back{f' from {source}' if source else ''}",
            "ping": "a heads-up that just came in",
        }.get(kind, "an update that just came in")
        system = (
            "You are a warm voice companion mid-conversation. Something just "
            "arrived for the user and you're gently bringing it up. Write ONE "
            "short, natural spoken line that surfaces it in your own voice — a "
            "brief lead-in ('oh hey —', 'quick reminder —', 'heads up —') then "
            "the thing. KEEP the actual content; do NOT invent facts, numbers, "
            "names, or details that aren't given. No quotes, no markdown. "
            "Output only the line."
        )
        user = "\n".join([
            f"What arrived: {what}.",
            f"Content: {content[:400]}",
            "",
            _backend_style(tts_backend).strip(),
            "",
            "Output the one spoken line and nothing else.",
        ])
        # Allow a bit more room than a tiny filler — this carries content.
        line = await self._complete(
            kind="announce", system=system, user=user, tts_backend=tts_backend,
        )
        if line:
            self._recent.remember(line)
        return line
