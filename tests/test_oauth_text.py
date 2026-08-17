"""OAuthTextClient — the chat-completions facade for the subscription providers.

The A2A text agent, orchestrate, the micro/filler tier, and drift analysis all
speak non-streaming ``chat.completions.create``; on an OAuth main LLM those
must translate to the native protocol instead of 404ing against the
subscription backend. Pure payload builders are tested directly; the client
round-trips run against fake SDK clients (no network); the wiring tests pin
that every consumer actually routes through the facade.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

import voice.llm.oauth as oauth
import voice.llm.oauth_text as oauth_text
from voice.llm.anthropic_oauth import CLAUDE_CODE_SYSTEM_PREFIX, OAUTH_BETAS
from voice.llm.oauth_text import (
    OAuthTextClient,
    anthropic_payload,
    codex_payload,
    is_oauth_provider,
)


@pytest.fixture(autouse=True)
def _isolated_stores(monkeypatch, tmp_path):
    monkeypatch.setenv("ORBIS_OAUTH_DIR", str(tmp_path / "oauth"))
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr(oauth, "_CLAUDE_CREDS_FILE", tmp_path / "claude-creds.json")
    monkeypatch.setattr(oauth, "_CODEX_CLI_AUTH_FILE", tmp_path / "codex-auth.json")
    monkeypatch.setattr(oauth, "_read_claude_keychain", lambda: None)
    oauth._reset_resolve_cache()
    yield
    oauth._reset_resolve_cache()


# A ReAct-shaped history: system, user, an assistant tool call, and two tool
# results — the exact wire shape app.py / orchestrate.py append.
TOOLS = [{
    "type": "function",
    "function": {
        "name": "delegate_to",
        "description": "hand off",
        "parameters": {"type": "object", "properties": {"agent": {"type": "string"}}},
    },
}]
HISTORY = [
    {"role": "system", "content": "you are orb"},
    {"role": "user", "content": "check the deploy"},
    {
        "role": "assistant",
        "content": "on it",
        "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "delegate_to", "arguments": '{"agent": "proto"}'}},
            {"id": "c2", "type": "function",
             "function": {"name": "delegate_to", "arguments": '{"agent": "gina"}'}},
        ],
    },
    {"role": "tool", "tool_call_id": "c1", "content": "proto: done"},
    {"role": "tool", "tool_call_id": "c2", "content": "gina: done"},
]


# --- payload builders -------------------------------------------------------


def test_anthropic_payload_shapes():
    p = anthropic_payload(HISTORY, TOOLS, "auto")
    # system folds out of messages, identity line leads
    assert p["system"].startswith(CLAUDE_CODE_SYSTEM_PREFIX)
    assert "you are orb" in p["system"]
    roles = [m["role"] for m in p["messages"]]
    assert roles == ["user", "assistant", "user"]
    # assistant turn carries text + both tool_use blocks with PARSED input
    blocks = p["messages"][1]["content"]
    assert blocks[0] == {"type": "text", "text": "on it"}
    assert blocks[1]["type"] == "tool_use" and blocks[1]["input"] == {"agent": "proto"}
    # both tool results merge into ONE user turn (strict alternation)
    results = p["messages"][2]["content"]
    assert [b["tool_use_id"] for b in results] == ["c1", "c2"]
    assert all(b["type"] == "tool_result" for b in results)
    # tools translated to input_schema shape
    assert p["tools"][0]["name"] == "delegate_to"
    assert p["tools"][0]["input_schema"]["type"] == "object"
    assert p["tool_choice"] == {"type": "auto"}


def test_anthropic_payload_without_system_is_identity_only():
    p = anthropic_payload([{"role": "user", "content": "hi"}], None, None)
    assert p["system"] == CLAUDE_CODE_SYSTEM_PREFIX
    assert "tools" not in p


def test_codex_payload_shapes():
    p = codex_payload(HISTORY, TOOLS, "auto")
    # system rides instructions, never an input item
    assert p["instructions"] == "you are orb"
    kinds = [(i.get("type") or i.get("role")) for i in p["input"]]
    assert kinds == [
        "user", "assistant",
        "function_call", "function_call",
        "function_call_output", "function_call_output",
    ]
    fc = p["input"][2]
    assert (fc["call_id"], fc["name"]) == ("c1", "delegate_to")
    assert fc["arguments"] == '{"agent": "proto"}'  # arguments stay a JSON string
    assert p["input"][4] == {"type": "function_call_output", "call_id": "c1", "output": "proto: done"}
    # tools flattened to the Responses shape
    assert p["tools"][0] == {
        "type": "function", "name": "delegate_to", "description": "hand off",
        "parameters": TOOLS[0]["function"]["parameters"],
    }
    assert p["tool_choice"] == "auto"


# --- client round-trips (fake SDK clients, no network) ----------------------


def test_anthropic_roundtrip(monkeypatch):
    monkeypatch.setattr(
        oauth_text, "resolve_anthropic_oauth_cached",
        lambda: oauth.AnthropicOAuthCreds(access_token="tok-live", source="env"),
    )
    client = OAuthTextClient("anthropic-oauth")
    seen = {}

    async def _create(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(
            content=[
                SimpleNamespace(type="text", text="handing off"),
                SimpleNamespace(type="tool_use", id="t1", name="delegate_to", input={"agent": "proto"}),
            ],
            usage=SimpleNamespace(input_tokens=11, output_tokens=7),
        )

    client._sdk_client = SimpleNamespace(
        auth_token="stale",
        beta=SimpleNamespace(messages=SimpleNamespace(create=_create)),
    )
    r = asyncio.run(client.chat.completions.create(
        model="claude-sonnet-4-5", messages=HISTORY, max_tokens=64,
        temperature=0.7, tools=TOOLS, tool_choice="auto",
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    ))
    # request: fresh token installed, OAuth betas, identity-led system, no sampling knobs
    assert client._sdk_client.auth_token == "tok-live"
    assert seen["betas"] == list(OAUTH_BETAS)
    assert seen["system"].startswith(CLAUDE_CODE_SYSTEM_PREFIX)
    assert seen["max_tokens"] == 64
    assert "temperature" not in seen and "extra_body" not in seen
    # response: OpenAI-shaped duck the consumers can read
    msg = r.choices[0].message
    assert msg.content == "handing off"
    assert msg.tool_calls[0].id == "t1"
    assert msg.tool_calls[0].function.name == "delegate_to"
    assert json.loads(msg.tool_calls[0].function.arguments) == {"agent": "proto"}
    assert (r.usage.prompt_tokens, r.usage.completion_tokens) == (11, 7)
    assert r.choices[0].finish_reason == "tool_calls"


def test_codex_roundtrip(monkeypatch):
    from openai.types.responses import (
        ResponseCompletedEvent,
        ResponseOutputItemDoneEvent,
        ResponseTextDeltaEvent,
    )

    monkeypatch.setattr(
        oauth_text, "resolve_codex_oauth",
        lambda: oauth.CodexOAuthCreds(
            access_token="tok-live", account_id="acct-1",
            base_url="https://chatgpt.com/backend-api/codex", source="instance_store",
        ),
    )
    client = OAuthTextClient("openai-codex")
    seen = {}
    events = [
        ResponseTextDeltaEvent.model_construct(delta="dele"),
        ResponseTextDeltaEvent.model_construct(delta="gating"),
        ResponseOutputItemDoneEvent.model_construct(
            item=SimpleNamespace(type="function_call", call_id="c9", name="delegate_to", arguments='{"agent": "gina"}'),
        ),
        ResponseCompletedEvent.model_construct(
            response=SimpleNamespace(
                output_text="ignored — deltas already collected",
                usage=SimpleNamespace(input_tokens=21, output_tokens=9),
            ),
        ),
    ]

    class _Stream:
        def __init__(self):
            self._it = iter(events)
            self.closed = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._it)
            except StopIteration:
                raise StopAsyncIteration from None

        async def close(self):
            self.closed = True

    stream = _Stream()

    async def _create(**kwargs):
        seen.update(kwargs)
        return stream

    client._sdk_client = SimpleNamespace(
        api_key="stale",
        _custom_headers={},
        responses=SimpleNamespace(create=_create),
    )
    r = asyncio.run(client.chat.completions.create(
        model="gpt-5-codex", messages=HISTORY, max_tokens=64, tools=TOOLS, tool_choice="auto",
    ))
    # request: backend rules (stream, store=false, instructions, no max_output_tokens),
    # fresh token + account header installed
    assert seen["stream"] is True and seen["store"] is False
    assert seen["instructions"] == "you are orb"
    assert "max_output_tokens" not in seen and "max_tokens" not in seen
    assert client._sdk_client.api_key == "tok-live"
    assert client._sdk_client._custom_headers["ChatGPT-Account-Id"] == "acct-1"
    assert stream.closed
    # response: aggregated deltas + tool call + usage
    msg = r.choices[0].message
    assert msg.content == "delegating"
    assert msg.tool_calls[0].function.arguments == '{"agent": "gina"}'
    assert (r.usage.prompt_tokens, r.usage.completion_tokens) == (21, 9)


# --- wiring -----------------------------------------------------------------


def test_is_oauth_provider():
    assert is_oauth_provider("anthropic-oauth") and is_oauth_provider("OPENAI-CODEX ")
    assert not is_oauth_provider("ollama") and not is_oauth_provider(None)


def test_get_text_client_routes_and_caches_oauth():
    import app as _app

    c1 = _app._get_text_client("https://api.anthropic.com", "", "anthropic-oauth")
    c2 = _app._get_text_client("https://api.anthropic.com", "ignored", "anthropic-oauth")
    assert isinstance(c1, OAuthTextClient) and c1 is c2  # token rotation can't leak clients
    from openai import AsyncOpenAI
    plain = _app._get_text_client("https://gw/v1", "k", None)
    assert isinstance(plain, AsyncOpenAI)


def test_filler_generator_routes_micro_tier_through_facade():
    from agent.filler import FillerGenerator

    fg = FillerGenerator(
        llm_url="https://api.anthropic.com", model="claude-haiku-4-5",
        provider="anthropic-oauth",
    )
    assert isinstance(fg._client, OAuthTextClient)
    assert fg._extra_body is None  # gateway dialect never reaches these backends


def test_resolve_skill_llm_micro_inherits_oauth_provider(monkeypatch):
    import app as _app

    for var in ("LLM_MICRO_URL", "LLM_MICRO_MODEL", "LLM_MICRO_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    skill = SimpleNamespace(llm={
        "url": "https://api.anthropic.com", "model": "claude-sonnet-4-5",
        "provider": "anthropic-oauth",
    })
    cfg = _app._resolve_skill_llm(skill)
    # micro tier shares the main URL → same wire protocol
    assert cfg["micro_provider"] == "anthropic-oauth"
    # …but an explicit local micro endpoint stays plain OpenAI-compat
    skill.llm["micro_url"] = "http://127.0.0.1:11434/v1"
    assert _app._resolve_skill_llm(skill)["micro_provider"] is None
