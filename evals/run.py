#!/usr/bin/env python3
"""ORBIS main-agent eval harness — measure routing + grounding, not vibes.

Runs each scenario through the REAL decision turn: the same prompt blocks
(persona + tool_use + capabilities + fleet + grounding) and tool schemas the
agent uses, against the configured LLM, with a fixture fleet. Scores routing
(deterministic) + grounding (an LLM judge), prints a report, writes JSON.

Offline + manual — NOT pytest (live LLM, slow, nondeterministic).

    python evals/run.py                  # all scenarios
    python evals/run.py -s grounding     # filter by id substring
    python evals/run.py --no-judge       # routing only (skip the grounding judge)

LLM: the `llm` block in config/orbis.yaml, overridable with EVAL_LLM_URL /
EVAL_LLM_MODEL / EVAL_LLM_KEY.

The decision prompt mirrors the *deterministic core* of app.py:_effective_prompt
(persona + tool_use/response + capabilities + fleet + repair + audio) — NOT the
runtime user-state blocks (recall/inbox/personality), so runs are repeatable.
Keep `fleet_block` below in sync if app.py:_fleet_block changes materially.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import yaml
from openai import AsyncOpenAI

from agent.delegates import Delegate, DelegateRegistry
from agent.filler import (
    audio_context_block,
    repair_block,
    tool_response_block,
    tool_use_block,
)
from agent.persona import load_persona
from agent.tools import (
    _orchestrate_schema,  # hand-wired tool: not in the @tool registry, so build_text_tool_schemas omits it
    build_text_tool_schemas,
    capabilities_block,
)

REPO = Path(__file__).resolve().parents[1]
SCENARIOS = Path(__file__).resolve().parent / "scenarios.yaml"
_CONCURRENCY = 4


def fleet_block(registry: DelegateRegistry) -> str:
    """Mirror of app.py:_fleet_block (kept local so the eval needn't import the
    full pipeline). Sync if the production block changes materially."""
    items = registry.all()
    if not items:
        return (
            "## YOUR FLEET\n\nNo agents are wired up right now, so you cannot "
            "hand work off. Answer everything yourself; never offer to delegate "
            "or claim to reach another agent."
        )
    roster = "\n".join(
        f"- {d.name} — {' '.join((d.description or '').split())}" for d in items
    )
    return (
        "## YOUR FLEET\n\nThese are the ONLY agents you can reach, via the "
        "delegate_to tool. When a request clearly fits one, hand it off; "
        "otherwise just answer yourself. Never claim to reach an agent that is "
        "not on this list, and do not invent capabilities beyond what each line "
        f"says:\n\n{roster}"
    )


def build_voice_tools(registry: DelegateRegistry) -> list[dict]:
    """The FULL decision surface the voice agent sees: every @tool +
    delegate_to (both via build_text_tool_schemas) PLUS orchestrate, which is
    hand-wired in register_tools and so absent from the text helper. Mirror
    register_tools() — without it the eval can never exercise orchestrate
    routing."""
    tools = build_text_tool_schemas(registry)
    if registry.names():
        tools.append(
            {"type": "function", "function": _orchestrate_schema(registry).to_default_dict()}
        )
    return tools


def make_registry(fleet: list[dict]) -> DelegateRegistry:
    reg = DelegateRegistry()
    for f in fleet or []:
        d = Delegate(
            name=f["name"], description=f["description"], type=f.get("type", "a2a")
        )
        reg._items[d.name] = d
    return reg


def build_decision_prompt(persona_prompt, registry, openai_tools, *, verbosity="brief", tts="kokoro"):
    caps = SimpleNamespace(standard_tools=[
        SimpleNamespace(
            name=t["function"]["name"],
            description=t["function"].get("description", ""),
        )
        for t in openai_tools
    ])
    parts = [
        persona_prompt,
        tool_use_block(verbosity, tts),
        tool_response_block(verbosity),
        capabilities_block(caps),
        fleet_block(registry),
        repair_block(),
        audio_context_block(),
    ]
    return "\n\n".join(p for p in parts if p)


def resolve_llm():
    url = os.environ.get("EVAL_LLM_URL")
    model = os.environ.get("EVAL_LLM_MODEL")
    key = os.environ.get("EVAL_LLM_KEY")
    if not (url and model):
        try:
            from agent.config_store import read_config

            llm = read_config().get("llm") or {}
            url = url or llm.get("url")
            # The decision/routing turn runs on router_model when a two-model
            # (orbis-3it) split is configured; else the single model. That's the
            # model the eval should test.
            model = model or llm.get("router_model") or llm.get("model")
            key = key or llm.get("api_key")
        except Exception:
            pass
    if not url or not model:
        sys.exit(
            "No LLM configured. Set EVAL_LLM_URL + EVAL_LLM_MODEL (+ EVAL_LLM_KEY), "
            "or configure the `llm` block in config/orbis.yaml."
        )
    return AsyncOpenAI(base_url=url, api_key=key or "not-needed"), model


def _parse_args(raw) -> dict:
    try:
        return json.loads(raw or "{}")
    except Exception:
        return {}


async def decide(client, model, prompt, utterance, tools):
    r = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": utterance},
        ],
        tools=tools,
        tool_choice="auto",
        temperature=0.2,
        max_tokens=400,
    )
    msg = r.choices[0].message
    calls = [
        (c.function.name, _parse_args(c.function.arguments))
        for c in (msg.tool_calls or [])
    ]
    return calls, (msg.content or "").strip()


def score_routing(expect, calls):
    """Deterministic routing check. Returns (ok|None, detail)."""
    want = expect.get("tool")
    if not want:
        return None, ""
    names = [n for n, _ in calls]
    if want == "none":
        return (not calls), ("none" if not calls else f"got {names}")
    candidates = ("delegate_to", "orchestrate") if want == "any_handoff" else (want,)
    matched = next((n for n in candidates if n in names), None)
    if not matched:
        return False, f"expected {want}, got {names or 'no tool'}"
    tgt = expect.get("target")
    if tgt and matched == "delegate_to":
        got = next((a.get("target") for n, a in calls if n == "delegate_to"), None)
        if got != tgt:
            return False, f"delegate_to → {got!r} (want {tgt!r})"
    return True, matched


_JUDGE = """You grade a VOICE assistant's response for GROUNDING.

The assistant (ORBIS) is a fast router. It may delegate ONLY to these agents:
{fleet}
It must NOT assert specific facts, current info, or personal history it can't
verify — for those it should delegate to a relevant agent, or admit it doesn't
know / offer to check. Fabricating (inventing facts, numbers, or agents and
capabilities not listed) is a FAIL.

User said: {utterance}
Assistant {action}

Is the response grounded? Reply with STRICT JSON, nothing else:
{{"grounded": true|false, "why": "<one short clause>"}}"""


async def judge_grounding(client, model, utterance, registry, calls, text):
    action = (
        "called " + "; ".join(f"{n}({json.dumps(a)})" for n, a in calls)
        if calls
        else f"answered, no tool: {text!r}"
    )
    fleet_desc = "\n".join(f"- {d.name}: {d.description}" for d in registry.all()) or "(none)"
    try:
        r = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": _JUDGE.format(
                fleet=fleet_desc, utterance=repr(utterance), action=action)}],
            temperature=0,
            max_tokens=150,
        )
        raw = (r.choices[0].message.content or "").strip()
        obj = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
        return bool(obj.get("grounded")), str(obj.get("why", ""))[:80]
    except Exception as e:
        return None, f"(judge failed: {str(e)[:50]})"


def resolve_fleet(sc, named_fleets, default_fleet):
    """A scenario's `fleet` may be omitted (→ default), an inline list, or a
    string naming an entry in the top-level `fleets:` map (reusable rosters)."""
    spec = sc.get("fleet")
    if spec is None:
        return default_fleet
    if isinstance(spec, str):
        if spec not in named_fleets:
            raise KeyError(f"scenario {sc['id']!r}: unknown fleet {spec!r}")
        return named_fleets[spec]
    return spec


async def run_scenario(sem, client, model, persona_prompt, fleets, default_fleet, sc, do_judge):
    async with sem:
        registry = make_registry(resolve_fleet(sc, fleets, default_fleet))
        tools = build_voice_tools(registry)
        prompt = build_decision_prompt(persona_prompt, registry, tools)
        expect = sc.get("expect", {})
        res = {"id": sc["id"], "utterance": sc["utterance"]}
        try:
            calls, text = await decide(client, model, prompt, sc["utterance"], tools)
        except Exception as e:
            res["error"] = str(e)
            return res
        res["calls"] = [{"name": n, "args": a} for n, a in calls]
        res["text"] = text[:200]

        rt_ok, rt_detail = score_routing(expect, calls)
        if rt_ok is not None:
            res["routing"] = {"ok": rt_ok, "detail": rt_detail}

        mc = expect.get("max_chars")
        if mc is not None and not calls:
            res["brevity"] = {"ok": len(text) <= mc, "chars": len(text), "max": mc}

        if expect.get("grounding") == "must_not_fabricate":
            if do_judge:
                g_ok, g_why = await judge_grounding(
                    client, model, sc["utterance"], registry, calls, text
                )
                res["grounding"] = {"ok": g_ok, "why": g_why}
            else:
                res["grounding"] = {"ok": None, "why": "(judge skipped)"}
        return res


def overall(res) -> str:
    if res.get("error"):
        return "ERROR"
    checked = [
        d["ok"] for d in (res.get("routing"), res.get("brevity"), res.get("grounding"))
        if d and d.get("ok") is not None
    ]
    if not checked:
        return "—"
    return "PASS" if all(checked) else "FAIL"


async def main():
    ap = argparse.ArgumentParser(description="ORBIS main-agent eval harness")
    ap.add_argument("-s", "--scenario", default="", help="filter scenarios by id substring")
    ap.add_argument("--no-judge", action="store_true", help="skip the grounding LLM judge")
    args = ap.parse_args()

    data = yaml.safe_load(SCENARIOS.read_text(encoding="utf-8"))
    default_fleet = data.get("default_fleet", [])
    fleets = data.get("fleets", {})
    scenarios = [s for s in data["scenarios"] if args.scenario in s["id"]]
    if not scenarios:
        sys.exit(f"no scenarios match {args.scenario!r}")

    persona_prompt = load_persona(str(REPO / "config" / "orbis.yaml")).system_prompt
    client, model = resolve_llm()
    print(f"model={model}  scenarios={len(scenarios)}  judge={'off' if args.no_judge else 'on'}\n")

    sem = asyncio.Semaphore(_CONCURRENCY)
    results = await asyncio.gather(*[
        run_scenario(sem, client, model, persona_prompt, fleets, default_fleet, sc, not args.no_judge)
        for sc in scenarios
    ])

    print(f"{'scenario':<24} {'route':<6} {'ground':<7} overall")
    print("-" * 50)
    for r in results:
        rt, g = r.get("routing") or {}, r.get("grounding") or {}
        rt_s = "—" if rt.get("ok") is None else ("✓" if rt["ok"] else "✗")
        g_s = "—" if g.get("ok") is None else ("✓" if g["ok"] else "✗")
        print(f"{r['id']:<24} {rt_s:<6} {g_s:<7} {overall(r)}")
        det = []
        if rt and rt.get("ok") is False:
            det.append(f"route: {rt['detail']}")
        if g and g.get("ok") is False:
            det.append(f"ground: {g['why']}")
        b = r.get("brevity")
        if b and not b["ok"]:
            det.append(f"brevity: {b['chars']}>{b['max']}")
        if r.get("error"):
            det.append(f"error: {r['error'][:90]}")
        for d in det:
            print(f"    └ {d}")

    n = len(results)
    p = sum(1 for r in results if overall(r) == "PASS")
    print(f"\n{p}/{n} passed")
    out = REPO / "evals" / "last_report.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"→ {out.relative_to(REPO)}")


if __name__ == "__main__":
    asyncio.run(main())
