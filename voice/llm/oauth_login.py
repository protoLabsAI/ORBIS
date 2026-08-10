"""In-app OAuth *sign-in* flows for the native subscription providers.

Lets a user authenticate a subscription from the ORBIS wizard/settings — no
terminal — for both providers (ported from protoAgent ADR 0097). Two shapes:

- **openai-codex** — OpenAI's device-code flow: start yields a short user-code +
  a URL to enter it at; the UI polls until the user approves, then we exchange
  for tokens.
- **anthropic-oauth** — Claude Code's PKCE flow with a displayed code: start
  yields an authorize URL; the user approves in the browser, Anthropic shows a
  ``code#state``, they paste it back, and we exchange for tokens.

NOTE (ToS): the Claude flow authenticates with Claude Code's own public OAuth
client id — i.e. ORBIS performs the login *as* Claude Code. That's a step beyond
reading the CLI's existing credentials; it's opt-in and the operator's call.

Flow state (the device id / PKCE verifier) is held in a short-lived in-process
store keyed by an opaque ``flow_id``; nothing is persisted until tokens are
minted, then they go to the same stores the resolvers read.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx

from voice.llm import oauth as _oauth

logger = logging.getLogger(__name__)


class OAuthLoginError(RuntimeError):
    def __init__(self, message: str, *, provider: str) -> None:
        super().__init__(message)
        self.provider = provider


# ── flow store (in-process, short-lived) ──────────────────────────────────────

_FLOW_TTL_S = 15 * 60  # a login the user never completes is swept after 15 min


@dataclass
class _Flow:
    provider: str
    created_at: float
    data: dict[str, Any] = field(default_factory=dict)


_FLOWS: dict[str, _Flow] = {}


def _new_flow(provider: str, data: dict[str, Any]) -> str:
    # opportunistic sweep of expired flows (login is rare; no background task needed)
    cutoff = _now() - _FLOW_TTL_S
    for fid in [k for k, v in _FLOWS.items() if v.created_at < cutoff]:
        _FLOWS.pop(fid, None)
    flow_id = secrets.token_urlsafe(18)
    _FLOWS[flow_id] = _Flow(provider=provider, created_at=_now(), data=data)
    return flow_id


def _get_flow(flow_id: str, provider: str) -> _Flow:
    flow = _FLOWS.get(flow_id)
    if flow is None or flow.provider != provider:
        raise OAuthLoginError("Sign-in session expired — start again.", provider=provider)
    return flow


def _now() -> float:
    # Wrapped so the flow TTL is testable without patching time everywhere.
    return time.time()


# ── openai-codex: device-code flow ────────────────────────────────────────────

_OPENAI_ISSUER = "https://auth.openai.com"


def codex_login_start() -> dict[str, Any]:
    """Request a device code; return what the UI shows the user + a flow_id to poll."""
    try:
        resp = httpx.post(
            f"{_OPENAI_ISSUER}/api/accounts/deviceauth/usercode",
            json={"client_id": _oauth._CODEX_CLIENT_ID},
            headers={"Content-Type": "application/json"},
            timeout=httpx.Timeout(15.0),
        )
    except httpx.HTTPError as exc:
        raise OAuthLoginError(f"Could not reach OpenAI: {exc}", provider="openai-codex") from exc
    if resp.status_code == 429:
        raise OAuthLoginError(
            "OpenAI is rate-limiting sign-in (429) — wait a minute and try again.",
            provider="openai-codex",
        )
    if resp.status_code != 200:
        raise OAuthLoginError(
            f"Device-code request failed (HTTP {resp.status_code}).", provider="openai-codex"
        )
    d = resp.json()
    user_code = str(d.get("user_code", "") or "")
    device_auth_id = str(d.get("device_auth_id", "") or "")
    if not user_code or not device_auth_id:
        raise OAuthLoginError("OpenAI returned an incomplete device code.", provider="openai-codex")
    flow_id = _new_flow("openai-codex", {"device_auth_id": device_auth_id, "user_code": user_code})
    return {
        "flow_id": flow_id,
        "mode": "device",
        "user_code": user_code,
        "verification_uri": f"{_OPENAI_ISSUER}/codex/device",
        "interval": max(3, int(d.get("interval", 5) or 5)),
    }


def codex_login_poll(flow_id: str) -> dict[str, str]:
    """One poll tick. Returns ``{status: pending|complete|error, error?}``.

    On the first success this exchanges the authorization code for tokens and
    writes the Codex store, so ``resolve_codex_oauth`` sees the sign-in."""
    flow = _get_flow(flow_id, "openai-codex")
    try:
        resp = httpx.post(
            f"{_OPENAI_ISSUER}/api/accounts/deviceauth/token",
            json={"device_auth_id": flow.data["device_auth_id"], "user_code": flow.data["user_code"]},
            headers={"Content-Type": "application/json"},
            timeout=httpx.Timeout(15.0),
        )
    except httpx.HTTPError as exc:
        return {"status": "error", "error": f"Poll failed: {exc}"}
    if resp.status_code in {403, 404}:
        return {"status": "pending"}
    if resp.status_code != 200:
        return {"status": "error", "error": f"Sign-in polling failed (HTTP {resp.status_code})."}

    code_resp = resp.json()
    authorization_code = str(code_resp.get("authorization_code", "") or "")
    code_verifier = str(code_resp.get("code_verifier", "") or "")
    if not authorization_code or not code_verifier:
        return {"status": "error", "error": "OpenAI approved the code but returned no authorization code."}
    try:
        tokens = _exchange_codex_code(authorization_code, code_verifier)
    except OAuthLoginError as exc:
        return {"status": "error", "error": str(exc)}
    store = _oauth._codex_store_path()
    # Same lock disconnect() holds for its delete-store→write-marker pair: an
    # unserialized sign-in write could land INSIDE that pair, after which both
    # "who finished last" signals (store, marker) exist at once. Serialized,
    # either order leaves storage self-consistent.
    with _oauth._store_lock(store):
        # ORBIS minted this login itself — disconnect may revoke it.
        _oauth._write_codex_store(store, tokens, provenance=_oauth.PROVENANCE_DEVICE_LOGIN)
        _oauth.clear_disconnected("openai-codex")  # explicit reconnect clears a prior disconnect
    _FLOWS.pop(flow_id, None)
    return {"status": "complete"}


def _exchange_codex_code(code: str, code_verifier: str) -> dict[str, Any]:
    resp = httpx.post(
        _oauth._CODEX_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": f"{_OPENAI_ISSUER}/deviceauth/callback",
            "client_id": _oauth._CODEX_CLIENT_ID,
            "code_verifier": code_verifier,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=httpx.Timeout(15.0),
    )
    if resp.status_code != 200:
        raise OAuthLoginError(f"Token exchange failed (HTTP {resp.status_code}).", provider="openai-codex")
    tokens = resp.json()
    if not str(tokens.get("access_token", "") or ""):
        raise OAuthLoginError("Token exchange returned no access_token.", provider="openai-codex")
    return {
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token", ""),
        "id_token": tokens.get("id_token", ""),
    }


# ── anthropic-oauth: PKCE flow with a displayed code ──────────────────────────

_ANTHROPIC_AUTHORIZE = "https://platform.claude.com/oauth/authorize"
_ANTHROPIC_REDIRECT = "https://platform.claude.com/oauth/code/callback"
# Claude Code's own public OAuth client (see the ToS note in the module docstring).
_ANTHROPIC_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
_ANTHROPIC_SCOPES = "org:create_api_key user:profile user:inference"


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def anthropic_login_start() -> dict[str, Any]:
    """Return the authorize URL the UI opens + a flow_id to complete with the pasted code."""
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)
    params = {
        "code": "true",  # display the code for manual paste (no loopback needed)
        "client_id": _ANTHROPIC_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": _ANTHROPIC_REDIRECT,
        "scope": _ANTHROPIC_SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    flow_id = _new_flow("anthropic-oauth", {"code_verifier": verifier, "state": state})
    return {
        "flow_id": flow_id,
        "mode": "redirect",
        "authorize_url": f"{_ANTHROPIC_AUTHORIZE}?{urlencode(params)}",
    }


def anthropic_login_complete(flow_id: str, code: str) -> dict[str, str]:
    """Exchange the pasted ``code#state`` for tokens and persist them. Returns
    ``{status: complete|error, error?}``."""
    flow = _get_flow(flow_id, "anthropic-oauth")
    raw = (code or "").strip()
    if not raw:
        return {"status": "error", "error": "Paste the code Anthropic showed after you approved."}
    # Anthropic returns the code as `<code>#<state>`.
    auth_code, _, returned_state = raw.partition("#")
    if returned_state and returned_state != flow.data["state"]:
        return {"status": "error", "error": "Sign-in state mismatch — start again."}
    try:
        resp = httpx.post(
            _oauth._ANTHROPIC_TOKEN_URL,
            json={
                "grant_type": "authorization_code",
                "code": auth_code,
                "state": flow.data["state"],
                "redirect_uri": _ANTHROPIC_REDIRECT,
                "client_id": _ANTHROPIC_CLIENT_ID,
                "code_verifier": flow.data["code_verifier"],
            },
            headers={"Content-Type": "application/json"},
            timeout=httpx.Timeout(20.0),
        )
    except httpx.HTTPError as exc:
        return {"status": "error", "error": f"Token exchange could not reach Anthropic: {exc}"}
    if resp.status_code != 200:
        return {"status": "error", "error": f"Token exchange failed (HTTP {resp.status_code}) — the code may have expired."}
    tokens = resp.json()
    if not str(tokens.get("access_token", "") or ""):
        return {"status": "error", "error": "Token exchange returned no access_token."}
    # Serialized against disconnect()'s delete-store→write-marker pair — same
    # rationale as the codex path above.
    with _oauth._store_lock(_oauth._anthropic_store_path()):
        _oauth._write_anthropic_store(tokens)
        _oauth.clear_disconnected("anthropic-oauth")  # explicit reconnect clears a prior disconnect
    _FLOWS.pop(flow_id, None)
    return {"status": "complete"}


def cancel_login(flow_id: str) -> dict[str, bool]:
    """Abandon an in-progress sign-in: drop the server-side pending flow so its
    device/PKCE state can't be completed later. Idempotent — an unknown flow is
    a no-op."""
    existed = _FLOWS.pop(flow_id, None) is not None
    return {"ok": True, "cancelled": existed}


def login_start(provider: str) -> dict[str, Any]:
    provider = (provider or "").strip().lower()
    if provider == "openai-codex":
        return codex_login_start()
    if provider == "anthropic-oauth":
        return anthropic_login_start()
    raise OAuthLoginError(f"no sign-in flow for provider {provider!r}", provider=provider)
