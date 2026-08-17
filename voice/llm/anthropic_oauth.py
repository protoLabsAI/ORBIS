"""Claude-subscription LLM adapter — Bearer OAuth on pipecat's Anthropic service.

``persona.llm.provider: anthropic-oauth`` runs the voice pipeline on a Claude
Pro/Max plan instead of an API key (ported from protoAgent ADR 0097). The
Messages API accepts a subscription OAuth token only when the request looks
like Claude Code's own traffic:

- ``Authorization: Bearer <token>`` (not ``x-api-key``)
- ``anthropic-beta: claude-code-20250219,oauth-2025-04-20``
- ``User-Agent: claude-code/<version> (external, cli)``
- a system prompt whose FIRST block is the Claude Code identity line — without
  it, OAuth requests intermittently 500 / are refused

pipecat's ``AnthropicLLMService`` accepts a custom ``AsyncAnthropic`` client, so
Bearer auth is just ``auth_token=`` on the client. The identity line is injected
in the adapter (the one seam both ``_process_context`` and ``run_inference``
share), the beta list is merged on both request paths, and the access token is
re-resolved before every request so a mid-session expiry refreshes transparently
(TTL-cached in voice/llm/oauth.py — resolution can shell out to the macOS
Keychain, so the warm path must stay off the hot path).
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from typing import Any

from agent.tool_loop import apply_tool_loop_guard
from voice.llm.oauth import OAuthCredentialError, resolve_anthropic_oauth_cached

logger = logging.getLogger(__name__)

# Beta headers subscription/OAuth traffic requires (matches Claude Code / OpenCode).
OAUTH_BETAS = ["claude-code-20250219", "oauth-2025-04-20"]
# Anthropic rejects OAuth requests whose spoofed client version is too far behind
# the real release, so we detect the installed Claude Code version and only fall
# back to a recent-enough constant when it can't be found.
_CLAUDE_CODE_VERSION_FALLBACK = "2.1.74"
# The first system block OAuth traffic must carry.
CLAUDE_CODE_SYSTEM_PREFIX = "You are Claude Code, Anthropic's official CLI for Claude."
# The token a signed-out boot presents; _refresh_auth treats it as "no token in
# hand" so a genuinely-signed-out resolve failure still raises into the
# ErrorFrame path (the announcer tells the user to sign in).
_SIGNED_OUT_SENTINEL = "signed-out"

_version_cache: str | None = None


def _claude_code_version() -> str:
    global _version_cache
    if _version_cache is not None:
        return _version_cache
    for cmd in ("claude", "claude-code"):
        try:
            out = subprocess.run(
                [cmd, "--version"], capture_output=True, text=True, timeout=5
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if out.returncode == 0 and out.stdout.strip():
            token = out.stdout.strip().split()[0]
            if token and token[0].isdigit():
                _version_cache = token
                return token
    _version_cache = _CLAUDE_CODE_VERSION_FALLBACK
    return _version_cache


def oauth_default_headers() -> dict[str, str]:
    """The identity + beta headers every OAuth request must carry.

    The ``anthropic-beta`` default only covers requests that don't pass a
    ``betas=`` param (a per-request ``betas`` list REPLACES this header —
    both request paths merge :data:`OAUTH_BETAS` into theirs for that reason).
    """
    return {
        "anthropic-beta": ",".join(OAUTH_BETAS),
        "User-Agent": f"claude-code/{_claude_code_version()} (external, cli)",
    }


def _with_identity_prefix(system: Any) -> Any:
    """Prepend the Claude Code identity line as the first system block.

    ``system`` is whatever the Anthropic adapter produced: a string, a list of
    text blocks (possibly carrying ``cache_control``), or a not-given sentinel.
    Idempotent — re-running never stacks the prefix.
    """
    if isinstance(system, str):
        if system.lstrip().startswith(CLAUDE_CODE_SYSTEM_PREFIX):
            return system
        return f"{CLAUDE_CODE_SYSTEM_PREFIX}\n\n{system}" if system else CLAUDE_CODE_SYSTEM_PREFIX
    if isinstance(system, list):
        first = system[0] if system else None
        first_text = first.get("text", "") if isinstance(first, dict) else ""
        if first_text.lstrip().startswith(CLAUDE_CODE_SYSTEM_PREFIX):
            return system
        return [{"type": "text", "text": CLAUDE_CODE_SYSTEM_PREFIX}, *system]
    # NOT_GIVEN / None — no system prompt at all: the identity line IS the system.
    return CLAUDE_CODE_SYSTEM_PREFIX


try:
    from anthropic import AsyncAnthropic

    from pipecat.adapters.services.anthropic_adapter import AnthropicLLMAdapter
    from pipecat.services.anthropic.llm import AnthropicLLMService

    _IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover — surfaced with an actionable message below
    AsyncAnthropic = None  # type: ignore[assignment]
    AnthropicLLMAdapter = object  # type: ignore[assignment]
    AnthropicLLMService = None  # type: ignore[assignment]
    _IMPORT_ERROR = exc


if AnthropicLLMService is not None:

    class _OAuthAnthropicAdapter(AnthropicLLMAdapter):
        """Anthropic adapter that injects the OAuth identity line + tool-loop guard.

        This is the single seam both ``_process_context`` (streaming turns) and
        ``run_inference`` (out-of-band summarization) pass through, so every
        request carries the identity prefix.
        """

        def get_llm_invocation_params(self, context, *args, **kwargs):
            params = super().get_llm_invocation_params(context, *args, **kwargs)
            params["system"] = _with_identity_prefix(params.get("system"))

            # Tool-loop guard (agent/tool_loop.py): detection runs on the
            # UNIVERSAL (OpenAI-shaped) context messages — the Anthropic-shaped
            # request messages are opaque to the scanner — then the brake is
            # translated to this API's native controls.
            guard_in = {
                "messages": self.get_messages(context),
                "tools": params.get("tools") or None,
            }
            guard_out = apply_tool_loop_guard(guard_in)
            if guard_out is not guard_in:
                note = guard_out["messages"][-1]["content"]
                params["messages"] = [
                    *params["messages"],
                    {"role": "user", "content": note},
                ]
                if guard_out.get("tool_choice") == "none":
                    params["tool_choice"] = {"type": "none"}
            return params

    class AnthropicOAuthLLMService(AnthropicLLMService):
        """``AnthropicLLMService`` authenticated with a Claude-subscription Bearer token."""

        adapter_class = _OAuthAnthropicAdapter

        def __init__(self, *, settings) -> None:
            # Signed-out boot is deliberate (mirrors protoAgent #2475): a
            # missing/disconnected credential must not kill voice-session
            # setup. The per-request _refresh_auth raises instead, which the
            # service maps to an ErrorFrame the LLM error announcer speaks —
            # the orb stays alive and tells the user to sign in.
            try:
                token = resolve_anthropic_oauth_cached().access_token
            except OAuthCredentialError as e:
                logger.warning(f"[anthropic-oauth] booting signed-out: {e}")
                token = _SIGNED_OUT_SENTINEL
            client = AsyncAnthropic(
                api_key=None,  # x-api-key must never be sent — Bearer only
                auth_token=token,
                default_headers=oauth_default_headers(),
            )
            # `betas=` on a request REPLACES the anthropic-beta default header.
            # run_inference sets `betas` inline then applies `settings.extra` on
            # top, so carrying the merged list in extra keeps the OAuth betas on
            # that path; _create_message_stream below covers the streaming path
            # (where the service's inline `betas` update runs AFTER extra).
            merged_extra = dict(getattr(settings, "extra", None) or {})
            merged_extra["betas"] = ["interleaved-thinking-2025-05-14", *OAUTH_BETAS]
            settings.extra = merged_extra
            super().__init__(api_key="oauth-via-auth-token", client=client, settings=settings)
            self._client.api_key = None  # the positional sentinel must not become x-api-key

        async def _refresh_auth(self) -> None:
            """Re-resolve the access token before each request (TTL-cached; an
            expiring token refreshes over HTTP in a worker thread so the event
            loop never blocks). With a real token already in hand, a resolve
            failure keeps it rather than killing a live turn — this runs before
            every request, so a transient store/Keychain hiccup must not take
            the orb down (protoAgent #2604). Signed-out, the raise IS the UX:
            ErrorFrame → the announcer says to sign in."""
            try:
                creds = await asyncio.to_thread(resolve_anthropic_oauth_cached)
            except OAuthCredentialError:
                if self._client.auth_token not in (None, "", _SIGNED_OUT_SENTINEL):
                    logger.warning(
                        "[anthropic-oauth] could not re-resolve the access token — "
                        "keeping the one in hand"
                    )
                    return
                raise
            self._client.auth_token = creds.access_token

        async def _create_message_stream(self, api_call, params):
            await self._refresh_auth()
            betas = [b for b in (params.get("betas") or []) if b not in OAUTH_BETAS]
            params["betas"] = [*betas, *OAUTH_BETAS]
            return await super()._create_message_stream(api_call, params)

        async def run_inference(self, *args, **kwargs):
            await self._refresh_auth()
            return await super().run_inference(*args, **kwargs)


def build_anthropic_oauth_llm(*, model: str, settings) -> Any:
    """Build the Bearer-authenticated service for ``provider: anthropic-oauth``.

    ``settings`` is the ``OpenAILLMService.Settings`` the pipeline already
    assembled — translated here so the factory call-site stays uniform.
    Boots signed-out (the first turn announces the sign-in hint); raises a
    clear ``RuntimeError`` when the model id is a gateway alias or the
    ``anthropic`` extra isn't installed.
    """
    if AnthropicLLMService is None:
        raise RuntimeError(
            "llm.provider is 'anthropic-oauth' but the anthropic SDK is not "
            f"importable ({_IMPORT_ERROR}). Install it: pip install 'pipecat-ai[anthropic]'."
        )
    name = (model or "").strip()
    if not name or "/" in name:
        # A gateway alias like "protolabs/smart" is meaningless to Anthropic.
        raise RuntimeError(
            f"llm.provider is 'anthropic-oauth' but llm.model={name!r} is not a "
            "Claude model id (e.g. 'claude-sonnet-4-5', 'claude-opus-4-1')."
        )
    max_tokens = getattr(settings, "max_tokens", None)
    svc_settings = AnthropicOAuthLLMService.Settings(
        model=name,
        max_tokens=int(max_tokens) if isinstance(max_tokens, (int, float)) else 4096,
        # NOTE: temperature is deliberately NOT forwarded — current Claude
        # models reject it ("`temperature` is deprecated for this model") and
        # it isn't a knob worth breaking every turn over.
    )
    logger.info(f"[llm-factory] using anthropic-oauth adapter model={name}")
    return AnthropicOAuthLLMService(settings=svc_settings)
