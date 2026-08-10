"""Personality + mood rendering and drift analysis.

Bridges the SQLite ``memory.personality`` DAL with the voice agent:

  - ``render_personality_block(mem)`` → text snippet to prepend to the
    system prompt so the model's tone reflects the orb's current axes
    and mood.
  - ``analyze_session_drift(messages, llm)`` → optional post-session
    small-LLM call that reads the transcript and returns a list of
    axis deltas + reasons. Falls back to a no-op when no LLM is wired
    or the call fails.

Directable drift ("be more playful") is picked up by the same
analyzer — it reads the transcript and picks up explicit instructions
alongside emergent interaction patterns.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


# The axis names that get written into the system prompt / exposed to
# the analyzer. Kept in sync with memory.personality.DEFAULT_AXES.
# Each entry is (axis_slug, negative_adjective, positive_adjective).
_AXIS_DESCRIPTIONS: list[tuple[str, str, str]] = [
    ("playful_serious",         "serious",     "playful"),
    ("warm_guarded",            "guarded",     "warm"),
    ("sarcastic_sincere",       "sincere",     "sarcastic"),
    ("verbose_terse",           "terse",       "verbose"),
    ("hopeful_cynical",         "cynical",     "hopeful"),
    ("grandiose_grounded",      "grounded",    "grandiose"),
    ("probing_incurious",       "incurious",   "probing"),
    ("philosophical_pragmatic", "pragmatic",   "philosophical"),
    ("independent_clingy",      "clingy",      "independent"),
    ("curious_bored",           "bored",       "curious"),
]


def _axis_label(value: float, negative: str, positive: str) -> str:
    """Map a float in [-1, +1] to a human descriptor."""
    magnitude = abs(value)
    if magnitude < 0.15:
        return "neutral"
    adjective = positive if value > 0 else negative
    if magnitude < 0.4:
        return f"slightly {adjective}"
    if magnitude < 0.75:
        return adjective
    return f"strongly {adjective}"


def render_personality_block(mem) -> str:
    """Return the personality-state prompt block.

    Injected into the system prompt at session start so the model's
    tone reflects the orb's current axes + mood. Empty when no state
    exists (first boot, degraded mode).
    """
    try:
        axes = {a.axis: a for a in mem.personality.all_axes()}
        mood = mem.personality.get_mood()
    except Exception as e:
        logger.warning(f"[personality] render failed: {e}")
        return ""

    axis_lines: list[str] = []
    for slug, neg, pos in _AXIS_DESCRIPTIONS:
        a = axes.get(slug)
        if a is None or abs(a.value) < 0.15:
            continue
        axis_lines.append(f"- {_axis_label(a.value, neg, pos)}")

    mood_desc: list[str] = []
    if abs(mood.valence) >= 0.15:
        mood_desc.append(
            "bright" if mood.valence > 0 else "low"
        )
    if abs(mood.arousal) >= 0.15:
        mood_desc.append(
            "energetic" if mood.arousal > 0 else "sleepy"
        )
    if mood.guardedness > 0.15:
        mood_desc.append("guarded")

    # Nothing above the noise floor — emit nothing, not even the header.
    if not axis_lines and not mood_desc:
        return ""

    lines: list[str] = ["## PERSONALITY"]
    if axis_lines:
        lines.append("Your current temperament leans:")
        lines.extend(axis_lines)
    if mood_desc:
        lines.append("")
        lines.append(f"Mood right now: {', '.join(mood_desc)}.")
    lines.append("")
    lines.append(
        "These color your tone — the substance of your answers stays "
        "the same. Don't caricature them; let them drift through."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Post-session drift analyzer.
#
# Runs a small LLM call over the session transcript and returns a list
# of proposed axis deltas. Called from the session-end persist path so
# the drift is ready for the next session.
# ---------------------------------------------------------------------------


_ANALYZER_PROMPT = """\
You analyze a voice conversation and return how the assistant's
personality should drift based on what happened.

The personality has these axes (each in [-1, +1]):

{axis_descriptions}

Rules:
- Return JSON: {{"deltas": [{{"axis": "<slug>", "delta": <float>, "reason": "<short>"}}, ...]}}
- |delta| MUST be in [0.01, 0.15]. Drift is slow.
- Return AT MOST 3 deltas. Usually return 0-1.
- Pick axes that have a clear signal in the conversation.
- Also honor explicit user instructions ("be more playful") — those
  can use the upper end of the delta range.
- If nothing noteworthy happened, return {{"deltas": []}}.
- Return ONLY the JSON object. No prose.
"""


def _axis_descriptions_for_prompt() -> str:
    return "\n".join(
        f"  {slug}: -1 {neg}, +1 {pos}"
        for slug, neg, pos in _AXIS_DESCRIPTIONS
    )


async def analyze_session_drift(
    messages: list[dict],
    *,
    llm_url: str,
    model: str,
    api_key: str,
    provider: str | None = None,
) -> list[dict]:
    """Ask a small LLM to propose personality drift deltas based on the
    session transcript. Returns a list of ``{axis, delta, reason}``
    dicts, or [] on any error.

    Never raises — analyzer failure should not affect the main
    session-end path.
    """
    # Bounded: drop everything except user/assistant turns, trim to
    # last ~20 exchanges, cap each turn to 800 chars.
    filtered: list[dict] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str):
            continue
        filtered.append({"role": role, "content": content[:800]})
    filtered = filtered[-40:]

    if not filtered:
        return []

    prompt = _ANALYZER_PROMPT.format(
        axis_descriptions=_axis_descriptions_for_prompt()
    )

    try:
        from openai import AsyncOpenAI
    except ImportError:
        logger.info("[personality] openai not installed; skipping drift analysis")
        return []

    try:
        if provider and provider.strip().lower() in ("anthropic-oauth", "openai-codex"):
            # Subscription backends don't serve chat completions — use the
            # chat-shaped facade over the native protocol.
            from voice.llm.oauth_text import OAuthTextClient
            client = OAuthTextClient(provider)
        else:
            client = AsyncOpenAI(api_key=api_key, base_url=llm_url)
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": "Transcript:\n" + json.dumps(filtered, ensure_ascii=False),
                },
            ],
            temperature=0.2,
            max_tokens=400,
        )
    except Exception as e:
        logger.info(f"[personality] drift analysis call failed: {e}")
        from agent import metrics
        metrics.inc("drift_llm_call_failed")
        return []

    try:
        raw = resp.choices[0].message.content or ""
    except Exception:
        from agent import metrics
        metrics.inc("drift_llm_empty_response")
        return []

    # The model may wrap JSON in a fence; strip greedily.
    raw = raw.strip()
    if raw.startswith("```"):
        # ```json\n...\n``` or ```\n...\n```
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip("` \n")

    try:
        data: Any = json.loads(raw)
    except Exception as e:
        logger.info(f"[personality] drift JSON parse failed: {e}")
        from agent import metrics
        metrics.inc("drift_json_parse_failed")
        return []

    if not isinstance(data, dict):
        return []
    deltas_raw = data.get("deltas")
    if not isinstance(deltas_raw, list):
        return []

    known_axes = {slug for slug, _, _ in _AXIS_DESCRIPTIONS}
    out: list[dict] = []
    for entry in deltas_raw[:3]:
        if not isinstance(entry, dict):
            continue
        axis = entry.get("axis")
        delta = entry.get("delta")
        reason = entry.get("reason") or ""
        if axis not in known_axes:
            continue
        try:
            delta = float(delta)
        except (TypeError, ValueError):
            continue
        # Clamp hard; the DAL clamps again at |0.3| but we want
        # analyzer proposals to be very modest.
        if abs(delta) < 0.01:
            continue
        delta = max(-0.15, min(0.15, delta))
        out.append({"axis": axis, "delta": delta, "reason": str(reason)[:200]})

    return out


def apply_drift(mem, deltas: list[dict]) -> None:
    """Apply analyzer-proposed drift deltas through memory.personality.drift."""
    for d in deltas:
        try:
            mem.personality.drift(d["axis"], d["delta"], d.get("reason", ""))
        except Exception as e:
            logger.warning(f"[personality] drift apply failed for {d}: {e}")
