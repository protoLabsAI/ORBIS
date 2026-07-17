"""Tool-loop guard — breaks a no-progress repeated tool call on the voice path.

Policy lives in ``agent/tool_loop.py`` (pure, plain dicts in/out). These tests
pin the detection truth table, the two escalating actions, and — the part that
rots silently — that all four LLM backends actually route through it. Ollama and
MLX bypass ``build_chat_completion_params``, so they are the ones that can drift.

Mirrors ``tests/test_orchestrate.py`` (scripted messages, no model, no network).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from pipecat.services.openai.llm import OpenAILLMService

from agent.tool_loop import apply_tool_loop_guard, trailing_repeat
from voice.llm import make_llm
from voice.llm.guarded import GuardedOpenAILLMService
from voice.llm.two_model import TwoModelOpenAILLMService

TOOL = [{"type": "function", "function": {"name": "check_status"}}]


def _round_trip(i: int, *, name="check_status", args='{"id": "7"}', result="still pending"):
    """One assistant tool call + its result, in OpenAI wire shape. Each gets a
    distinct call id — as on the wire — so an id is never what makes two
    round-trips compare equal."""
    cid = f"call_{i}"
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": cid, "type": "function",
                 "function": {"name": name, "arguments": args}},
            ],
        },
        {"role": "tool", "tool_call_id": cid, "content": result},
    ]


def _history(n: int, **kw):
    msgs: list = [
        {"role": "system", "content": "you are orb"},
        {"role": "user", "content": "what's the status?"},
    ]
    for i in range(n):
        msgs += _round_trip(i, **kw)
    return msgs


def _params(messages, tools=TOOL):
    return {"messages": messages, "tools": tools, "tool_choice": "auto"}


def _note_of(out) -> str:
    """The guard's appended guidance — always the last message."""
    return out["messages"][-1]["content"]


# --- trailing_repeat -------------------------------------------------------


def test_reports_count_tool_and_snippet() -> None:
    assert trailing_repeat(_history(4)) == (4, "check_status", "still pending")


def test_empty_history_is_not_a_loop() -> None:
    assert trailing_repeat([]) == (0, "", "")
    assert trailing_repeat(None) == (0, "", "")


def test_args_key_order_does_not_defeat_detection() -> None:
    """On the wire `arguments` is a JSON *string*, so the same call can arrive
    with different key order on different turns. It's still the same call."""
    msgs = (
        _history(0)
        + _round_trip(0, args='{"a": 1, "b": 2}')
        + _round_trip(1, args='{"b": 2, "a": 1}')
    )
    n, _, _ = trailing_repeat(msgs)
    assert n == 2


def test_handles_object_messages() -> None:
    """Messages reach the services as plain dicts or as pydantic params."""
    def _obj(m):
        if "tool_calls" in m:
            return SimpleNamespace(
                role=m["role"], content=m["content"],
                tool_calls=[
                    SimpleNamespace(id=tc["id"], function=SimpleNamespace(**tc["function"]))
                    for tc in m["tool_calls"]
                ],
            )
        return SimpleNamespace(**m)

    n, tool, _ = trailing_repeat([_obj(m) for m in _history(3)])
    assert (n, tool) == (3, "check_status")


def test_multi_tool_call_unit_is_matched_as_a_set() -> None:
    """One assistant message can fan out several calls; the unit repeats only
    if the whole set — and every answer — repeats."""
    def _fan(i):
        a, b = f"a{i}", f"b{i}"
        return [
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": a, "type": "function",
                 "function": {"name": "check_status", "arguments": '{"id": "7"}'}},
                {"id": b, "type": "function",
                 "function": {"name": "read_file", "arguments": '{"p": "x"}'}},
            ]},
            {"role": "tool", "tool_call_id": a, "content": "still pending"},
            {"role": "tool", "tool_call_id": b, "content": "no such file"},
        ]

    n, _, _ = trailing_repeat(_history(0) + _fan(0) + _fan(1))
    assert n == 2


# --- what is NOT a loop ----------------------------------------------------


def test_varied_results_is_not_a_loop() -> None:
    """Same call, moving result — the tool is doing something. Left alone."""
    msgs = _history(0)
    for i in range(6):
        msgs += _round_trip(i, result=f"{i} items processed")
    assert trailing_repeat(msgs)[0] == 1
    assert apply_tool_loop_guard(_params(msgs)) == _params(msgs)


def test_varied_args_is_not_a_loop() -> None:
    """Same tool, different arguments — the model is exploring, not stuck."""
    msgs = _history(0)
    for i in range(6):
        msgs += _round_trip(i, args=json.dumps({"id": str(i)}))
    assert trailing_repeat(msgs)[0] == 1
    assert apply_tool_loop_guard(_params(msgs)) == _params(msgs)


def test_fresh_user_turn_is_never_stalled() -> None:
    """The tail must be a tool-result block for a loop to be active. A new user
    message means the model is answering, not spinning."""
    msgs = _history(6) + [{"role": "user", "content": "ok, forget it — what time is it?"}]
    assert trailing_repeat(msgs)[0] == 0
    assert apply_tool_loop_guard(_params(msgs)) == _params(msgs)


def test_real_user_message_breaks_the_run() -> None:
    """Mid-turn steering (a barge-in) resets the count — the model has been
    given new information, so it deserves a fresh budget."""
    msgs = _history(3) + [{"role": "user", "content": "try the other one"}]
    for i in range(3, 5):
        msgs += _round_trip(i)
    n, _, _ = trailing_repeat(msgs)
    assert n == 2  # only the post-steer run counts
    out = apply_tool_loop_guard(_params(msgs))
    assert out["tools"] == TOOL  # → nudge, not stop


def test_no_tools_offered_is_a_no_op() -> None:
    """Nothing to loop on, and nothing to withhold."""
    for tools in ([], None):
        p = _params(_history(6), tools=tools)
        assert apply_tool_loop_guard(p) is p


# --- the two escalating actions --------------------------------------------


def test_below_threshold_is_untouched() -> None:
    p = _params(_history(1))
    assert apply_tool_loop_guard(p) is p


def test_nudge_keeps_tools_available() -> None:
    """At NUDGE_AT the model is told to change approach but keeps its tools —
    this is the recovery path, not the brake."""
    out = apply_tool_loop_guard(_params(_history(2)))
    assert out["tools"] == TOOL
    assert out["tool_choice"] == "auto"
    assert len(out["messages"]) == len(_history(2)) + 1
    note = _note_of(out)
    assert "check_status" in note and "still pending" in note


def test_stop_on_openai_uses_tool_choice_none() -> None:
    """On an OpenAI-compat backend the brake is `tool_choice: "none"` — the
    API's own answer to "don't call a tool". `tools` stays put: dropping it
    while the history carries tool_calls is a contract change, and the gateway
    answers unexpected params with a 400 + a silent model-group fallback."""
    out = apply_tool_loop_guard(_params(_history(3)))
    assert out["tool_choice"] == "none"
    assert out["tools"] == TOOL
    assert "out loud" in _note_of(out)


def test_stop_without_tool_choice_support_withholds_tools() -> None:
    """Ollama/MLX ignore `tool_choice` entirely, so setting it would leave the
    guard silently doing nothing. There the brake has to be the schema."""
    out = apply_tool_loop_guard(_params(_history(3)), supports_tool_choice=False)
    assert "tools" not in out
    assert "tool_choice" not in out  # can't outlive the tools it referred to
    assert "out loud" in _note_of(out)


def test_guard_re_applies_past_the_stop_threshold() -> None:
    """The edit is request-only, so there is no note in history for the model to
    remember. The thresholds must therefore be >=, not equality — a run that
    somehow reaches 5 must still be braked."""
    assert apply_tool_loop_guard(_params(_history(5)))["tool_choice"] == "none"


def test_does_not_mutate_the_caller() -> None:
    """Ollama and MLX hand us the adapter's own dict — writing into it would
    write back into context-derived state."""
    msgs = _history(3)
    p = _params(msgs)
    before = json.dumps(p, sort_keys=True)
    apply_tool_loop_guard(p)
    assert json.dumps(p, sort_keys=True) == before
    assert len(msgs) == len(_history(3))


# --- backend wiring --------------------------------------------------------
#
# The guard is only worth anything if every backend reaches it. The OpenAI path
# inherits it; Ollama and MLX bypass build_chat_completion_params entirely and
# have to opt in by hand, so these are the ones that can rot.


def test_openai_path_is_guarded() -> None:
    svc = make_llm(base_url="https://gw/v1", model="m", api_key="k",
                   settings=OpenAILLMService.Settings(model="m"))
    assert isinstance(svc, GuardedOpenAILLMService)


def test_two_model_path_is_guarded() -> None:
    assert issubclass(TwoModelOpenAILLMService, GuardedOpenAILLMService)


def test_two_model_build_params_applies_the_guard() -> None:
    """Pins that the model-swap override doesn't shadow the guarded super()."""
    svc = TwoModelOpenAILLMService(
        api_key="k", base_url="https://gw/v1",
        settings=OpenAILLMService.Settings(model="base"),
        router_model="protolabs/smart", content_model="protolabs/fast",
    )
    params = svc.build_chat_completion_params(_params(_history(3)))
    assert params["tool_choice"] == "none"
    assert params["model"] == "protolabs/fast"  # stalled turn narrates


def test_ollama_applies_the_guard(monkeypatch) -> None:
    """Real call through the adapter's own get_chat_completions, spying on what
    it forwards to Ollama. No network: _stream_as_openai_chunks is an async
    generator function, so calling it doesn't execute the body."""
    from voice.llm import ollama as _ol

    svc = _ol.OllamaLLMService(
        base_url="http://127.0.0.1:11434/v1", model="m",
        settings=OpenAILLMService.Settings(model="m"),
    )
    monkeypatch.setattr(svc, "get_llm_adapter", lambda: SimpleNamespace(
        get_llm_invocation_params=lambda *a, **k: _params(_history(3))
    ))
    seen: dict = {}

    def _fake(http, root, model, messages, *, think, tools):
        seen["tools"], seen["messages"] = tools, messages
        async def _gen():
            return
            yield
        return _gen()

    monkeypatch.setattr(_ol, "_stream_as_openai_chunks", _fake)
    asyncio.run(svc.get_chat_completions(context=None))
    assert seen["tools"] is None  # withheld
    assert "out loud" in seen["messages"][-1]["content"]


def test_mlx_applies_the_guard() -> None:
    """Source-level: mlx.py imports mlx_lm at module scope (Apple-Silicon only),
    so CI can't import it to check behaviourally. Assert the call is present in
    get_chat_completions — a tripwire against someone editing the params block
    and dropping the guard."""
    src = Path("voice/llm/mlx.py").read_text()
    body = src.split("async def get_chat_completions", 1)[1].split("\nasync def ", 1)[0]
    assert "apply_tool_loop_guard(params, supports_tool_choice=False)" in body
