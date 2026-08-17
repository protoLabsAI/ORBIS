"""OAuth-subscription credential resolution for the native LLM providers.

Two ``persona.llm.provider`` values authenticate ORBIS's voice pipeline with a
coding-agent OAuth subscription instead of a gateway API key (ported from
protoAgent ADR 0097, including the #2440/#2441/#2461 lifecycle fixes). Their
credential stories differ deliberately:

- ``anthropic-oauth`` — READ Claude Code's own credentials live
  (``$CLAUDE_CODE_OAUTH_TOKEN`` env, ``~/.claude/.credentials.json``, or the
  macOS Keychain item the CLI actually writes on darwin), or use the token
  ORBIS's own in-app sign-in minted. Claude Code owns login *and* refresh for
  its stores; ORBIS refreshes only its own. Anthropic's Agent
  SDK (2026-06) explicitly licenses a third-party app authenticating with a
  user's Claude subscription.

- ``openai-codex`` — BOOTSTRAP from the Codex CLI's store (``~/.codex/auth.json``),
  then keep and refresh ORBIS's OWN copy under the data dir. OAuth refresh tokens
  are single-use, so sharing one file with the Codex CLI means the two clients
  rotate each other's tokens and race to 401 — owning our copy avoids that.
  Using ChatGPT/Codex OAuth from a third-party app is a grayer ToS area than the
  Claude path; the provider is opt-in.

This module resolves *credentials only* — the pipecat service builders live in
:mod:`voice.llm.anthropic_oauth` and :mod:`voice.llm.openai_codex`; the in-app
sign-in flows live in :mod:`voice.llm.oauth_login`.

Concurrency: resolution runs per turn and from the wizard/status routes, so two
in-process consumers can race the read→refresh→write on a single-use refresh
token. A per-store ``threading.Lock`` serializes the slow (refresh/bootstrap)
path; warm reads stay lock-free. Disconnect takes the same lock so it can't race
a refresh that would rewrite the store after deletion.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from agent.paths import get_oauth_dir

log = logging.getLogger(__name__)

NATIVE_OAUTH_PROVIDERS = frozenset({"anthropic-oauth", "openai-codex"})

_STORE_LOCKS: dict[str, threading.Lock] = {}
_STORE_LOCKS_GUARD = threading.Lock()

# Explicit disconnect: a provider listed here must NOT auto-resolve (no Codex-CLI
# re-bootstrap, no stored/CLI Claude token) until an in-app sign-in reconnects it.
_DISCONNECT_MARKER = "oauth-disconnected.json"


def _store_lock(path: Path) -> threading.Lock:
    key = str(path)
    with _STORE_LOCKS_GUARD:
        lock = _STORE_LOCKS.get(key)
        if lock is None:
            lock = _STORE_LOCKS[key] = threading.Lock()
        return lock


def _atomic_write(path: Path, text: str, mode: int = 0o600) -> None:
    """tempfile → os.replace in the target dir (atomic on POSIX), owner-only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _disconnect_marker_path() -> Path:
    return get_oauth_dir() / _DISCONNECT_MARKER


def _disconnected_providers() -> set[str]:
    try:
        doc = json.loads(_disconnect_marker_path().read_text())
    except (OSError, ValueError, json.JSONDecodeError):
        return set()
    return set(doc) if isinstance(doc, list) else set()


def is_disconnected(provider: str) -> bool:
    return provider in _disconnected_providers()


def _write_disconnected(providers: set[str]) -> None:
    path = _disconnect_marker_path()
    if not providers:
        path.unlink(missing_ok=True)
        return
    _atomic_write(path, json.dumps(sorted(providers)))


def _mark_disconnected(provider: str) -> None:
    _write_disconnected(_disconnected_providers() | {provider})
    _reset_resolve_cache()


def clear_disconnected(provider: str) -> None:
    """An explicit in-app sign-in clears the disconnect intent for ``provider``."""
    _write_disconnected(_disconnected_providers() - {provider})
    _reset_resolve_cache()


class OAuthCredentialError(RuntimeError):
    """No usable OAuth credential could be resolved for a native provider.

    Carries a ``provider`` and a ``relogin`` hint so the caller (make_llm / a
    status route) can surface an actionable message instead of a bare 401.
    """

    def __init__(self, message: str, *, provider: str, relogin: bool = True) -> None:
        super().__init__(message)
        self.provider = provider
        self.relogin = relogin


# ── Anthropic (Claude) — env / own store / Claude Code's file ─────────────────

# The env var Claude Code sets for embedding hosts; also holds a `sk-ant-oat…`
# *setup token* a user can paste for a headless box (`claude setup-token`).
_CLAUDE_ENV_VAR = "CLAUDE_CODE_OAUTH_TOKEN"
_CLAUDE_CREDS_FILE = Path.home() / ".claude" / ".credentials.json"
# Refresh 60s early so a token that expires mid-turn isn't handed out.
_ANTHROPIC_REFRESH_SKEW_S = 60
# Claude Code's OAuth endpoints + public client id — used to REFRESH tokens that
# ORBIS's own in-app sign-in minted (voice/llm/oauth_login.py). See the ToS note
# there: minting via this client is opt-in and the operator's call.
_ANTHROPIC_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"  # noqa: S105 — public OAuth endpoint
_ANTHROPIC_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"


@dataclass(frozen=True)
class AnthropicOAuthCreds:
    access_token: str
    source: str  # "env" | "credentials_file" | "keychain" | "instance_store"
    expires_at: float | None = None  # epoch seconds, when known


def _read_claude_credentials_file() -> dict[str, Any] | None:
    try:
        raw = _CLAUDE_CREDS_FILE.read_text()
    except (OSError, ValueError):
        return None
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError:
        log.debug("~/.claude/.credentials.json is not valid JSON")
        return None
    return doc if isinstance(doc, dict) else None


def _read_claude_keychain() -> dict[str, Any] | None:
    """Claude Code's macOS credential store (ported from protoAgent #2508).

    On macOS the CLI writes its login to the Keychain (a "Claude Code-credentials"
    generic password), NOT ``~/.claude/.credentials.json`` — so a file-only read
    makes the borrow-the-CLI-login story silently dead on the one platform ORBIS
    ships on. Same JSON document shape as the credentials file. Returns None
    off-macOS, when the item is absent, or on any error — never raises, bounded
    wait.
    """
    if sys.platform != "darwin":
        return None
    try:
        proc = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:  # absent item exits 44; a locked keychain errors similarly
        return None
    try:
        doc = json.loads(proc.stdout.strip())
    except json.JSONDecodeError:
        return None
    return doc if isinstance(doc, dict) else None


def _creds_from_claude_doc(doc: dict[str, Any], source: str) -> AnthropicOAuthCreds | None:
    """The ``claudeAiOauth`` document (credentials file / Keychain item) → creds.

    None when the shape/token is missing. Expiry is advisory only — Claude Code
    owns refresh for its own login; a dead token surfaces as a clean 401 the
    caller maps to a relogin hint.
    """
    oauth = doc.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return None
    token = str(oauth.get("accessToken", "") or "").strip()
    if not token:
        return None
    exp_ms = oauth.get("expiresAt")
    expires_at = float(exp_ms) / 1000.0 if isinstance(exp_ms, (int, float)) else None
    if expires_at is not None and expires_at <= time.time() - _ANTHROPIC_REFRESH_SKEW_S:
        log.info(
            "[anthropic-oauth] Claude Code token looks expired (run any `claude` "
            "command to refresh); trying it anyway",
        )
    return AnthropicOAuthCreds(access_token=token, source=source, expires_at=expires_at)


def _anthropic_store_path() -> Path:
    """ORBIS's own Claude token copy (from in-app sign-in)."""
    return get_oauth_dir() / "anthropic-oauth.json"


def _read_anthropic_store() -> dict[str, Any] | None:
    try:
        doc = json.loads(_anthropic_store_path().read_text())
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return doc if isinstance(doc, dict) and doc.get("access_token") else None


def _write_anthropic_store(tokens: dict[str, Any]) -> None:
    """Persist a freshly minted/refreshed Claude token set. Stamps ``expires_at``
    from the response's ``expires_in`` so the resolver can refresh proactively."""
    access = str(tokens.get("access_token", "") or "").strip()
    if not access:
        return
    expires_in = tokens.get("expires_in")
    expires_at = _now() + float(expires_in) if isinstance(expires_in, (int, float)) else None
    doc = {
        "access_token": access,
        "refresh_token": str(tokens.get("refresh_token", "") or ""),
        "expires_at": expires_at,
    }
    _atomic_write(_anthropic_store_path(), json.dumps(doc))
    _reset_resolve_cache()  # a fresh sign-in/refresh must be picked up immediately


def _now() -> float:
    return time.time()


def _refresh_anthropic_tokens(refresh_token: str, *, timeout_s: float = 20.0) -> dict[str, Any]:
    resp = httpx.post(
        _ANTHROPIC_TOKEN_URL,
        json={"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": _ANTHROPIC_CLIENT_ID},
        headers={"Content-Type": "application/json"},
        timeout=httpx.Timeout(max(5.0, float(timeout_s))),
    )
    if resp.status_code != 200:
        raise OAuthCredentialError(
            f"Claude token refresh failed (HTTP {resp.status_code}). Sign in again.",
            provider="anthropic-oauth",
            relogin=resp.status_code in {400, 401, 403},
        )
    tokens = resp.json()
    if not str(tokens.get("access_token", "") or ""):
        raise OAuthCredentialError(
            "Claude token refresh returned no access_token.", provider="anthropic-oauth"
        )
    return tokens


def resolve_anthropic_oauth() -> AnthropicOAuthCreds:
    """Return a live Claude OAuth access token, or raise OAuthCredentialError.

    Order (explicit intent first): ``$CLAUDE_CODE_OAUTH_TOKEN`` env → ORBIS's own
    store from in-app sign-in (refreshed when expiring, under the store lock) →
    ``~/.claude/.credentials.json`` → the macOS Keychain item Claude Code writes
    on darwin (same document shape). Only our own store is refreshed here; Claude
    Code owns refresh for its stores, and a truly-dead CLI token surfaces as a
    clean 401 the caller maps to a relogin hint.
    """
    env_token = os.environ.get(_CLAUDE_ENV_VAR, "").strip()
    if env_token:
        return AnthropicOAuthCreds(access_token=env_token, source="env")

    # Once disconnected in the app, don't auto-resolve from our store or Claude
    # Code's credentials until an in-app sign-in reconnects. (An explicit
    # CLAUDE_CODE_OAUTH_TOKEN env above still wins — it's deliberate config.)
    if is_disconnected("anthropic-oauth"):
        raise OAuthCredentialError(
            "Claude is disconnected in ORBIS. Sign in again to reconnect.",
            provider="anthropic-oauth",
        )

    store = _read_anthropic_store()
    if store:
        access = str(store["access_token"]).strip()
        expires_at = store.get("expires_at")
        expiring = isinstance(expires_at, (int, float)) and expires_at <= _now() + _ANTHROPIC_REFRESH_SKEW_S
        if expiring and str(store.get("refresh_token", "") or ""):
            with _store_lock(_anthropic_store_path()):
                store = _read_anthropic_store() or store  # a peer may have refreshed while we waited
                expires_at = store.get("expires_at")
                still_expiring = (
                    isinstance(expires_at, (int, float))
                    and expires_at <= _now() + _ANTHROPIC_REFRESH_SKEW_S
                )
                if still_expiring and str(store.get("refresh_token", "") or ""):
                    refreshed = _refresh_anthropic_tokens(str(store["refresh_token"]))
                    # A refresh may not return a new refresh_token — keep the old one.
                    refreshed.setdefault("refresh_token", store["refresh_token"])
                    _write_anthropic_store(refreshed)
                    store = _read_anthropic_store() or refreshed
            return AnthropicOAuthCreds(
                access_token=str(store["access_token"]).strip(), source="instance_store"
            )
        return AnthropicOAuthCreds(
            access_token=access,
            source="instance_store",
            expires_at=expires_at if isinstance(expires_at, (int, float)) else None,
        )

    doc = _read_claude_credentials_file()
    if doc:
        creds = _creds_from_claude_doc(doc, "credentials_file")
        if creds:
            return creds

    # macOS: Claude Code's login lives in the Keychain, not the credentials file.
    doc = _read_claude_keychain()
    if doc:
        creds = _creds_from_claude_doc(doc, "keychain")
        if creds:
            return creds

    raise OAuthCredentialError(
        "No Claude OAuth credential found. Sign in from the ORBIS settings, the Claude "
        "Code CLI (`claude`), or set CLAUDE_CODE_OAUTH_TOKEN to a setup token "
        "(`claude setup-token`).",
        provider="anthropic-oauth",
    )


# How long a resolved Claude credential is reused before the stores are re-read.
# Resolution can shell out to the macOS Keychain (`security find-generic-password`),
# and the voice pipeline + filler tier resolve before every request — a turn makes
# several. A short TTL keeps the subprocess off the hot path while bounding
# staleness to seconds (protoAgent #2604); sign-in, refresh, and disconnect reset
# the cache so they apply immediately.
_RESOLVE_TTL_S = 30.0
_resolve_cache: tuple[AnthropicOAuthCreds, float] | None = None


def resolve_anthropic_oauth_cached(*, force: bool = False) -> AnthropicOAuthCreds:
    """:func:`resolve_anthropic_oauth`, re-run at most every :data:`_RESOLVE_TTL_S`.

    ``force=True`` bypasses the cache — for when the credential in hand is
    precisely the one that just failed. Failures are never cached: a signed-out
    resolve raises every time until a sign-in lands.
    """
    global _resolve_cache
    if not force and _resolve_cache is not None:
        creds, at = _resolve_cache
        if time.monotonic() - at < _RESOLVE_TTL_S:
            return creds
    creds = resolve_anthropic_oauth()
    _resolve_cache = (creds, time.monotonic())
    return creds


def _reset_resolve_cache() -> None:
    """Drop the cached credential (sign-in, refresh, disconnect, tests)."""
    global _resolve_cache
    _resolve_cache = None


# ── OpenAI Codex — bootstrap-then-own, with refresh ───────────────────────────

_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
_CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"  # noqa: S105 — public OAuth endpoint
_CODEX_DEFAULT_BASE_URL = "https://chatgpt.com/backend-api/codex"
_CODEX_CLI_AUTH_FILE = Path.home() / ".codex" / "auth.json"
_CODEX_REFRESH_SKEW_S = 120
_CODEX_USER_AGENT = "ORBIS-codex/0.1 (+https://github.com/protoLabsAI/ORBIS)"


@dataclass(frozen=True)
class CodexOAuthCreds:
    access_token: str
    account_id: str | None
    base_url: str
    source: str  # "instance_store" | "codex_cli_bootstrap"


def _codex_store_path() -> Path:
    """ORBIS's own Codex token copy."""
    return get_oauth_dir() / "codex-oauth.json"


def codex_base_url() -> str:
    """The Codex Responses backend base URL (env-overridable for testing)."""
    return os.environ.get("ORBIS_CODEX_BASE_URL", "").strip().rstrip("/") or _CODEX_DEFAULT_BASE_URL


def _b64url_json(segment: str) -> dict[str, Any]:
    """Decode one base64url JWT segment to a JSON object (no signature check —
    we only read the account-id claim, never trust it for auth)."""
    pad = "=" * (-len(segment) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(segment + pad))
    except (binascii.Error, ValueError, json.JSONDecodeError):
        return {}


def _codex_account_id(tokens: dict[str, Any]) -> str | None:
    """The ChatGPT account id for the ``ChatGPT-Account-Id`` header.

    Prefer an explicit ``account_id`` field; otherwise pull the
    ``chatgpt_account_id`` claim out of the id_token (or access_token) JWT.
    """
    explicit = tokens.get("account_id")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    for key in ("id_token", "access_token"):
        jwt = tokens.get(key)
        if not isinstance(jwt, str) or jwt.count(".") != 2:
            continue
        claims = _b64url_json(jwt.split(".")[1])
        auth = claims.get("https://api.openai.com/auth")
        if isinstance(auth, dict):
            acct = auth.get("chatgpt_account_id") or auth.get("chatgpt_user_id")
            if isinstance(acct, str) and acct.strip():
                return acct.strip()
    return None


def _jwt_is_expiring(access_token: str, skew_s: int) -> bool:
    """True if the access-token JWT's ``exp`` is within ``skew_s`` (or unreadable)."""
    exp = _jwt_expiry(access_token)
    return True if exp is None else exp <= time.time() + skew_s


def _jwt_expiry(access_token: str) -> float | None:
    """The access-token JWT's ``exp`` (epoch seconds), or None when unreadable."""
    if not isinstance(access_token, str) or access_token.count(".") != 2:
        return None
    exp = _b64url_json(access_token.split(".")[1]).get("exp")
    return float(exp) if isinstance(exp, (int, float)) else None


def _read_codex_tokens(path: Path) -> dict[str, Any] | None:
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(doc, dict):
        return None
    tokens = doc.get("tokens")
    return tokens if isinstance(tokens, dict) else None


def _refresh_codex_tokens(tokens: dict[str, Any], *, timeout_s: float = 20.0) -> dict[str, Any]:
    """POST the refresh grant to OpenAI's token endpoint; return updated tokens.

    Preserves ``refresh_token`` when the response rotates it (falls back to the
    old one) and the ``account_id`` we already resolved.
    """
    refresh_token = str(tokens.get("refresh_token", "") or "").strip()
    if not refresh_token:
        raise OAuthCredentialError(
            "Codex credentials are missing a refresh_token. Sign in again.",
            provider="openai-codex",
        )
    try:
        resp = httpx.post(
            _CODEX_TOKEN_URL,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": _CODEX_USER_AGENT,
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": _CODEX_CLIENT_ID,
            },
            timeout=httpx.Timeout(max(5.0, float(timeout_s))),
        )
    except httpx.HTTPError as exc:
        raise OAuthCredentialError(
            f"Codex token refresh could not reach OpenAI: {exc}",
            provider="openai-codex",
            relogin=False,
        ) from exc

    if resp.status_code != 200:
        relogin = resp.status_code in {400, 401, 403}
        raise OAuthCredentialError(
            f"Codex token refresh failed (HTTP {resp.status_code}). "
            "Sign in again (in ORBIS settings, or re-run `codex` in your terminal).",
            provider="openai-codex",
            relogin=relogin,
        )
    try:
        payload = resp.json()
    except ValueError as exc:
        raise OAuthCredentialError(
            "Codex token refresh returned invalid JSON.", provider="openai-codex"
        ) from exc

    new_access = str(payload.get("access_token", "") or "").strip()
    if not new_access:
        raise OAuthCredentialError(
            "Codex token refresh response was missing access_token.",
            provider="openai-codex",
        )
    updated = dict(tokens)
    updated["access_token"] = new_access
    if isinstance(payload.get("refresh_token"), str) and payload["refresh_token"].strip():
        updated["refresh_token"] = payload["refresh_token"].strip()
    if isinstance(payload.get("id_token"), str) and payload["id_token"].strip():
        updated["id_token"] = payload["id_token"].strip()
    return updated


# Credential provenance: who minted the token set this store holds.
# "cli_bootstrap" — copied from the Codex CLI's auth.json; the login is SHARED
# with another application, so ORBIS must never remotely revoke it.
# "device_login" — minted by ORBIS's own in-app device sign-in; ours to revoke.
# Stores written before this field exist ("" on read) and are treated as
# borrowed: with ownership unproven, deleting our copy is the only safe scope.
PROVENANCE_CLI_BOOTSTRAP = "cli_bootstrap"
PROVENANCE_DEVICE_LOGIN = "device_login"


def _read_codex_provenance(path: Path) -> str:
    try:
        doc = json.loads(path.read_text())
    except (OSError, ValueError, json.JSONDecodeError):
        return ""
    return str(doc.get("provenance", "") or "") if isinstance(doc, dict) else ""


def _write_codex_store(path: Path, tokens: dict[str, Any], provenance: str | None = None) -> None:
    """Persist the token set. ``provenance=None`` (the refresh path) preserves
    whatever the store already recorded — a refresh rotates tokens, it does not
    change who minted the login."""
    if provenance is None:
        provenance = _read_codex_provenance(path)
    doc: dict[str, Any] = {"tokens": tokens, "last_refresh": time.time()}
    if provenance:
        doc["provenance"] = provenance
    _atomic_write(path, json.dumps(doc))


def resolve_codex_oauth() -> CodexOAuthCreds:
    """Return a fresh Codex access token + account id, refreshing/bootstrapping as needed.

    1. Read ORBIS's own store; if absent, bootstrap it once from the Codex CLI's
       ``~/.codex/auth.json``.
    2. If the access token is expiring, refresh against OpenAI and persist our
       copy (never the Codex CLI's file — we don't rotate its single-use token).

    Serialized per store: concurrent resolutions can't both spend the same
    single-use refresh token — a warm read is lock-free, but the refresh/bootstrap
    path takes the store lock and re-reads, so a waiter reuses the token the first
    caller minted.
    """
    store = _codex_store_path()

    def _creds(tokens: dict[str, Any], access: str, source: str) -> CodexOAuthCreds:
        return CodexOAuthCreds(access_token=access, account_id=_codex_account_id(tokens), base_url=codex_base_url(), source=source)

    # Fast path: a warm, unexpired store read needs neither the lock nor a write.
    tokens = _read_codex_tokens(store)
    if tokens:
        access = str(tokens.get("access_token", "") or "").strip()
        if access and not _jwt_is_expiring(access, _CODEX_REFRESH_SKEW_S):
            return _creds(tokens, access, "instance_store")

    # Slow path: refresh or first bootstrap — serialized so single-use refresh is spent once.
    with _store_lock(store):
        tokens = _read_codex_tokens(store)  # re-read: a peer may have refreshed while we waited
        source = "instance_store"
        if tokens is None:
            # Respect an explicit disconnect: do NOT silently re-import the Codex
            # CLI's credential until an in-app sign-in reconnects.
            if is_disconnected("openai-codex"):
                raise OAuthCredentialError(
                    "Codex is disconnected in ORBIS. Sign in again to reconnect.",
                    provider="openai-codex",
                )
            tokens = _read_codex_tokens(_CODEX_CLI_AUTH_FILE)
            source = "codex_cli_bootstrap"
            if tokens is None:
                raise OAuthCredentialError(
                    "No Codex OAuth credential found. Sign in from ORBIS settings or "
                    "with the Codex CLI (`codex`), then retry — ORBIS imports it once "
                    "and keeps its own refreshed copy.",
                    provider="openai-codex",
                )

        access = str(tokens.get("access_token", "") or "").strip()
        refreshed = False
        if not access or _jwt_is_expiring(access, _CODEX_REFRESH_SKEW_S):
            tokens = _refresh_codex_tokens(tokens)
            access = str(tokens["access_token"]).strip()
            refreshed = True

        if source == "codex_cli_bootstrap":
            # Stamp the borrowed origin — disconnect uses it to scope itself to
            # our copy instead of revoking a login the CLI still holds.
            _write_codex_store(store, tokens, provenance=PROVENANCE_CLI_BOOTSTRAP)
        elif refreshed:
            _write_codex_store(store, tokens)
        return _creds(tokens, access, "instance_store" if refreshed else source)


# ── Disconnect / revoke lifecycle ─────────────────────────────────────────────

_CODEX_REVOKE_URL = "https://auth.openai.com/oauth/revoke"  # noqa: S105 — public OAuth endpoint


@dataclass(frozen=True)
class DisconnectResult:
    provider: str
    removed: bool  # ORBIS's own credential store was deleted
    revoked: bool  # remote revocation succeeded (best-effort; OpenAI only)
    note: str

    def as_dict(self) -> dict[str, Any]:
        return {"provider": self.provider, "removed": self.removed, "revoked": self.revoked, "note": self.note}


def _revoke_codex_token(tokens: dict[str, Any], *, timeout_s: float = 8.0) -> bool:
    """Best-effort revoke of ORBIS's OpenAI token (refresh first, then access).
    Never raises — a failed/unreachable revoke must not block local deletion."""
    for hint in ("refresh_token", "access_token"):
        tok = str(tokens.get(hint, "") or "").strip()
        if not tok:
            continue
        try:
            resp = httpx.post(
                _CODEX_REVOKE_URL,
                data={"client_id": _CODEX_CLIENT_ID, "token": tok, "token_type_hint": hint},
                headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": _CODEX_USER_AGENT},
                timeout=httpx.Timeout(max(3.0, float(timeout_s))),
            )
            if resp.status_code in (200, 204):
                return True
        except httpx.HTTPError:
            continue
    return False


def disconnect(provider: str) -> DisconnectResult:
    """Idempotent disconnect for a native OAuth provider.

    Attempts best-effort remote revocation for ORBIS-owned tokens, ALWAYS deletes
    ORBIS's own credential store (even when revocation fails), and marks the
    provider disconnected so it won't auto-resolve until an in-app sign-in
    reconnects it. The vendor CLI's own auth file (``~/.codex/auth.json`` /
    ``~/.claude/.credentials.json``) is never modified. Takes the same per-store
    lock as resolution, so it can't race a refresh that would rewrite the store
    after deletion.
    """
    provider = (provider or "").strip().lower()
    if provider == "openai-codex":
        store = _codex_store_path()
        with _store_lock(store):
            tokens = _read_codex_tokens(store)  # our copy only — never ~/.codex/auth.json
            # Ownership gate: a bootstrap-derived token set is the Codex CLI's
            # login, borrowed — remote revocation would sign the CLI out too,
            # well outside ORBIS's mandate. Only a credential our own device
            # sign-in minted is ours to revoke; a legacy store with no
            # provenance is treated as borrowed (ownership unproven).
            owned = _read_codex_provenance(store) == PROVENANCE_DEVICE_LOGIN
            revoked = _revoke_codex_token(tokens) if (tokens and owned) else False
            existed = store.exists()
            store.unlink(missing_ok=True)
            _mark_disconnected(provider)
        if not existed:
            note = "already disconnected"
        elif revoked:
            note = "revoked at OpenAI and removed ORBIS's local copy"
        elif not owned:
            note = (
                "removed ORBIS's borrowed copy — the login is shared with the "
                "Codex CLI, so it was not revoked remotely"
            )
        else:
            note = "removed ORBIS's local copy (remote revoke did not confirm)"
        return DisconnectResult(provider, removed=existed, revoked=revoked, note=note)
    if provider == "anthropic-oauth":
        store = _anthropic_store_path()
        with _store_lock(store):
            existed = store.exists()
            store.unlink(missing_ok=True)
            _mark_disconnected(provider)
        # Anthropic has no token-revoke endpoint for these tokens; local removal +
        # suppression is the contract. Claude Code's own credentials are untouched.
        return DisconnectResult(
            provider, removed=existed, revoked=False,
            note="removed ORBIS's Claude token; sign in again to reconnect",
        )
    raise OAuthCredentialError(f"not a native OAuth provider: {provider!r}", provider=provider)
