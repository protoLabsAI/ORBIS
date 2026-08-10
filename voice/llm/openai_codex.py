"""ChatGPT/Codex-subscription LLM adapter — Responses API on the Codex backend.

``persona.llm.provider: openai-codex`` runs the voice pipeline on a ChatGPT
plan instead of an API key (ported from protoAgent ADR 0097). The ChatGPT
backend (``https://chatgpt.com/backend-api/codex``) does not serve chat
completions — it serves the **Responses API** with subscription-specific rules,
all surfaced live during protoAgent's validation:

- Bearer auth with the Codex OAuth access token + ``ChatGPT-Account-Id`` header
  + the ``codex_cli_rs`` originator the backend validates
- ``store=false`` is mandatory (pipecat's HTTP Responses service default)
- no system-role input items — the system prompt must ride the Responses
  ``instructions`` field (the pipecat adapter emits context system messages as
  ``developer`` items; we fold both roles into ``instructions``)
- ``max_output_tokens`` is rejected (the backend owns truncation) — never set
- ``stream=true`` is mandatory — pipecat's service always streams
- model ids are per-account (probe ``GET /models``; see voice/llm/oauth_login.py
  callers) — a gateway alias raises a clear error here

Using ChatGPT-subscription OAuth from a third-party app is a grayer ToS area
than the Claude path; this provider is opt-in via explicit config.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from openai.types.responses import ResponseCompletedEvent, ResponseTextDeltaEvent

from pipecat.services.openai.responses.llm import OpenAIResponsesHttpLLMService

from agent.tool_loop import apply_tool_loop_guard
from voice.llm.oauth import resolve_codex_oauth

logger = logging.getLogger(__name__)

# The originator the Codex backend validates, plus the Responses beta flag.
_CODEX_ORIGINATOR = "codex_cli_rs"
_CODEX_USER_AGENT = "ORBIS-codex/0.1 (+https://github.com/protoLabsAI/ORBIS)"


class CodexLLMService(OpenAIResponsesHttpLLMService):
    """Responses-API service pointed at the ChatGPT/Codex subscription backend."""

    def __init__(self, *, settings) -> None:
        creds = resolve_codex_oauth()  # raises OAuthCredentialError when signed out
        headers = {
            "OpenAI-Beta": "responses=experimental",
            "originator": _CODEX_ORIGINATOR,
            "User-Agent": _CODEX_USER_AGENT,
        }
        if creds.account_id:
            headers["ChatGPT-Account-Id"] = creds.account_id
        else:
            logger.warning(
                "[openai-codex] no ChatGPT account id resolved from the token — the "
                "backend may reject requests; sign in again to refresh credentials."
            )
        super().__init__(
            api_key=creds.access_token,
            base_url=creds.base_url,
            default_headers=headers,
            settings=settings,
        )
        # The tool-loop guard needs the universal context; _process_context
        # stashes it for _build_response_params (same-task, sequential).
        self._guard_context = None

    async def _process_context(self, context) -> None:
        # Re-resolve the access token before each turn: warm path is a
        # lock-free file read; an expiring token refreshes over HTTP in a
        # worker thread (serialized per store) so two consumers can't both
        # spend the single-use refresh token.
        creds = await asyncio.to_thread(resolve_codex_oauth)
        self._client.api_key = creds.access_token
        self._guard_context = context
        try:
            await super()._process_context(context)
        finally:
            self._guard_context = None

    def _build_response_params(self, invocation_params) -> dict:
        params = super()._build_response_params(invocation_params)

        # The Codex backend forbids system-role input items and ignores
        # developer items' intent — fold both into the `instructions` field
        # (protoAgent's CodexResponsesInputMiddleware, ported).
        input_items = list(params.get("input") or [])
        folded: list[str] = []
        kept: list[Any] = []
        for item in input_items:
            role = item.get("role") if isinstance(item, dict) else None
            if role in ("system", "developer"):
                content = item.get("content")
                text = content if isinstance(content, str) else _text_of(content)
                if text:
                    folded.append(text)
            else:
                kept.append(item)
        if folded:
            existing = str(params.get("instructions") or "")
            params["instructions"] = "\n\n".join(([existing] if existing else []) + folded)
            params["input"] = kept

        # The backend owns truncation and rejects max_output_tokens.
        params.pop("max_output_tokens", None)

        # Tool-loop guard: detect on the universal context messages, then brake
        # with the Responses API's own tool_choice control.
        context = self._guard_context
        if context is not None and params.get("tools"):
            guard_in = {"messages": context.get_messages(), "tools": params["tools"]}
            guard_out = apply_tool_loop_guard(guard_in)
            if guard_out is not guard_in:
                note = guard_out["messages"][-1]["content"]
                params["input"] = [*params["input"], {"role": "user", "content": note}]
                if guard_out.get("tool_choice") == "none":
                    params["tool_choice"] = "none"
        return params

    async def run_inference(self, context, max_tokens=None, system_instruction=None):
        """Out-of-band inference (context summarization). The base class forces
        ``stream=False`` here, which the Codex backend rejects — stream and
        aggregate instead."""
        creds = await asyncio.to_thread(resolve_codex_oauth)
        self._client.api_key = creds.access_token
        adapter = self.get_llm_adapter()
        invocation_params = adapter.get_llm_invocation_params(
            context, system_instruction=system_instruction or self._settings.system_instruction
        )
        params = self._build_response_params(invocation_params)
        params["stream"] = True  # mandatory on this backend (max_tokens is ignored: it owns truncation)
        stream = await self._client.responses.create(**params)
        parts: list[str] = []
        try:
            async for event in stream:
                if isinstance(event, ResponseTextDeltaEvent):
                    parts.append(event.delta)
                elif isinstance(event, ResponseCompletedEvent) and not parts:
                    parts.append(event.response.output_text)
        finally:
            await stream.close()
        return "".join(parts) or None


def _text_of(content: Any) -> str:
    """Flatten a Responses content-part list to plain text."""
    if not isinstance(content, list):
        return ""
    parts = []
    for part in content:
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            parts.append(part["text"])
    return "\n".join(parts)


def build_codex_llm(*, model: str, settings) -> Any:
    """Build the Codex-subscription service for ``provider: openai-codex``.

    ``settings`` is the ``OpenAILLMService.Settings`` the pipeline already
    assembled — translated here (model only: the Codex backend rejects
    ``max_output_tokens`` and manages sampling itself).
    """
    name = (model or "").strip()
    if not name or "/" in name:
        raise RuntimeError(
            f"llm.provider is 'openai-codex' but llm.model={name!r} is not a Codex "
            "model id for this account (pick one from the sign-in step's model list)."
        )
    svc_settings = CodexLLMService.Settings(model=name)
    logger.info(f"[llm-factory] using openai-codex adapter model={name}")
    return CodexLLMService(settings=svc_settings)
