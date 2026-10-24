"""Claude 5 compatibility for the Anthropic OAuth adapter (voice/llm/anthropic_oauth.py).

- pipecat injects ``thinking: {"type": "disabled"}`` for sonnet-N N>=5 when unset,
  which Anthropic rejects with a 400 — so 5+ ids must carry ``between_tools``.
- the persona's brevity ``max_tokens`` is floored on Claude (thinking blocks and
  half-written tool_use blocks count against it).
- abnormal ``stop_reason`` (e.g. max_tokens) is logged.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest
from pipecat.services.openai.llm import OpenAILLMService

import voice.llm.anthropic_oauth as mod
from voice.llm import oauth
from voice.llm.anthropic_oauth import (
    _log_stop_reason,
    build_anthropic_oauth_llm,
    claude_max_tokens,
    claude_thinking_param,
)


@pytest.mark.parametrize(
    "model,expected",
    [
        ("claude-sonnet-4-5", None),
        ("claude-opus-4-1", None),
        ("claude-haiku-4-5", None),
        ("claude-sonnet-4-20250514", None),
        ("claude-3-5-sonnet-20241022", None),
        ("claude-sonnet-5-5", {"type": "between_tools"}),
        ("claude-opus-5-5", {"type": "between_tools"}),
        ("claude-haiku-5-5", {"type": "between_tools"}),
        ("claude-fable-5-1", {"type": "between_tools"}),
        ("anthropic.claude-sonnet-5-5-v1:0", {"type": "between_tools"}),
        ("", None),
    ],
)
def test_claude_thinking_param(model, expected):
    assert claude_thinking_param(model) == expected


@pytest.mark.parametrize(
    "configured,expected", [(150, 1024), (4096, 4096), (None, 1024), (0, 1024)]
)
def test_claude_max_tokens_floor(configured, expected):
    assert claude_max_tokens(configured) == expected


def _build(monkeypatch, model, max_tokens):
    monkeypatch.setattr(
        mod,
        "resolve_anthropic_oauth_cached",
        lambda: oauth.AnthropicOAuthCreds(access_token="tok", source="env"),
    )
    return build_anthropic_oauth_llm(
        model=model,
        settings=OpenAILLMService.Settings(model=model, max_tokens=max_tokens),
    )


def test_build_sets_between_tools_and_floor_on_claude_5(monkeypatch):
    s = _build(monkeypatch, "claude-sonnet-5-5", 150)._settings
    assert (
        s.thinking.type == "between_tools"
    )  # pipecat would otherwise inject "disabled"
    assert s.max_tokens == 1024


def test_build_leaves_claude_4_thinking_unset(monkeypatch):
    svc = _build(monkeypatch, "claude-sonnet-4-5", 150)
    assert not isinstance(
        getattr(svc._settings, "thinking", None), mod.AnthropicLLMService.ThinkingConfig
    )
    assert svc._settings.max_tokens == 1024


def test_stop_reason_logged_only_when_abnormal(caplog):
    def delta(reason, out):
        return SimpleNamespace(
            type="message_delta",
            delta=SimpleNamespace(stop_reason=reason),
            usage=SimpleNamespace(output_tokens=out),
        )

    async def stream(*events):
        for e in events:
            yield e

    async def drain(*events):
        return [e async for e in _log_stop_reason(stream(*events), "m")]

    with caplog.at_level(logging.INFO, logger="voice.llm.anthropic_oauth"):
        out = asyncio.run(
            drain(delta("end_turn", 5), delta("tool_use", 6), delta("max_tokens", 150))
        )
    assert len(out) == 3  # events pass through untouched
    msgs = [r.getMessage() for r in caplog.records]
    assert (
        len(msgs) == 1
        and "stop_reason=max_tokens" in msgs[0]
        and "output_tokens=150" in msgs[0]
    )
