"""Per-delegate-type adapters — the single extension point for delegate types.

Adding a delegate type = implement a ``DelegateAdapter`` and ``register_adapter``
it. Everything generic routes through ``get_adapter(type)``:

  - the runtime registry parse (``agent.delegates._parse_entry``)
  - dispatch + reachability probe (``agent.delegates.dispatch`` / ``probe``)
  - health-cache invalidation (``agent.delegates._config_changed``)
  - the API write-validation (``agent.delegate_config_store.validate_entry``)
  - the Settings New/Edit form (rendered from ``config_schema()`` via
    ``GET /api/delegate-types``)
  - (later) the delegation monitor widget + operator co-drive, gated on
    ``capabilities`` + ``stream()`` / ``inject()``

so a new type lights up in all of them at once.

This module owns everything **type-specific** (parse, validate, dispatch, probe,
the client/session caches, the field schema, the capability flags). ``agent/
delegates.py`` keeps the type-AGNOSTIC machinery (the ``Delegate`` record, the
registry, health scheduling). Import direction is one-way — adapters → delegates
(for the ``Delegate`` record, ``DelegateError``, ``_expand_env``); ``delegates``
imports this module lazily inside its facades to avoid a cycle.

The per-type bodies here were moved **verbatim** out of ``delegates.py`` /
``delegate_config_store.py`` — this is a refactor, not a behavior change.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from a2a_outbound import A2AClient, A2ADispatchError, ProgressCallback
from acp import AcpClient, AcpError
from agent.delegates import Delegate, DelegateError, _expand_env
from agent.tracing import active_trace, propagation_headers

logger = logging.getLogger(__name__)


class DelegateValidationError(ValueError):
    """Raised when a delegate entry fails schema validation. The API path maps
    this to HTTP 400. Defined here (with the adapters that raise it) and
    re-exported from ``delegate_config_store`` for backward-compatible imports."""


# Top-level YAML keys common to every delegate type. Per-type extras are added
# by each adapter's ``allowed_keys``; anything outside the union is dropped on
# write with a log line (keeps the UI from injecting fields the parser ignores).
_COMMON_KEYS = {"name", "type", "description", "url"}


@dataclass
class FieldSpec:
    """One field in a delegate type's config form. Drives the generic Settings
    New/Edit form (Stage 1b) and the ``/api/delegate-types`` contract. Dotted
    ``key`` (e.g. ``auth.scheme``) maps to a nested YAML path."""
    key: str
    label: str
    kind: str  # text | secret-env | args | path | number | textarea | select
    required: bool = False
    placeholder: str = ""
    help: str = ""
    options: list[str] | None = None  # for kind="select"

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "key": self.key,
            "label": self.label,
            "kind": self.kind,
            "required": self.required,
        }
        if self.placeholder:
            d["placeholder"] = self.placeholder
        if self.help:
            d["help"] = self.help
        if self.options is not None:
            d["options"] = self.options
        return d


@dataclass(frozen=True)
class Capabilities:
    """What a delegate type supports at runtime. Read by the frontend (via
    ``/api/delegate-types``) to decide what a monitor widget can render — a
    stream pane iff ``stream``, an operator inject box iff ``inject``, plain
    request/response iff ``oneshot``."""
    stream: bool = False   # emits intermediate events worth rendering
    inject: bool = False   # accepts operator co-drive mid-session
    session: bool = False  # long-lived (sticky context/session) vs one-shot
    oneshot: bool = True   # request/response only

    def as_dict(self) -> dict[str, bool]:
        return {
            "stream": self.stream,
            "inject": self.inject,
            "session": self.session,
            "oneshot": self.oneshot,
        }


class DelegateAdapter:
    """Base class. A delegate type implements the hooks below and registers an
    instance. Defaults cover the no-op cases (one-shot types that can't stream
    or be co-driven) so a minimal adapter only needs parse/validate/dispatch/
    probe/config_schema."""

    type: str = ""
    capabilities: Capabilities = Capabilities()
    #: YAML keys this type accepts beyond ``_COMMON_KEYS``.
    extra_keys: frozenset[str] = frozenset()

    @property
    def allowed_keys(self) -> set[str]:
        return _COMMON_KEYS | set(self.extra_keys)

    # --- config: schema (UI/contract) + runtime parse + API validate ---------
    def config_schema(self) -> list[FieldSpec]:
        raise NotImplementedError

    def parse(self, raw: dict, name: str, description: str) -> Delegate | None:
        """Build a ``Delegate`` from a raw YAML entry, or ``None`` to skip it
        (the runtime registry silently drops bad entries). ``name`` /
        ``description`` are pre-validated by the caller."""
        raise NotImplementedError

    def validate(self, entry: dict, name: str, description: str) -> dict:
        """Return a normalized dict for the API write path, raising
        ``DelegateValidationError`` (loud) on bad input. ``name`` /
        ``description`` are pre-validated by the caller."""
        raise NotImplementedError

    def config_changed(self, a: Delegate, b: Delegate) -> bool:
        """Did dispatch-relevant config change (→ invalidate the health cache)?
        Default compares ``url``; override for auth/model/etc."""
        return a.url != b.url

    # --- dispatch + reachability --------------------------------------------
    async def dispatch(
        self,
        delegate: Delegate,
        query: str,
        *,
        timeout: float = 60.0,
        progress_callback: ProgressCallback | None = None,
        push_notification_url: str | None = None,
        push_notification_token: str | None = None,
    ) -> str:
        raise NotImplementedError

    async def probe(self, delegate: Delegate, *, timeout: float = 8.0) -> dict:
        raise NotImplementedError

    # --- live session: operator co-drive + event stream (Stage 4/5) ----------
    async def inject(self, delegate: Delegate, operator_text: str) -> None:
        """Push an operator message into this delegate's live session (co-drive).
        Default: unsupported (one-shot types)."""
        raise DelegateError(f"{self.type}: operator co-drive not supported")


# ---------------------------------------------------------------------------
# A2A — JSON-RPC fleet peer
# ---------------------------------------------------------------------------


class A2AAdapter(DelegateAdapter):
    type = "a2a"
    capabilities = Capabilities(stream=True, inject=True, session=True, oneshot=False)
    extra_keys = frozenset({"auth", "headers"})

    def __init__(self) -> None:
        # Per-delegate A2AClient cache — reused across dispatches so the Agent
        # Card is fetched once. Keyed by (url, sorted auth headers) so an
        # auth/url change yields a fresh client.
        self._clients: dict[tuple, A2AClient] = {}

    def config_schema(self) -> list[FieldSpec]:
        return [
            FieldSpec("url", "URL", "text", required=True,
                      placeholder="http://ava:3008/a2a",
                      help="JSON-RPC endpoint, typically ending in /a2a. "
                           "Supports ${VAR:-default}."),
            FieldSpec("auth.scheme", "Auth scheme", "select",
                      options=["", "apiKey", "bearer"],
                      help="How the delegate expects credentials, if any."),
            FieldSpec("auth.credentialsEnv", "Credentials env var", "secret-env",
                      placeholder="AVA_API_KEY",
                      help="Name of the env var holding the secret. "
                           "Set it in .env on the host."),
        ]

    def parse(self, raw: dict, name: str, description: str) -> Delegate | None:
        url = _expand_env(str(raw.get("url", "")))
        if not url:
            logger.warning(f"[delegates] {name}: url required; skipping")
            return None
        auth = raw.get("auth") or {}
        scheme = auth.get("scheme")
        cred_env = auth.get("credentialsEnv")
        cred = os.environ.get(cred_env) if cred_env else None
        if cred_env and not cred:
            logger.warning(
                f"[delegates] {name}: auth env {cred_env!r} unset (unauthenticated)"
            )
        return Delegate(
            name=name, description=description, type="a2a", url=url,
            auth_scheme=scheme,
            a2a_credential=cred,
            a2a_headers=dict(raw.get("headers", {})),
        )

    def validate(self, entry: dict, name: str, description: str) -> dict:
        url = entry.get("url")
        if not isinstance(url, str) or not url.strip():
            raise DelegateValidationError("`url` is required")
        out: dict[str, Any] = {
            "name": name, "type": "a2a",
            "description": description, "url": url.strip(),
        }
        auth = entry.get("auth")
        if auth is not None:
            if not isinstance(auth, dict):
                raise DelegateValidationError("`auth` must be an object when present")
            scheme = auth.get("scheme")
            if scheme is not None and scheme not in ("apiKey", "bearer"):
                raise DelegateValidationError(
                    f"`auth.scheme` must be 'apiKey' or 'bearer', got {scheme!r}"
                )
            cred_env = auth.get("credentialsEnv")
            if cred_env is not None and not isinstance(cred_env, str):
                raise DelegateValidationError(
                    "`auth.credentialsEnv` must be a string env-var name"
                )
            out["auth"] = {
                k: v for k, v in {"scheme": scheme, "credentialsEnv": cred_env}.items()
                if v is not None
            }
        headers = entry.get("headers")
        if headers is not None:
            if not isinstance(headers, dict):
                raise DelegateValidationError("`headers` must be an object")
            out["headers"] = {str(k): str(v) for k, v in headers.items()}
        for k in entry:
            if k not in self.allowed_keys:
                logger.warning(
                    f"[delegate_config_store] {name}: dropping unknown a2a key {k!r}"
                )
        return out

    def config_changed(self, a: Delegate, b: Delegate) -> bool:
        return (
            a.url != b.url
            or a.auth_scheme != b.auth_scheme
            or a.a2a_credential != b.a2a_credential
            or a.a2a_headers != b.a2a_headers
        )

    def client_for(self, delegate: Delegate) -> A2AClient:
        key = (delegate.url, tuple(sorted(delegate.auth_headers().items())))
        client = self._clients.get(key)
        if client is None:
            client = A2AClient(
                delegate.url,
                headers=delegate.auth_headers(),
                card_origin=delegate.origin(),
                name=delegate.name,
            )
            self._clients[key] = client
        return client

    async def dispatch(
        self, delegate: Delegate, query: str, *, timeout: float = 60.0,
        progress_callback: ProgressCallback | None = None,
        push_notification_url: str | None = None,
        push_notification_token: str | None = None,
    ) -> str:
        """Reliable synchronous ``message/send`` by default; opt into streaming
        (progress narration) with ``A2A_STREAM=1`` or a ``progress_callback`` +
        a card that advertises ``streaming``. Any stream error/timeout falls
        back to one synchronous ``message/send``. (Moved verbatim from the old
        ``_dispatch_a2a``; see that history for the hang-guard rationale.)"""
        client = self.client_for(delegate)
        ctx = uuid.uuid4().hex  # fresh per dispatch — one-shot, no multi-turn
        env_stream = os.environ.get("A2A_STREAM", "0") == "1"
        want_status = progress_callback is not None and await client.supports_streaming()
        if env_stream or want_status:
            bound = (
                float(os.environ.get("A2A_STREAM_TIMEOUT", "20")) if env_stream
                else timeout
            )
            try:
                res = await asyncio.wait_for(
                    client.send(
                        query,
                        context_id=ctx,
                        progress_callback=progress_callback,
                        prefer_stream=True,
                        timeout=timeout,
                        push_notification_url=push_notification_url,
                        push_notification_token=push_notification_token,
                    ),
                    timeout=bound,
                )
                return res.text
            except (A2ADispatchError, asyncio.TimeoutError, httpx.HTTPError) as e:
                logger.warning(
                    f"[delegates] {delegate.name} streaming failed/timed out ({e}); "
                    "falling back to message/send"
                )
        res = await client.send(
            query, context_id=ctx, prefer_stream=False, timeout=timeout
        )
        return res.text

    async def probe(self, delegate: Delegate, *, timeout: float = 8.0) -> dict:
        origin = delegate.origin()
        if not origin:
            return {"ok": False, "error": f"malformed url: {delegate.url!r}"}
        card_url = f"{origin}/.well-known/agent-card.json"
        headers = delegate.auth_headers()
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.get(card_url, headers=headers)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
            return {"ok": False, "error": f"unreachable: {e}"}
        except Exception as e:  # noqa: BLE001 — surface but don't raise
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        latency_ms = int((time.monotonic() - t0) * 1000)
        if r.status_code in (401, 403):
            return {
                "ok": False, "error": "auth rejected",
                "status": r.status_code, "latency_ms": latency_ms,
            }
        if r.status_code != 200:
            return {
                "ok": False,
                "error": f"agent card HTTP {r.status_code}",
                "status": r.status_code, "latency_ms": latency_ms,
            }
        return {"ok": True, "latency_ms": latency_ms}


# ---------------------------------------------------------------------------
# OpenAI-compatible /v1 endpoint
# ---------------------------------------------------------------------------


class OpenAIAdapter(DelegateAdapter):
    type = "openai"
    capabilities = Capabilities(stream=False, inject=False, session=False, oneshot=True)
    extra_keys = frozenset(
        {"model", "api_key_env", "system_prompt", "max_tokens", "temperature"}
    )

    def config_schema(self) -> list[FieldSpec]:
        return [
            FieldSpec("url", "URL", "text", required=True,
                      placeholder="http://gateway:4000/v1",
                      help="OpenAI-compat base URL ending in /v1."),
            FieldSpec("model", "Model", "text", required=True,
                      placeholder="claude-opus-4-6",
                      help="Provider-specific model id."),
            FieldSpec("api_key_env", "API key env var", "secret-env",
                      placeholder="LITELLM_MASTER_KEY",
                      help="Optional. Leave empty for endpoints that don't need auth."),
            FieldSpec("system_prompt", "System prompt", "textarea",
                      placeholder="Answer thoroughly but concisely (2-4 sentences).",
                      help="Optional. Overrides the default spoken-aloud framing."),
        ]

    def parse(self, raw: dict, name: str, description: str) -> Delegate | None:
        url = _expand_env(str(raw.get("url", "")))
        if not url:
            logger.warning(f"[delegates] {name}: url required; skipping")
            return None
        model = raw.get("model")
        if not model:
            logger.warning(f"[delegates] {name}: openai delegate requires model; skipping")
            return None
        key_env = raw.get("api_key_env")
        api_key = os.environ.get(key_env) if key_env else None
        if key_env and not api_key:
            logger.warning(
                f"[delegates] {name}: api_key_env {key_env!r} unset (sending unauthenticated)"
            )
        return Delegate(
            name=name, description=description, type="openai", url=url,
            model=model,
            openai_api_key=api_key or "not-needed",
            system_prompt=raw.get("system_prompt"),
            max_tokens=int(raw.get("max_tokens", 400)),
            temperature=float(raw.get("temperature", 0.4)),
        )

    def validate(self, entry: dict, name: str, description: str) -> dict:
        url = entry.get("url")
        if not isinstance(url, str) or not url.strip():
            raise DelegateValidationError("`url` is required")
        out: dict[str, Any] = {
            "name": name, "type": "openai",
            "description": description, "url": url.strip(),
        }
        model = entry.get("model")
        if not isinstance(model, str) or not model.strip():
            raise DelegateValidationError("`model` is required for openai delegates")
        out["model"] = model.strip()

        api_key_env = entry.get("api_key_env")
        if api_key_env is not None:
            if not isinstance(api_key_env, str):
                raise DelegateValidationError("`api_key_env` must be a string env-var name")
            out["api_key_env"] = api_key_env

        system_prompt = entry.get("system_prompt")
        if system_prompt is not None:
            if not isinstance(system_prompt, str):
                raise DelegateValidationError("`system_prompt` must be a string")
            out["system_prompt"] = system_prompt

        max_tokens = entry.get("max_tokens")
        if max_tokens is not None:
            try:
                out["max_tokens"] = int(max_tokens)
            except (TypeError, ValueError) as e:
                raise DelegateValidationError(f"`max_tokens` must be an int: {e}") from e
            if out["max_tokens"] <= 0:
                raise DelegateValidationError("`max_tokens` must be > 0")

        temperature = entry.get("temperature")
        if temperature is not None:
            try:
                out["temperature"] = float(temperature)
            except (TypeError, ValueError) as e:
                raise DelegateValidationError(f"`temperature` must be a number: {e}") from e

        for k in entry:
            if k not in self.allowed_keys:
                logger.warning(
                    f"[delegate_config_store] {name}: dropping unknown openai key {k!r}"
                )
        return out

    def config_changed(self, a: Delegate, b: Delegate) -> bool:
        return a.url != b.url or a.model != b.model or a.openai_api_key != b.openai_api_key

    async def dispatch(
        self, delegate: Delegate, query: str, *, timeout: float = 60.0,
        progress_callback: ProgressCallback | None = None,
        push_notification_url: str | None = None,
        push_notification_token: str | None = None,
    ) -> str:
        """One-shot non-streaming chat completion via plain httpx. (Moved
        verbatim from ``_dispatch_openai``; we avoid the OpenAI SDK because its
        fingerprint headers trip some proxies' WAFs.)"""
        sys_prompt = delegate.system_prompt or (
            "You are a research assistant. Answer thoroughly but concisely "
            "(2-4 sentences). Plain text only — no markdown, no lists. "
            "The answer will be spoken aloud verbatim."
        )
        headers = {"Content-Type": "application/json"}
        headers.update(propagation_headers(trace=active_trace()))
        if delegate.openai_api_key and delegate.openai_api_key != "not-needed":
            headers["Authorization"] = f"Bearer {delegate.openai_api_key}"
        payload = {
            "model": delegate.model,
            "messages": [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": query},
            ],
            "max_tokens": delegate.max_tokens,
            "temperature": delegate.temperature,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(
                    f"{delegate.url.rstrip('/')}/chat/completions",
                    headers=headers,
                    json=payload,
                )
        except (httpx.ConnectError, httpx.ConnectTimeout, OSError) as e:
            raise DelegateError(f"{delegate.name} unreachable: {e}") from e
        except Exception as e:
            raise DelegateError(f"{delegate.name}: {e}") from e

        if r.status_code != 200:
            raise DelegateError(
                f"{delegate.name}: HTTP {r.status_code} — {r.text[:200]}"
            )
        try:
            body = r.json()
            return (body["choices"][0]["message"]["content"] or "").strip()
        except Exception as e:
            raise DelegateError(f"{delegate.name}: malformed response ({e})") from e

    async def probe(self, delegate: Delegate, *, timeout: float = 8.0) -> dict:
        from agent.llm_probe import ping_endpoint
        return await ping_endpoint(
            url=delegate.url,
            model=delegate.model or "",
            api_key=delegate.openai_api_key
            if delegate.openai_api_key and delegate.openai_api_key != "not-needed"
            else "",
        )


# ---------------------------------------------------------------------------
# ACP — local coding agent ORBIS launches + drives over stdio
# ---------------------------------------------------------------------------


class AcpAdapter(DelegateAdapter):
    type = "acp"
    capabilities = Capabilities(stream=True, inject=True, session=True, oneshot=False)
    extra_keys = frozenset({"command", "args", "workdir"})

    def __init__(self) -> None:
        # One launched agent process + session per delegate, reused across turns
        # so follow-ups continue the thread (the sticky-session analog of the A2A
        # contextId). Keyed by the launch identity.
        self._clients: dict[tuple, AcpClient] = {}

    def config_schema(self) -> list[FieldSpec]:
        return [
            FieldSpec("command", "Command", "text", required=True, placeholder="proto",
                      help="The agent binary on your PATH."),
            FieldSpec("args", "Args", "args", placeholder="--acp",
                      help="Args that start it in ACP mode "
                           "(proto: --acp · opencode: acp)."),
            FieldSpec("workdir", "Workdir", "path", required=True, placeholder="~/dev/ORBIS",
                      help="The directory the agent reads, edits, and runs code in."),
        ]

    def parse(self, raw: dict, name: str, description: str) -> Delegate | None:
        command = _expand_env(str(raw.get("command", "")))
        if not command:
            logger.warning(f"[delegates] {name}: acp delegate requires command; skipping")
            return None
        workdir = _expand_env(str(raw.get("workdir", "")))
        if not workdir:
            logger.warning(f"[delegates] {name}: acp delegate requires workdir; skipping")
            return None
        raw_args = raw.get("args", [])
        args = (
            [_expand_env(str(a)) for a in raw_args] if isinstance(raw_args, list) else []
        )
        return Delegate(
            name=name, description=description, type="acp",
            command=command, args=args, workdir=workdir,
        )

    def validate(self, entry: dict, name: str, description: str) -> dict:
        command = entry.get("command")
        if not isinstance(command, str) or not command.strip():
            raise DelegateValidationError(
                "`command` is required for acp delegates (the agent binary, e.g. 'proto')"
            )
        workdir = entry.get("workdir")
        if not isinstance(workdir, str) or not workdir.strip():
            raise DelegateValidationError(
                "`workdir` is required for acp delegates (the directory the agent works in)"
            )
        raw_args = entry.get("args") or []
        if not isinstance(raw_args, list):
            raise DelegateValidationError("`args` must be a list of strings")
        out: dict[str, Any] = {
            "name": name, "type": "acp", "description": description,
            "command": command.strip(),
            "args": [str(a) for a in raw_args],
            "workdir": workdir.strip(),
        }
        for k in entry:
            if k not in self.allowed_keys:
                logger.warning(
                    f"[delegate_config_store] {name}: dropping unknown acp key {k!r}"
                )
        return out

    def client_for(self, delegate: Delegate) -> AcpClient:
        key = (delegate.name, delegate.command, tuple(delegate.args), delegate.workdir)
        client = self._clients.get(key)
        if client is None:
            client = AcpClient(
                delegate.command, delegate.args,
                cwd=delegate.workdir, name=delegate.name,
            )
            self._clients[key] = client
        return client

    async def dispatch(
        self, delegate: Delegate, query: str, *, timeout: float = 60.0,
        progress_callback: ProgressCallback | None = None,
        push_notification_url: str | None = None,
        push_notification_token: str | None = None,
    ) -> str:
        """Drive a launched ACP coding agent for one turn. Coding turns are slow,
        so the floor is generous regardless of the caller's default timeout."""
        client = self.client_for(delegate)
        try:
            text = await client.prompt(
                query,
                progress_callback=progress_callback,
                timeout=max(timeout, 600.0),
            )
        except AcpError as exc:
            raise DelegateError(f"{delegate.name}: {exc}") from exc
        if not text:
            raise DelegateError(f"{delegate.name} finished but returned no text")
        return text

    async def probe(self, delegate: Delegate, *, timeout: float = 8.0) -> dict:
        # No URL to ping — an acp agent is "reachable" if its binary is on PATH
        # and its workdir exists (ORBIS launches it on demand).
        import shutil
        from pathlib import Path

        cmd = (delegate.command or "").strip()
        if not cmd:
            return {"ok": False, "error": "no command configured"}
        if shutil.which(cmd) is None:
            return {"ok": False, "error": f"command not on PATH: {cmd}"}
        wd = Path((delegate.workdir or "").strip()).expanduser()
        if not wd.is_dir():
            return {"ok": False, "error": f"workdir not found: {wd}"}
        return {"ok": True}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

ADAPTERS: dict[str, DelegateAdapter] = {}


def register_adapter(adapter: DelegateAdapter) -> None:
    """Register a delegate type. Idempotent — re-registering a type replaces it
    (so a plugin reload doesn't accumulate stale adapters)."""
    ADAPTERS[adapter.type] = adapter


def get_adapter(dtype: str) -> DelegateAdapter:
    """Return the adapter for ``dtype``. Raises ``KeyError`` for unknown types —
    callers map that to a user-facing 'unknown delegate type' message."""
    return ADAPTERS[(dtype or "").lower()]


def all_adapter_types() -> list[str]:
    return list(ADAPTERS.keys())


def delegate_type_specs() -> list[dict[str, Any]]:
    """The ``/api/delegate-types`` contract: every registered type with its
    capabilities + field schema, for the generic Settings form + monitor."""
    return [
        {
            "type": a.type,
            "capabilities": a.capabilities.as_dict(),
            "fields": [f.as_dict() for f in a.config_schema()],
        }
        for a in ADAPTERS.values()
    ]


# Built-in types. A new type adds one line here (or registers from a plugin).
register_adapter(A2AAdapter())
register_adapter(OpenAIAdapter())
register_adapter(AcpAdapter())
