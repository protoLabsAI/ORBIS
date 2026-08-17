"""Read-layer for the OAuth subscription providers — wizard/settings probes.

Powers "is the user signed in?", "what models can this subscription run?", and
the wizard's Test button — so nobody has to hand-edit YAML or guess a model id.
Status and model listing are read-only probes; nothing here refreshes or writes
a token except ``validate_oauth_connection`` (which resolves through the normal
path, so a stale token refreshes exactly like a real turn would).
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass

import httpx

from voice.llm import oauth as _oauth
from voice.llm.oauth import NATIVE_OAUTH_PROVIDERS

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OAuthStatus:
    provider: str
    signed_in: bool
    source: str  # where the credential came from ("" when not signed in)
    detail: str  # human context: plan, account, expiry — "" when unknown
    hint: str  # the exact sign-in step when not signed in ("" when signed in)

    def as_dict(self) -> dict:
        return asdict(self)


_SIGN_IN_HINTS = {
    "anthropic-oauth": "Sign in below, or with the Claude Code CLI (`claude`), or run "
    "`claude setup-token` and set CLAUDE_CODE_OAUTH_TOKEN.",
    "openai-codex": "Sign in below, or with the Codex CLI (`codex`) — ORBIS imports the "
    "credential once and keeps its own refreshed copy.",
}


def _anthropic_status() -> OAuthStatus:
    if os.environ.get(_oauth._CLAUDE_ENV_VAR, "").strip():
        return OAuthStatus("anthropic-oauth", True, "env", "CLAUDE_CODE_OAUTH_TOKEN", "")
    store = _oauth._read_anthropic_store()
    if store:
        exp = store.get("expires_at")
        detail = "Claude subscription (signed in here)"
        if isinstance(exp, (int, float)) and exp <= _oauth._now():
            detail += " (token will refresh on use)"
        return OAuthStatus("anthropic-oauth", True, "instance_store", detail, "")
    doc = _oauth._read_claude_credentials_file()
    oauth = (doc or {}).get("claudeAiOauth") if isinstance(doc, dict) else None
    if isinstance(oauth, dict) and str(oauth.get("accessToken", "") or "").strip():
        plan = str(oauth.get("subscriptionType", "") or "").strip()
        detail = f"{plan} plan" if plan else "Claude Code credentials"
        return OAuthStatus("anthropic-oauth", True, "credentials_file", detail, "")
    return OAuthStatus("anthropic-oauth", False, "", "", _SIGN_IN_HINTS["anthropic-oauth"])


def _codex_status() -> OAuthStatus:
    """Read-only: is a Codex token present (our store or the CLI file), unexpired?
    Never refreshes or writes — a status poll must be side-effect-free."""
    tokens = _oauth._read_codex_tokens(_oauth._codex_store_path())
    source = "instance_store"
    if tokens is None:
        tokens = _oauth._read_codex_tokens(_oauth._CODEX_CLI_AUTH_FILE)
        source = "codex_cli"
    if not tokens or not str(tokens.get("access_token", "") or "").strip():
        return OAuthStatus("openai-codex", False, "", "", _SIGN_IN_HINTS["openai-codex"])
    acct = _oauth._codex_account_id(tokens)
    expiring = _oauth._jwt_is_expiring(str(tokens["access_token"]), 0)
    detail = "ChatGPT account" + (f" …{acct[-6:]}" if acct else "")
    if expiring:
        # Token itself is stale but the refresh token is likely still good —
        # we'll refresh transparently on first use, so still "signed in".
        detail += " (token will refresh on use)"
    return OAuthStatus("openai-codex", True, source, detail, "")


def oauth_status(provider: str) -> OAuthStatus:
    """Read-only sign-in status for a native OAuth provider."""
    provider = (provider or "").strip().lower()
    if provider not in NATIVE_OAUTH_PROVIDERS:
        raise ValueError(f"not a native OAuth provider: {provider!r}")
    # An explicit disconnect reads as signed-out even if a vendor CLI credential
    # is still on disk — otherwise status would say "signed in" while resolution
    # refuses.
    if _oauth.is_disconnected(provider):
        return OAuthStatus(provider, False, "", "disconnected", _SIGN_IN_HINTS[provider])
    return _anthropic_status() if provider == "anthropic-oauth" else _codex_status()


def all_oauth_status() -> list[dict]:
    """Status for every native OAuth provider — the UI renders all of them."""
    return [oauth_status(p).as_dict() for p in sorted(NATIVE_OAUTH_PROVIDERS)]


# ── model listing ────────────────────────────────────────────────────────────

# Fallback Claude ids if the live /models probe fails (offline, or the OAuth
# token can't list). Kept short and current; the live probe is preferred.
_ANTHROPIC_FALLBACK_MODELS = [
    "claude-opus-4-1",
    "claude-sonnet-4-5",
    "claude-haiku-4-5",
]
_MODELS_TIMEOUT_S = 15.0


def _list_codex_models() -> tuple[list[str], str]:
    try:
        creds = _oauth.resolve_codex_oauth()
    except _oauth.OAuthCredentialError as exc:
        return [], str(exc)
    headers = {
        "Authorization": f"Bearer {creds.access_token}",
        "User-Agent": "codex-cli",
        "originator": "codex_cli_rs",
    }
    if creds.account_id:
        headers["ChatGPT-Account-Id"] = creds.account_id
    try:
        resp = httpx.get(
            f"{creds.base_url}/models?client_version=1.0.0",
            headers=headers,
            timeout=_MODELS_TIMEOUT_S,
        )
        resp.raise_for_status()
        models = [
            str(m.get("slug"))
            for m in resp.json().get("models", [])
            if isinstance(m, dict) and m.get("slug")
        ]
        return models, ""
    except httpx.HTTPError as exc:
        return [], f"Could not list Codex models: {exc}"


def _list_anthropic_models() -> tuple[list[str], str]:
    try:
        creds = _oauth.resolve_anthropic_oauth()
    except _oauth.OAuthCredentialError as exc:
        return _ANTHROPIC_FALLBACK_MODELS, str(exc)
    from voice.llm.anthropic_oauth import oauth_default_headers

    headers = {"Authorization": f"Bearer {creds.access_token}", "anthropic-version": "2023-06-01"}
    headers.update(oauth_default_headers())
    try:
        resp = httpx.get("https://api.anthropic.com/v1/models", headers=headers, timeout=_MODELS_TIMEOUT_S)
        resp.raise_for_status()
        models = [
            str(m.get("id")) for m in resp.json().get("data", []) if isinstance(m, dict) and m.get("id")
        ]
        return (models or _ANTHROPIC_FALLBACK_MODELS), ""
    except httpx.HTTPError:
        # The OAuth token may not carry models:list scope — fall back to the curated set.
        return _ANTHROPIC_FALLBACK_MODELS, ""


def list_provider_models(provider: str) -> tuple[list[str], str]:
    """Return ``(models, error)`` for a native OAuth provider's account.

    Codex is probed live from the account's ``/models`` endpoint; Claude tries
    the Anthropic ``/v1/models`` API and falls back to a curated list.
    """
    provider = (provider or "").strip().lower()
    if provider == "openai-codex":
        return _list_codex_models()
    if provider == "anthropic-oauth":
        return _list_anthropic_models()
    raise ValueError(f"not a native OAuth provider: {provider!r}")


# ── test connection ──────────────────────────────────────────────────────────


def validate_oauth_connection(provider: str, model: str) -> tuple[bool, str]:
    """The wizard/Settings "Test" for a native OAuth provider.

    Speaks each backend's real wire protocol with the real credential —
    Claude via a 1-token Messages call (Bearer + OAuth betas + the identity
    system block), Codex via a streamed Responses call (streaming is mandatory
    there). Returns ``(ok, error)``.
    """
    provider = (provider or "").strip().lower()
    model = (model or "").strip()
    if provider not in NATIVE_OAUTH_PROVIDERS:
        return False, f"not a native OAuth provider: {provider!r}"
    if not model:
        return False, "Pick a model first."
    try:
        if provider == "anthropic-oauth":
            return _validate_anthropic(model)
        return _validate_codex(model)
    except _oauth.OAuthCredentialError as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001 — surface the provider's own error text
        return False, str(exc)


def _validate_anthropic(model: str) -> tuple[bool, str]:
    import anthropic

    from voice.llm.anthropic_oauth import (
        OAUTH_BETAS,
        _with_identity_prefix,
        oauth_default_headers,
    )

    creds = _oauth.resolve_anthropic_oauth()
    client = anthropic.Anthropic(
        api_key=None, auth_token=creds.access_token, default_headers=oauth_default_headers()
    )
    resp = client.beta.messages.create(
        model=model,
        max_tokens=8,
        # The enforcement wants the identity line as an exact first BLOCK —
        # no string shape is valid (protoAgent #2764).
        system=_with_identity_prefix(None),
        messages=[{"role": "user", "content": "Reply with: ok"}],
        betas=list(OAUTH_BETAS),
    )
    if not resp.content:
        return False, "The provider accepted the request but returned no content."
    return True, ""


def _validate_codex(model: str) -> tuple[bool, str]:
    import openai

    from voice.llm.openai_codex import _CODEX_ORIGINATOR, _CODEX_USER_AGENT

    creds = _oauth.resolve_codex_oauth()
    headers = {
        "OpenAI-Beta": "responses=experimental",
        "originator": _CODEX_ORIGINATOR,
        "User-Agent": _CODEX_USER_AGENT,
    }
    if creds.account_id:
        headers["ChatGPT-Account-Id"] = creds.account_id
    client = openai.OpenAI(
        api_key=creds.access_token, base_url=creds.base_url, default_headers=headers
    )
    stream = client.responses.create(
        model=model,
        input=[{"role": "user", "content": "Reply with: ok"}],
        instructions="You are a connectivity check. Reply with: ok",
        store=False,
        stream=True,  # the Codex backend mandates streaming
    )
    got = False
    for _event in stream:
        got = True
        break
    stream.close()
    if not got:
        return False, "The provider accepted the request but streamed no response."
    return True, ""
