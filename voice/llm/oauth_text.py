"""Chat-completions-shaped client for the OAuth subscription providers.

The out-of-pipeline text paths — the A2A text agent and ``orchestrate`` ReAct
loops (``app.py`` / ``agent/orchestrate.py``), the micro/filler tier
(``agent/filler.py``), and post-session drift analysis
(``agent/personality.py``) — all speak **non-streaming**
``client.chat.completions.create(...)`` against an ``AsyncOpenAI`` client and
read ``choices[0].message.{content,tool_calls}`` + ``usage``. The subscription
backends don't serve that API: Claude OAuth is the Anthropic Messages API and
the Codex backend is Responses-only.

:class:`OAuthTextClient` is a drop-in for those call sites: the same call
shape, translated to each backend's native protocol (identity prefix + OAuth
betas for Claude; instructions/function_call items + mandatory streaming,
aggregated, for Codex) and mapped back to an OpenAI-shaped response object.
Deliberately NOT a general adapter: non-streaming only, exactly the kwargs the
in-repo consumers use (``extra_body`` is accepted and dropped — it's a
vLLM/gateway dialect). The voice pipeline itself doesn't come through here —
it runs on the real pipecat services in voice/llm/{anthropic_oauth,openai_codex}.

Credentials are re-resolved per call (warm path = lock-free file read), so
mid-session refresh and sign-in/out behave exactly like the pipeline adapters.
"""

from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace
from typing import Any

from voice.llm.oauth import (
    NATIVE_OAUTH_PROVIDERS,
    OAuthCredentialError,
    codex_base_url,
    resolve_anthropic_oauth_cached,
    resolve_codex_oauth,
)

logger = logging.getLogger(__name__)


def is_oauth_provider(provider: str | None) -> bool:
    """True when ``provider`` names an OAuth subscription provider."""
    return (provider or "").strip().lower() in NATIVE_OAUTH_PROVIDERS


# ── OpenAI-chat → native payload builders (pure; unit-tested directly) ────────


def _parse_args(raw: Any) -> dict:
    try:
        parsed = json.loads(raw or "{}")
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def anthropic_payload(
    messages: list[dict], tools: list[dict] | None, tool_choice: Any
) -> dict:
    """Translate OpenAI-shaped chat kwargs to Anthropic Messages kwargs.

    system/developer messages fold into ``system`` (the identity line leads —
    the OAuth routing requirement); assistant ``tool_calls`` become
    ``tool_use`` blocks; ``tool`` results become ``tool_result`` blocks on a
    user turn, with consecutive results merged into one turn so the strict
    assistant/user alternation holds.
    """
    from voice.llm.anthropic_oauth import _with_identity_prefix

    system_parts: list[str] = []
    out: list[dict] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role in ("system", "developer"):
            if content:
                system_parts.append(str(content))
            continue
        if role == "assistant" and m.get("tool_calls"):
            blocks: list[dict] = []
            if content:
                blocks.append({"type": "text", "text": str(content)})
            for tc in m["tool_calls"]:
                fn = tc.get("function") or {}
                blocks.append({
                    "type": "tool_use",
                    "id": str(tc.get("id") or ""),
                    "name": str(fn.get("name") or ""),
                    "input": _parse_args(fn.get("arguments")),
                })
            out.append({"role": "assistant", "content": blocks})
            continue
        if role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": str(m.get("tool_call_id") or ""),
                "content": str(content or ""),
            }
            last = out[-1] if out else None
            if (
                last is not None
                and last["role"] == "user"
                and isinstance(last["content"], list)
                and all(b.get("type") == "tool_result" for b in last["content"])
            ):
                last["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
            continue
        if role in ("user", "assistant"):
            out.append({"role": role, "content": str(content or "")})

    payload: dict = {
        "system": _with_identity_prefix("\n\n".join(system_parts) or None),
        "messages": out,
    }
    a_tools = [
        {
            "name": t["function"]["name"],
            "description": t["function"].get("description", "") or "",
            "input_schema": t["function"].get("parameters")
            or {"type": "object", "properties": {}},
        }
        for t in (tools or [])
        if t.get("type") == "function" and t.get("function", {}).get("name")
    ]
    if a_tools:
        payload["tools"] = a_tools
        choice = {"auto": "auto", "none": "none", "required": "any"}.get(
            tool_choice if isinstance(tool_choice, str) else "auto", "auto"
        )
        payload["tool_choice"] = {"type": choice}
    return payload


def codex_payload(
    messages: list[dict], tools: list[dict] | None, tool_choice: Any
) -> dict:
    """Translate OpenAI-shaped chat kwargs to Codex Responses kwargs.

    system/developer messages ride ``instructions`` (the backend forbids
    system-role input items); assistant ``tool_calls`` become ``function_call``
    items and ``tool`` results become ``function_call_output`` items.
    """
    instruction_parts: list[str] = []
    items: list[dict] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role in ("system", "developer"):
            if content:
                instruction_parts.append(str(content))
            continue
        if role == "assistant" and m.get("tool_calls"):
            if content:
                items.append({"role": "assistant", "content": str(content)})
            for tc in m["tool_calls"]:
                fn = tc.get("function") or {}
                items.append({
                    "type": "function_call",
                    "call_id": str(tc.get("id") or ""),
                    "name": str(fn.get("name") or ""),
                    "arguments": str(fn.get("arguments") or "{}"),
                })
            continue
        if role == "tool":
            items.append({
                "type": "function_call_output",
                "call_id": str(m.get("tool_call_id") or ""),
                "output": str(content or ""),
            })
            continue
        if role in ("user", "assistant"):
            items.append({"role": role, "content": str(content or "")})

    payload: dict = {"input": items}
    if instruction_parts:
        payload["instructions"] = "\n\n".join(instruction_parts)
    r_tools = [
        {
            "type": "function",
            "name": t["function"]["name"],
            "description": t["function"].get("description", "") or "",
            "parameters": t["function"].get("parameters")
            or {"type": "object", "properties": {}},
        }
        for t in (tools or [])
        if t.get("type") == "function" and t.get("function", {}).get("name")
    ]
    if r_tools:
        payload["tools"] = r_tools
        payload["tool_choice"] = tool_choice if isinstance(tool_choice, str) else "auto"
    return payload


def _completion(
    *, content: str, tool_calls: list[SimpleNamespace], prompt_tokens: int, completion_tokens: int
) -> SimpleNamespace:
    """An OpenAI-ChatCompletion-shaped duck object — exactly the attributes the
    in-repo consumers read."""
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=tool_calls or None),
                finish_reason="tool_calls" if tool_calls else "stop",
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
        ),
    )


def _tool_call(call_id: str, name: str, arguments: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id, type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


# ── the client facade ─────────────────────────────────────────────────────────


class OAuthTextClient:
    """``AsyncOpenAI``-shaped facade (``.chat.completions.create``) over a
    subscription backend. One instance per provider; safe to cache and share
    like the real client (auth is re-resolved per call)."""

    def __init__(self, provider: str) -> None:
        provider = (provider or "").strip().lower()
        if provider not in NATIVE_OAUTH_PROVIDERS:
            raise ValueError(f"not a native OAuth provider: {provider!r}")
        self.provider = provider
        self._sdk_client: Any = None
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    async def _create(
        self,
        *,
        model: str,
        messages: list[dict],
        max_tokens: int | None = None,
        temperature: float | None = None,  # dropped: both backends reject/own sampling
        tools: list[dict] | None = None,
        tool_choice: Any = None,
        extra_body: dict | None = None,  # dropped: vLLM/gateway dialect
        **_ignored: Any,
    ) -> SimpleNamespace:
        if self.provider == "anthropic-oauth":
            return await self._create_anthropic(model, messages, max_tokens, tools, tool_choice)
        return await self._create_codex(model, messages, tools, tool_choice)

    # -- anthropic ------------------------------------------------------------

    async def _create_anthropic(self, model, messages, max_tokens, tools, tool_choice):
        from anthropic import AsyncAnthropic

        from voice.llm.anthropic_oauth import OAUTH_BETAS, oauth_default_headers

        # TTL-cached (resolution can shell out to the macOS Keychain); with a
        # token already installed, a transient resolve failure keeps it rather
        # than failing the call — same grace as the pipeline adapter.
        try:
            creds = await asyncio.to_thread(resolve_anthropic_oauth_cached)
        except OAuthCredentialError:
            if self._sdk_client is None or not getattr(self._sdk_client, "auth_token", None):
                raise
            logger.warning(
                "[oauth-text] could not re-resolve the Claude token — keeping the one in hand"
            )
            creds = None
        if self._sdk_client is None:
            self._sdk_client = AsyncAnthropic(
                api_key=None, auth_token=creds.access_token,
                default_headers=oauth_default_headers(),
            )
        if creds is not None:
            self._sdk_client.auth_token = creds.access_token

        payload = anthropic_payload(messages, tools, tool_choice)
        resp = await self._sdk_client.beta.messages.create(
            model=model,
            max_tokens=int(max_tokens) if max_tokens else 1024,
            betas=list(OAUTH_BETAS),
            **payload,
        )
        text = "".join(
            getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text"
        )
        tool_calls = [
            _tool_call(b.id, b.name, json.dumps(getattr(b, "input", None) or {}))
            for b in resp.content
            if getattr(b, "type", "") == "tool_use"
        ]
        usage = getattr(resp, "usage", None)
        return _completion(
            content=text, tool_calls=tool_calls,
            prompt_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            completion_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        )

    # -- codex ----------------------------------------------------------------

    async def _create_codex(self, model, messages, tools, tool_choice):
        import openai
        from openai.types.responses import (
            ResponseCompletedEvent,
            ResponseOutputItemDoneEvent,
            ResponseTextDeltaEvent,
        )

        from voice.llm.openai_codex import _CODEX_ORIGINATOR, _CODEX_USER_AGENT

        creds = await asyncio.to_thread(resolve_codex_oauth)
        if self._sdk_client is None:
            headers = {
                "OpenAI-Beta": "responses=experimental",
                "originator": _CODEX_ORIGINATOR,
                "User-Agent": _CODEX_USER_AGENT,
            }
            self._sdk_client = openai.AsyncOpenAI(
                api_key=creds.access_token,
                base_url=creds.base_url or codex_base_url(),
                default_headers=headers,
            )
        self._sdk_client.api_key = creds.access_token
        if creds.account_id:
            self._sdk_client._custom_headers["ChatGPT-Account-Id"] = creds.account_id

        payload = codex_payload(messages, tools, tool_choice)
        # The backend mandates streaming and owns truncation (no
        # max_output_tokens) — aggregate the stream into one response.
        stream = await self._sdk_client.responses.create(
            model=model, store=False, stream=True, **payload
        )
        text_parts: list[str] = []
        tool_calls: list[SimpleNamespace] = []
        prompt_tokens = completion_tokens = 0
        try:
            async for event in stream:
                if isinstance(event, ResponseTextDeltaEvent):
                    text_parts.append(event.delta)
                elif isinstance(event, ResponseOutputItemDoneEvent):
                    item = event.item
                    if getattr(item, "type", "") == "function_call":
                        tool_calls.append(_tool_call(
                            getattr(item, "call_id", "") or "",
                            getattr(item, "name", "") or "",
                            getattr(item, "arguments", "") or "{}",
                        ))
                elif isinstance(event, ResponseCompletedEvent):
                    if not text_parts:
                        text_parts.append(getattr(event.response, "output_text", "") or "")
                    u = getattr(event.response, "usage", None)
                    prompt_tokens = int(getattr(u, "input_tokens", 0) or 0)
                    completion_tokens = int(getattr(u, "output_tokens", 0) or 0)
        finally:
            await stream.close()
        return _completion(
            content="".join(text_parts), tool_calls=tool_calls,
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
        )
