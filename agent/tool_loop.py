"""Tool-loop policy — break a no-progress repeated tool call.

A model that re-issues the *same* tool call with the *same* arguments and gets
the *same* result, over and over, is stuck. It is not a dropped-result bug: the
tool messages are right there in the context and the model received them; it
simply isn't changing strategy. Small/fast models do this far more readily than
big ones, and ORBIS's voice path runs a small model by design.

Every other tool loop in ORBIS is already bounded — the text/A2A ReAct loop caps
at ``TEXT_AGENT_MAX_ITER`` (``app.py``) and ``orchestrate`` caps at
``ORCHESTRATE_MAX_ITER`` *and* refuses an identical ``delegate_to`` signature
(``agent/orchestrate.py``). The voice path had no bound at all: pipecat closes
the loop itself (``_handle_function_call_result`` → push context upstream →
inference again), so a degenerate model spins until the user barges in. Each
spin is a real gateway call and a real second of dead air.

This module is the single, *testable* spec of the policy. It is pure — plain
dicts in, plain dicts out, no pipecat imports — and the LLM services call it
while building a request (``voice/llm/guarded.py``, ``voice/llm/ollama.py``,
``voice/llm/mlx.py``), so the four backends can't drift.

Two escalating steps, keyed on the trailing run of identical
``(tool, args, result)`` round-trips:

  - at ``NUDGE_AT`` — append a note telling the model to change approach. Tools
    stay available; this is the recovery path.
  - at ``STOP_AT`` — append the note *and take tool calling off the table* for
    that one request, so the model has no choice but to answer in words.

The brake uses each backend's native mechanism rather than one compromise,
because neither mechanism works everywhere:

  - OpenAI-compat (``supports_tool_choice=True``, the default) — send
    ``tool_choice: "none"``. This is precisely the API's own answer to "don't
    call a tool this turn", and it leaves the request otherwise untouched.
  - Ollama / MLX — withhold the ``tools`` schema. Those adapters forward
    ``tools`` to a native endpoint / chat template and have no ``tool_choice``
    concept at all, so setting it would be silently ignored and the guard would
    quietly do nothing.

The reverse substitution is not safe either: dropping ``tools`` while the
history still carries ``tool_calls`` is a contract change, and the protoLabs
gateway is a known-strict actor that answers unexpected params with a 400 and a
silent fall back to another model group (see the ``supports_developer_role``
note in ``voice/llm/__init__.py``). On the gateway path we change nothing but
the one field designed to be changed.

The stop step forces SPEECH, never silence. This is the same call
``orchestrate`` makes when it runs out of step budget (``_force_synthesis``) —
in a voice agent, going quiet is the one unacceptable failure, so a stuck model
is made to say it's stuck rather than dropped on the floor.

Both edits are **ephemeral**: they apply to the outbound request only, never to
the shared ``LLMContext``. A stuck turn therefore leaves no trace in history
once it recovers — nothing for a later summarization pass to fold into the
persona, and no marker the scan has to skip. The consequence is that the nudge
must re-apply on every subsequent inference (it isn't in history to be
remembered), so the thresholds below are ``>=`` and not equality.

It is a **no-op on any healthy history**: the run only extends while the exact
same calls keep getting the exact same answers, contiguously, at the tail. A
real user message — mid-turn steering, a barge-in — breaks the run, as it
should.
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

# Consecutive identical round-trips before we act. Tighter than a text agent's
# budget on purpose: a voice turn pays ~1s of LLM latency plus the tool's own
# time per spin, and the user hears every one of them as silence. Two identical
# calls with two identical results is already a bad turn; three is a broken one.
# `orchestrate` is stricter still (it refuses the *first* exact repeat), but it
# can refuse before executing — here the tool has already run, so the earliest
# we can see a repeat is the second one.
NUDGE_AT = int(os.environ.get("VOICE_TOOL_LOOP_NUDGE_AT", "2"))
STOP_AT = int(os.environ.get("VOICE_TOOL_LOOP_STOP_AT", "3"))

# How much of the repeated tool result to quote back to the model. Short: it is
# there to ground the model's explanation to the user, and a long paste invites
# a small model to read it aloud verbatim.
SNIPPET_CHARS = 120


def _field(m, key):
    """Read a field off a message that may be a plain dict or a pydantic
    message param — both shapes reach the services (see ``_has_tool_result``
    in ``voice/llm/two_model.py``, which handles the same split)."""
    return m.get(key) if isinstance(m, dict) else getattr(m, key, None)


def _content(m) -> str:
    c = _field(m, "content")
    return c if isinstance(c, str) else ("" if c is None else str(c))


def _canonical_args(raw) -> str:
    """A stable fingerprint of one call's arguments.

    On the wire ``arguments`` is a JSON *string*, so the same call can arrive
    with different key order or spacing on different turns. Parse and re-dump
    sorted so those compare equal; fall back to the raw text when it isn't
    valid JSON (a malformed-args loop is still a loop worth catching).
    """
    parsed = raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw or "{}")
        except Exception:
            return raw.strip()
    try:
        return json.dumps(parsed, sort_keys=True, default=str)
    except Exception:
        return str(parsed)


def _signature(msg, results: dict):
    """A hashable fingerprint of one assistant tool-call message + its answers:
    the sorted ``(name, args)`` calls and the sorted result contents. Two units
    with equal signatures made the same calls and got the same results."""
    calls, answers = [], []
    for tc in _field(msg, "tool_calls") or []:
        fn = _field(tc, "function")
        calls.append((
            str(_field(fn, "name") if fn is not None else None),
            _canonical_args(_field(fn, "arguments") if fn is not None else None),
        ))
        answers.append(results.get(_field(tc, "id")) or "")
    return tuple(sorted(calls)), tuple(sorted(answers))


def trailing_repeat(messages) -> tuple[int, str, str]:
    """Length of the trailing run of identical tool round-trips, plus the tool
    name and a result snippet for reporting. ``0`` when the tail is not an
    active repeated-tool loop (a fresh user turn, a varied tail, or no tools).
    """
    msgs = list(messages or [])
    results = {
        _field(m, "tool_call_id"): _content(m)
        for m in msgs
        if _field(m, "role") == "tool" and _field(m, "tool_call_id")
    }

    # An active loop means we just ran tools — the tail is a tool-result block.
    # Anything else (a fresh user message, a plain answer) is not a stall.
    i = len(msgs) - 1
    if i < 0 or _field(msgs[i], "role") != "tool":
        return 0, "", ""

    units: list = []
    while i >= 0:
        if _field(msgs[i], "role") != "tool":
            break  # a real boundary (user message / plain answer) ends the run
        j = i
        while j >= 0 and _field(msgs[j], "role") == "tool":
            j -= 1  # peel the contiguous tool-result block
        if j < 0 or not _field(msgs[j], "tool_calls"):
            break  # results with no answering assistant tool_calls — stop scanning
        units.append(_signature(msgs[j], results))
        i = j - 1

    if not units or not units[0][0]:
        return 0, "", ""
    last = units[0]
    n = 0
    for sig in units:
        if sig != last:
            break
        n += 1
    tool = last[0][0][0]
    snippet = (last[1][0] if last[1] else "")[:SNIPPET_CHARS]
    return n, tool, snippet


def _note(tool: str, n: int, snippet: str, *, stop: bool) -> str:
    """The guidance appended to the request.

    Phrased as instruction, never as a quoted example line: a small model will
    happily read an example of what to say straight out to the user (see
    `reference_prompt_describes_unbuilt_features`). Positive framing for the
    same reason — small models pattern-match what you show them and skip past
    what you forbid.
    """
    seen = f" (it keeps returning: {snippet!r})" if snippet else ""
    if stop:
        return (
            f"You have called `{tool}` {n} times in a row with identical arguments and "
            f"got the same result every time{seen}. That path is exhausted, and tools "
            "are unavailable for this reply. Answer the user out loud now, in one or "
            "two short spoken sentences: say what you were trying to do, that it isn't "
            "working, and ask them for the one thing that would unblock you. Keep it "
            "brief and natural."
        )
    return (
        f"You have called `{tool}` {n} times in a row with identical arguments and got "
        f"the same result every time{seen}. Repeating it will not help. Change your "
        "approach — different arguments, a different tool, or work with the result you "
        "already have. If you cannot make progress, tell the user plainly what you "
        "tried and what you need from them."
    )


def apply_tool_loop_guard(params: dict, *, supports_tool_choice: bool = True) -> dict:
    """Reshape one outbound LLM request to break a no-progress tool loop.

    Takes and returns the provider-agnostic invocation params (``messages``,
    ``tools``, ``tool_choice``). Returns a shallow copy — Ollama and MLX hand us
    the adapter's own dict, so mutating in place would write back into
    context-derived state.

    Args:
        params: the outbound invocation params.
        supports_tool_choice: whether the backend honours ``tool_choice``. True
            for OpenAI-compat endpoints, where ``"none"`` is the clean brake.
            False for Ollama / MLX, which ignore the field — there the brake has
            to be withholding ``tools`` instead. See the module docstring.

    No-op unless the tail of ``messages`` is a run of ``NUDGE_AT`` or more
    identical ``(tool, args, result)`` round-trips.
    """
    if not params.get("tools"):
        return params  # no tools offered → no loop to break
    n, tool, snippet = trailing_repeat(params.get("messages"))
    if n < NUDGE_AT:
        return params

    stop = n >= STOP_AT
    out = dict(params)
    out["messages"] = [
        *(params.get("messages") or []),
        {"role": "user", "content": _note(tool, n, snippet, stop=stop)},
    ]
    if stop:
        if supports_tool_choice:
            out["tool_choice"] = "none"
        else:
            # No tool_choice concept — withhold the schema itself. Drop the
            # field too, so a lingering "auto" can't outlive the tools it
            # referred to.
            out.pop("tools", None)
            out.pop("tool_choice", None)
        logger.warning(
            f"[tool-loop] {tool} repeated {n}x with identical args+result — "
            "tools off for this reply, forcing a spoken answer"
        )
    else:
        logger.info(f"[tool-loop] {tool} repeated {n}x — nudging for a new approach")
    return out
