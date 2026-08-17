"""OAuth subscription providers — credentials, sign-in flows, and adapters.

Port of protoAgent's ADR 0097 suite, adapted to ORBIS's pipecat stack. Three
layers, no network anywhere (httpx is monkeypatched at the module seam):

- ``voice/llm/oauth.py`` — resolution order, refresh, bootstrap-then-own,
  provenance, and the disconnect lifecycle (never touch the vendor CLI's file,
  never remote-revoke a borrowed login).
- ``voice/llm/oauth_login.py`` — the in-app device/PKCE sign-in flows.
- ``voice/llm/anthropic_oauth.py`` / ``openai_codex.py`` — the pipecat service
  builders: Bearer wiring, identity prefix, beta headers, Codex request rules,
  and (mirroring ``test_tool_loop``) that both adapters route through the
  tool-loop guard.
"""

from __future__ import annotations

import base64
import json
import threading
import time
from types import SimpleNamespace

import pytest
from pipecat.services.openai.llm import OpenAILLMService

import voice.llm.oauth as oauth
import voice.llm.oauth_discovery as discovery
import voice.llm.oauth_login as oauth_login
from voice.llm import make_llm


# The real Keychain reader, for the tests that exercise its parsing — the
# autouse fixture below stubs the module attribute (the dev machine's REAL
# Keychain holds a Claude Code login).
_real_read_keychain = oauth._read_claude_keychain


@pytest.fixture(autouse=True)
def _isolated_stores(monkeypatch, tmp_path):
    """Every test gets its own oauth dir + fake vendor-CLI files, and never
    sees the developer's real credentials (incl. the macOS Keychain)."""
    monkeypatch.setenv("ORBIS_OAUTH_DIR", str(tmp_path / "oauth"))
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr(oauth, "_CLAUDE_CREDS_FILE", tmp_path / "claude-creds.json")
    monkeypatch.setattr(oauth, "_CODEX_CLI_AUTH_FILE", tmp_path / "codex-auth.json")
    monkeypatch.setattr(oauth, "_read_claude_keychain", lambda: None)
    oauth._reset_resolve_cache()
    yield
    oauth._reset_resolve_cache()


def _jwt(claims: dict) -> str:
    seg = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"e30.{seg}.sig"


def _codex_tokens(*, exp_in=3600, account="acct-123", refresh="rt-1"):
    return {
        "access_token": _jwt({"exp": time.time() + exp_in}),
        "refresh_token": refresh,
        "account_id": account,
    }


def _write_cli_codex(tokens):
    oauth._CODEX_CLI_AUTH_FILE.write_text(json.dumps({"tokens": tokens}))


# --- anthropic resolution --------------------------------------------------


def test_anthropic_env_token_wins(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat-abc")
    creds = oauth.resolve_anthropic_oauth()
    assert creds.access_token == "sk-ant-oat-abc"
    assert creds.source == "env"


def test_anthropic_reads_credentials_file():
    oauth._CLAUDE_CREDS_FILE.write_text(json.dumps(
        {"claudeAiOauth": {"accessToken": "tok-file", "expiresAt": (time.time() + 600) * 1000}}
    ))
    creds = oauth.resolve_anthropic_oauth()
    assert creds.access_token == "tok-file"
    assert creds.source == "credentials_file"


def test_anthropic_missing_raises():
    with pytest.raises(oauth.OAuthCredentialError) as ei:
        oauth.resolve_anthropic_oauth()
    assert ei.value.provider == "anthropic-oauth"


def test_anthropic_own_store_beats_credentials_file():
    oauth._CLAUDE_CREDS_FILE.write_text(json.dumps({"claudeAiOauth": {"accessToken": "cli"}}))
    oauth._write_anthropic_store({"access_token": "mine", "expires_in": 3600})
    assert oauth.resolve_anthropic_oauth().access_token == "mine"


def test_anthropic_reads_keychain_when_file_absent(monkeypatch):
    monkeypatch.setattr(
        oauth, "_read_claude_keychain",
        lambda: {"claudeAiOauth": {"accessToken": "tok-kc", "expiresAt": (time.time() + 600) * 1000}},
    )
    creds = oauth.resolve_anthropic_oauth()
    assert creds.access_token == "tok-kc"
    assert creds.source == "keychain"


def test_anthropic_credentials_file_beats_keychain(monkeypatch):
    oauth._CLAUDE_CREDS_FILE.write_text(json.dumps({"claudeAiOauth": {"accessToken": "tok-file"}}))
    monkeypatch.setattr(
        oauth, "_read_claude_keychain", lambda: {"claudeAiOauth": {"accessToken": "tok-kc"}}
    )
    assert oauth.resolve_anthropic_oauth().source == "credentials_file"


def test_keychain_reader_parses_and_never_raises(monkeypatch):
    monkeypatch.setattr(oauth.sys, "platform", "darwin")
    doc = {"claudeAiOauth": {"accessToken": "kc"}}

    def _run(cmd, **kw):
        assert cmd[:2] == ["security", "find-generic-password"]
        assert kw.get("timeout") == 5  # bounded — a locked keychain must not hang a turn
        return SimpleNamespace(returncode=0, stdout=json.dumps(doc) + "\n")

    monkeypatch.setattr(oauth.subprocess, "run", _run)
    assert _real_read_keychain() == doc
    # absent item (exit 44), broken output, and a raising subprocess → None, never a raise
    monkeypatch.setattr(
        oauth.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=44, stdout="")
    )
    assert _real_read_keychain() is None
    monkeypatch.setattr(
        oauth.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="not-json")
    )
    assert _real_read_keychain() is None

    def _boom(*a, **k):
        raise OSError("no security binary")

    monkeypatch.setattr(oauth.subprocess, "run", _boom)
    assert _real_read_keychain() is None


def test_keychain_reader_is_darwin_only(monkeypatch):
    monkeypatch.setattr(oauth.sys, "platform", "linux")
    monkeypatch.setattr(
        oauth.subprocess, "run", lambda *a, **k: pytest.fail("no subprocess off-macOS")
    )
    assert _real_read_keychain() is None


def test_anthropic_store_refreshes_when_expiring(monkeypatch):
    oauth._write_anthropic_store({"access_token": "old", "refresh_token": "rt", "expires_in": 10})
    calls = []

    def _post(url, **kw):
        calls.append(url)
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"access_token": "fresh", "expires_in": 3600},
        )

    monkeypatch.setattr(oauth.httpx, "post", _post)
    creds = oauth.resolve_anthropic_oauth()
    assert creds.access_token == "fresh"
    assert calls == [oauth._ANTHROPIC_TOKEN_URL]
    # the refresh_token the response omitted is preserved in the store
    assert oauth._read_anthropic_store()["refresh_token"] == "rt"


# --- codex resolution ------------------------------------------------------


def test_codex_bootstrap_stamps_borrowed_provenance():
    _write_cli_codex(_codex_tokens())
    creds = oauth.resolve_codex_oauth()
    assert creds.account_id == "acct-123"
    store = oauth._codex_store_path()
    assert store.exists()
    assert oauth._read_codex_provenance(store) == oauth.PROVENANCE_CLI_BOOTSTRAP
    # owner-only credential file
    assert (store.stat().st_mode & 0o777) == 0o600


def test_codex_warm_read_neither_refreshes_nor_writes(monkeypatch):
    _write_cli_codex(_codex_tokens())
    oauth.resolve_codex_oauth()  # bootstrap
    store = oauth._codex_store_path()
    before = store.read_bytes()
    monkeypatch.setattr(oauth.httpx, "post", lambda *a, **k: pytest.fail("no HTTP on warm read"))
    creds = oauth.resolve_codex_oauth()
    assert creds.source == "instance_store"
    assert store.read_bytes() == before


def test_codex_refresh_on_expiry_preserves_provenance(monkeypatch):
    _write_cli_codex(_codex_tokens())
    oauth.resolve_codex_oauth()
    # overwrite our copy with an expiring token
    store = oauth._codex_store_path()
    oauth._write_codex_store(store, _codex_tokens(exp_in=5))

    def _post(url, **kw):
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"access_token": _jwt({"exp": time.time() + 3600}), "refresh_token": "rt-2"},
        )

    monkeypatch.setattr(oauth.httpx, "post", _post)
    creds = oauth.resolve_codex_oauth()
    assert creds.source == "instance_store"
    assert oauth._read_codex_tokens(store)["refresh_token"] == "rt-2"
    assert oauth._read_codex_provenance(store) == oauth.PROVENANCE_CLI_BOOTSTRAP


def test_codex_missing_raises():
    with pytest.raises(oauth.OAuthCredentialError) as ei:
        oauth.resolve_codex_oauth()
    assert ei.value.provider == "openai-codex"


def test_codex_account_id_from_explicit_and_jwt():
    assert oauth._codex_account_id({"account_id": "a1"}) == "a1"
    jwt = _jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "a2"}})
    assert oauth._codex_account_id({"id_token": jwt}) == "a2"
    assert oauth._codex_account_id({}) is None


def test_jwt_expiry():
    assert oauth._jwt_is_expiring(_jwt({"exp": time.time() + 5}), 120)
    assert not oauth._jwt_is_expiring(_jwt({"exp": time.time() + 3600}), 120)
    assert oauth._jwt_is_expiring("not-a-jwt", 0)


def test_codex_concurrent_refresh_spends_the_token_once(monkeypatch):
    """Two threads race an expired store; the single-use refresh grant must be
    spent exactly once (the waiter reuses the winner's token)."""
    _write_cli_codex(_codex_tokens())
    oauth.resolve_codex_oauth()
    oauth._write_codex_store(oauth._codex_store_path(), _codex_tokens(exp_in=5))
    refreshes = []
    lock = threading.Lock()

    def _post(url, **kw):
        with lock:
            refreshes.append(url)
        time.sleep(0.05)
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"access_token": _jwt({"exp": time.time() + 3600})},
        )

    monkeypatch.setattr(oauth.httpx, "post", _post)
    results = []
    threads = [threading.Thread(target=lambda: results.append(oauth.resolve_codex_oauth())) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 2
    assert len(refreshes) == 1


# --- disconnect lifecycle --------------------------------------------------


def test_disconnect_never_revokes_a_borrowed_credential(monkeypatch):
    _write_cli_codex(_codex_tokens())
    oauth.resolve_codex_oauth()  # bootstrap → borrowed
    monkeypatch.setattr(oauth.httpx, "post", lambda *a, **k: pytest.fail("borrowed login must not be revoked"))
    res = oauth.disconnect("openai-codex")
    assert res.removed and not res.revoked
    assert not oauth._codex_store_path().exists()
    # the Codex CLI's own file is untouched
    assert oauth._read_codex_tokens(oauth._CODEX_CLI_AUTH_FILE)


def test_disconnect_revokes_a_device_login(monkeypatch):
    oauth._write_codex_store(
        oauth._codex_store_path(), _codex_tokens(), provenance=oauth.PROVENANCE_DEVICE_LOGIN
    )
    revoked = []

    def _post(url, **kw):
        revoked.append(url)
        return SimpleNamespace(status_code=200, json=dict)

    monkeypatch.setattr(oauth.httpx, "post", _post)
    res = oauth.disconnect("openai-codex")
    assert res.revoked and res.removed
    assert revoked == [oauth._CODEX_REVOKE_URL]


def test_disconnect_removes_local_even_when_revoke_fails(monkeypatch):
    oauth._write_codex_store(
        oauth._codex_store_path(), _codex_tokens(), provenance=oauth.PROVENANCE_DEVICE_LOGIN
    )
    monkeypatch.setattr(
        oauth.httpx, "post",
        lambda *a, **k: (_ for _ in ()).throw(oauth.httpx.ConnectError("down")),
    )
    res = oauth.disconnect("openai-codex")
    assert res.removed and not res.revoked
    assert not oauth._codex_store_path().exists()


def test_disconnect_is_idempotent():
    res1 = oauth.disconnect("openai-codex")
    res2 = oauth.disconnect("openai-codex")
    assert not res1.removed and not res2.removed


def test_disconnect_suppresses_cli_reimport_until_reconnect(monkeypatch):
    _write_cli_codex(_codex_tokens())
    oauth.resolve_codex_oauth()
    oauth.disconnect("openai-codex")
    # CLI file still on disk, but resolution must refuse…
    with pytest.raises(oauth.OAuthCredentialError):
        oauth.resolve_codex_oauth()
    # …and status reads signed-out.
    assert discovery.oauth_status("openai-codex").signed_in is False
    # explicit reconnect clears the suppression
    oauth.clear_disconnected("openai-codex")
    assert oauth.resolve_codex_oauth().account_id == "acct-123"


def test_disconnect_anthropic_suppresses_credentials_file():
    oauth._CLAUDE_CREDS_FILE.write_text(json.dumps({"claudeAiOauth": {"accessToken": "cli"}}))
    oauth._write_anthropic_store({"access_token": "mine"})
    res = oauth.disconnect("anthropic-oauth")
    assert res.removed and not res.revoked
    with pytest.raises(oauth.OAuthCredentialError):
        oauth.resolve_anthropic_oauth()


def test_disconnect_anthropic_suppresses_keychain(monkeypatch):
    monkeypatch.setattr(
        oauth, "_read_claude_keychain", lambda: {"claudeAiOauth": {"accessToken": "tok-kc"}}
    )
    oauth.disconnect("anthropic-oauth")
    with pytest.raises(oauth.OAuthCredentialError):
        oauth.resolve_anthropic_oauth()


def test_env_token_wins_over_disconnect(monkeypatch):
    oauth.disconnect("anthropic-oauth")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "explicit")
    assert oauth.resolve_anthropic_oauth().access_token == "explicit"


# --- TTL-cached resolution --------------------------------------------------


def test_cached_resolve_rereads_at_most_every_ttl(monkeypatch):
    oauth._write_anthropic_store({"access_token": "mine", "expires_in": 3600})
    calls = []
    real = oauth.resolve_anthropic_oauth

    def _counted():
        calls.append(1)
        return real()

    monkeypatch.setattr(oauth, "resolve_anthropic_oauth", _counted)
    assert oauth.resolve_anthropic_oauth_cached().access_token == "mine"
    assert oauth.resolve_anthropic_oauth_cached().access_token == "mine"
    assert len(calls) == 1  # second call within the TTL — no re-read
    # force bypasses the cache — for when the cached token is the one that failed
    oauth.resolve_anthropic_oauth_cached(force=True)
    assert len(calls) == 2


def test_signin_and_disconnect_reset_the_resolve_cache():
    oauth._write_anthropic_store({"access_token": "first", "expires_in": 3600})
    assert oauth.resolve_anthropic_oauth_cached().access_token == "first"
    # a fresh sign-in (store write) is visible immediately, not after the TTL
    oauth._write_anthropic_store({"access_token": "second", "expires_in": 3600})
    assert oauth.resolve_anthropic_oauth_cached().access_token == "second"
    # disconnect stops resolution immediately too
    oauth.disconnect("anthropic-oauth")
    with pytest.raises(oauth.OAuthCredentialError):
        oauth.resolve_anthropic_oauth_cached()


def test_cached_resolve_never_caches_a_failure():
    with pytest.raises(oauth.OAuthCredentialError):
        oauth.resolve_anthropic_oauth_cached()
    # a sign-in right after must be picked up on the very next call
    oauth._write_anthropic_store({"access_token": "late", "expires_in": 3600})
    assert oauth.resolve_anthropic_oauth_cached().access_token == "late"


# --- sign-in flows ---------------------------------------------------------


def test_anthropic_login_start_url_is_well_formed():
    flow = oauth_login.anthropic_login_start()
    url = flow["authorize_url"]
    assert url.startswith("https://platform.claude.com/oauth/authorize?")
    for param in ("code_challenge=", "state=", "client_id=", "code_challenge_method=S256"):
        assert param in url
    assert flow["mode"] == "redirect"


def test_anthropic_login_complete_exchanges_and_stores(monkeypatch):
    oauth.disconnect("anthropic-oauth")  # a prior disconnect must be cleared by sign-in
    flow = oauth_login.anthropic_login_start()
    state = oauth_login._FLOWS[flow["flow_id"]].data["state"]

    def _post(url, **kw):
        assert url == oauth._ANTHROPIC_TOKEN_URL
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"access_token": "minted", "refresh_token": "rt", "expires_in": 3600},
        )

    monkeypatch.setattr(oauth_login.httpx, "post", _post)
    res = oauth_login.anthropic_login_complete(flow["flow_id"], f"code123#{state}")
    assert res["status"] == "complete"
    assert oauth.resolve_anthropic_oauth().access_token == "minted"
    assert not oauth.is_disconnected("anthropic-oauth")


def test_anthropic_login_complete_rejects_state_mismatch():
    flow = oauth_login.anthropic_login_start()
    res = oauth_login.anthropic_login_complete(flow["flow_id"], "code123#wrong-state")
    assert res["status"] == "error"
    assert "mismatch" in res["error"]


def test_codex_login_poll_pending_then_complete(monkeypatch):
    monkeypatch.setattr(
        oauth_login.httpx, "post",
        lambda url, **kw: SimpleNamespace(
            status_code=200,
            json=lambda: {"user_code": "ABCD-1234", "device_auth_id": "dev-1", "interval": 5},
        ),
    )
    flow = oauth_login.codex_login_start()
    assert flow["mode"] == "device"
    assert flow["user_code"] == "ABCD-1234"

    # first poll: not yet approved
    monkeypatch.setattr(
        oauth_login.httpx, "post", lambda url, **kw: SimpleNamespace(status_code=403, json=dict)
    )
    assert oauth_login.codex_login_poll(flow["flow_id"])["status"] == "pending"

    # second poll: approved → token exchange → device-login provenance stored
    def _post(url, **kw):
        if url.endswith("/deviceauth/token"):
            return SimpleNamespace(
                status_code=200,
                json=lambda: {"authorization_code": "ac", "code_verifier": "cv"},
            )
        assert url == oauth._CODEX_TOKEN_URL
        return SimpleNamespace(status_code=200, json=lambda: _codex_tokens())

    monkeypatch.setattr(oauth_login.httpx, "post", _post)
    assert oauth_login.codex_login_poll(flow["flow_id"])["status"] == "complete"
    store = oauth._codex_store_path()
    assert oauth._read_codex_provenance(store) == oauth.PROVENANCE_DEVICE_LOGIN


def test_cancel_login_drops_the_pending_flow():
    flow = oauth_login.anthropic_login_start()
    assert oauth_login.cancel_login(flow["flow_id"])["cancelled"] is True
    # the flow is gone — completing raises (the route maps it to an error)
    with pytest.raises(oauth_login.OAuthLoginError):
        oauth_login.anthropic_login_complete(flow["flow_id"], "code#state")
    # idempotent
    assert oauth_login.cancel_login(flow["flow_id"])["cancelled"] is False


# --- discovery / status ----------------------------------------------------


def test_all_oauth_status_covers_every_provider():
    rows = discovery.all_oauth_status()
    assert {r["provider"] for r in rows} == set(oauth.NATIVE_OAUTH_PROVIDERS)
    assert all(r["signed_in"] is False and r["hint"] for r in rows)


def test_oauth_status_signed_in_via_env(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok")
    st = discovery.oauth_status("anthropic-oauth")
    assert st.signed_in and st.source == "env"


def test_oauth_status_reports_cli_codex_credentials():
    _write_cli_codex(_codex_tokens())
    st = discovery.oauth_status("openai-codex")
    assert st.signed_in and st.source == "codex_cli"
    assert "…ct-123" in st.detail


def test_oauth_status_reads_keychain(monkeypatch):
    """Status must see the same sources resolution does — a Keychain-only login
    (the normal macOS case) reads signed-in, not 'sign in below'."""
    monkeypatch.setattr(
        oauth, "_read_claude_keychain",
        lambda: {"claudeAiOauth": {"accessToken": "tok-kc", "subscriptionType": "max"}},
    )
    st = discovery.oauth_status("anthropic-oauth")
    assert st.signed_in and st.source == "keychain"
    assert "max" in st.detail


# --- adapters --------------------------------------------------------------

from voice.llm.anthropic_oauth import (  # noqa: E402
    CLAUDE_CODE_SYSTEM_PREFIX,
    OAUTH_BETAS,
    AnthropicOAuthLLMService,
    _with_identity_prefix,
)
from voice.llm.openai_codex import CodexLLMService  # noqa: E402


def _anthropic_creds(**kw):
    return oauth.AnthropicOAuthCreds(access_token=kw.get("token", "tok-a"), source="env")


def _codex_creds(**kw):
    return oauth.CodexOAuthCreds(
        access_token=kw.get("token", "tok-c"),
        account_id=kw.get("account", "acct-9"),
        base_url="https://chatgpt.com/backend-api/codex",
        source="instance_store",
    )


@pytest.fixture
def anthropic_llm(monkeypatch):
    import voice.llm.anthropic_oauth as mod

    monkeypatch.setattr(mod, "resolve_anthropic_oauth_cached", lambda: _anthropic_creds())
    return make_llm(
        base_url="https://api.anthropic.com", model="claude-sonnet-4-5", api_key="",
        settings=OpenAILLMService.Settings(model="claude-sonnet-4-5", max_tokens=1024),
        provider="anthropic-oauth",
    )


@pytest.fixture
def codex_llm(monkeypatch):
    import voice.llm.openai_codex as mod

    monkeypatch.setattr(mod, "resolve_codex_oauth", lambda: _codex_creds())
    return make_llm(
        base_url="https://chatgpt.com/backend-api/codex", model="gpt-5-codex", api_key="",
        settings=OpenAILLMService.Settings(model="gpt-5-codex"),
        provider="openai-codex",
    )


def test_make_llm_dispatches_anthropic_oauth_with_bearer(anthropic_llm):
    assert isinstance(anthropic_llm, AnthropicOAuthLLMService)
    client = anthropic_llm._client
    assert client.auth_token == "tok-a"
    assert client.api_key is None  # x-api-key must never be sent
    headers = client.default_headers
    assert headers["anthropic-beta"] == ",".join(OAUTH_BETAS)
    assert headers["User-Agent"].startswith("claude-code/")
    # run_inference path: the merged betas ride settings.extra
    assert set(OAUTH_BETAS) <= set(anthropic_llm._settings.extra["betas"])
    # temperature deliberately not forwarded (current Claude models reject it)
    assert not isinstance(anthropic_llm._settings.temperature, float)


def test_anthropic_stream_path_merges_oauth_betas(anthropic_llm):
    import asyncio

    seen = {}

    async def _api_call(**params):
        seen.update(params)
        return "stream"

    params = {"betas": ["interleaved-thinking-2025-05-14"], "messages": []}
    out = asyncio.run(anthropic_llm._create_message_stream(_api_call, params))
    assert out == "stream"
    assert set(OAUTH_BETAS) <= set(seen["betas"])
    assert "interleaved-thinking-2025-05-14" in seen["betas"]


def test_refresh_auth_keeps_the_token_in_hand_on_transient_failure(anthropic_llm, monkeypatch):
    import asyncio

    import voice.llm.anthropic_oauth as mod

    def _raise():
        raise oauth.OAuthCredentialError("store hiccup", provider="anthropic-oauth")

    monkeypatch.setattr(mod, "resolve_anthropic_oauth_cached", _raise)
    # with a live token, a transient resolve failure must not kill the turn
    anthropic_llm._client.auth_token = "tok-live"
    asyncio.run(anthropic_llm._refresh_auth())
    assert anthropic_llm._client.auth_token == "tok-live"
    # signed-out there is nothing to keep — the raise IS the sign-in UX
    anthropic_llm._client.auth_token = mod._SIGNED_OUT_SENTINEL
    with pytest.raises(oauth.OAuthCredentialError):
        asyncio.run(anthropic_llm._refresh_auth())


def test_make_llm_dispatches_codex_responses(codex_llm):
    assert isinstance(codex_llm, CodexLLMService)
    assert codex_llm._client.api_key == "tok-c"
    assert str(codex_llm._client.base_url).startswith("https://chatgpt.com/backend-api/codex")
    headers = codex_llm._client.default_headers
    assert headers["ChatGPT-Account-Id"] == "acct-9"
    assert headers["originator"] == "codex_cli_rs"


def test_codex_params_fold_system_into_instructions_and_obey_backend_rules(codex_llm):
    params = codex_llm._build_response_params({
        "input": [
            {"role": "developer", "content": "persona prompt"},
            {"role": "user", "content": "hi"},
        ],
        "tools": None,
    })
    assert params["store"] is False
    assert params["stream"] is True
    assert "max_output_tokens" not in params
    assert "persona prompt" in params["instructions"]
    assert params["input"] == [{"role": "user", "content": "hi"}]


def test_codex_run_inference_streams(codex_llm, monkeypatch):
    """The base class's run_inference forces stream=False, which the Codex
    backend rejects — the override must stream and aggregate."""
    import asyncio

    from pipecat.processors.aggregators.llm_context import LLMContext

    seen = {}

    class _FakeStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

        async def close(self):
            pass

    async def _create(**params):
        seen.update(params)
        return _FakeStream()

    monkeypatch.setattr(codex_llm._client.responses, "create", _create)
    out = asyncio.run(codex_llm.run_inference(LLMContext([{"role": "user", "content": "sum this"}])))
    assert out is None  # empty fake stream
    assert seen["stream"] is True
    assert seen["store"] is False


def test_oauth_adapters_reject_gateway_aliases(monkeypatch):
    import voice.llm.anthropic_oauth as amod
    import voice.llm.openai_codex as cmod

    monkeypatch.setattr(amod, "resolve_anthropic_oauth_cached", lambda: _anthropic_creds())
    monkeypatch.setattr(cmod, "resolve_codex_oauth", lambda: _codex_creds())
    for provider in ("anthropic-oauth", "openai-codex"):
        with pytest.raises(RuntimeError, match="not a"):
            make_llm(
                base_url="", model="protolabs/fast", api_key="",
                settings=OpenAILLMService.Settings(model="protolabs/fast"),
                provider=provider,
            )


IDENTITY_BLOCK = {"type": "text", "text": CLAUDE_CODE_SYSTEM_PREFIX}


def test_identity_prefix_shapes():
    # a string system ALWAYS becomes a block list — exact identity block first
    out = _with_identity_prefix("be an orb")
    assert out == [IDENTITY_BLOCK, {"type": "text", "text": "be an orb"}]
    # idempotent
    assert _with_identity_prefix(out) == out
    # the old merged-string shape is REPAIRED into exact blocks, not skipped
    merged = f"{CLAUDE_CODE_SYSTEM_PREFIX}\n\npersona"
    assert _with_identity_prefix(merged) == [IDENTITY_BLOCK, {"type": "text", "text": "persona"}]
    # block-list shape (cache_control blocks survive, exact prefix block leads)
    blocks = [{"type": "text", "text": "persona", "cache_control": {"type": "ephemeral"}}]
    out = _with_identity_prefix(blocks)
    assert out[0] == IDENTITY_BLOCK
    assert out[1] is blocks[0]
    assert _with_identity_prefix(out) == out
    # a first block that STARTS with the line but carries more is split —
    # cache_control moves to the remainder, never onto the one-line anchor
    fat = [{"type": "text", "text": merged, "cache_control": {"type": "ephemeral"}}]
    assert _with_identity_prefix(fat) == [
        IDENTITY_BLOCK,
        {"type": "text", "text": "persona", "cache_control": {"type": "ephemeral"}},
    ]
    # no system at all → the identity block IS the system
    assert _with_identity_prefix(None) == [IDENTITY_BLOCK]


# --- tool-loop guard wiring (mirrors test_tool_loop's backend section) ------


def _round_trip(i: int):
    cid = f"call_{i}"
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": cid, "type": "function",
                 "function": {"name": "check_status", "arguments": '{"id": "7"}'}},
            ],
        },
        {"role": "tool", "tool_call_id": cid, "content": "still pending"},
    ]


def _stalled_context():
    from pipecat.adapters.schemas.function_schema import FunctionSchema
    from pipecat.adapters.schemas.tools_schema import ToolsSchema
    from pipecat.processors.aggregators.llm_context import LLMContext

    msgs: list = [
        {"role": "system", "content": "you are orb"},
        {"role": "user", "content": "status?"},
    ]
    for i in range(3):
        msgs += _round_trip(i)
    tools = ToolsSchema(standard_tools=[
        FunctionSchema(name="check_status", description="d", properties={}, required=[]),
    ])
    return LLMContext(msgs, tools=tools)


def test_anthropic_adapter_applies_the_guard(anthropic_llm):
    params = anthropic_llm.get_llm_adapter().get_llm_invocation_params(
        _stalled_context(), enable_prompt_caching=False, system_instruction=None
    )
    # identity is the exact first system block
    assert params["system"][0] == IDENTITY_BLOCK
    # 3 identical round-trips = STOP: note appended + tools off, natively
    last = params["messages"][-1]
    assert last["role"] == "user"
    assert "out loud" in str(last["content"])
    assert params["tool_choice"] == {"type": "none"}


def test_codex_adapter_applies_the_guard(codex_llm):
    codex_llm._guard_context = _stalled_context()
    try:
        params = codex_llm._build_response_params({
            "input": [{"role": "user", "content": "status?"}],
            "tools": [{"type": "function", "name": "check_status"}],
        })
    finally:
        codex_llm._guard_context = None
    assert params["input"][-1]["role"] == "user"
    assert "out loud" in str(params["input"][-1]["content"])
    assert params["tool_choice"] == "none"


def test_signed_out_boot_does_not_kill_the_session():
    """A missing/disconnected credential must not raise at pipeline build
    (mirrors protoAgent #2475) — the per-turn resolve raises instead, which
    the service maps to an ErrorFrame the announcer speaks."""
    a = make_llm(
        base_url="https://api.anthropic.com", model="claude-sonnet-4-5", api_key="",
        settings=OpenAILLMService.Settings(model="claude-sonnet-4-5"),
        provider="anthropic-oauth",
    )
    assert a._client.auth_token == "signed-out"
    c = make_llm(
        base_url="", model="gpt-5-codex", api_key="",
        settings=OpenAILLMService.Settings(model="gpt-5-codex"),
        provider="openai-codex",
    )
    assert c._client.api_key == "signed-out"
    assert "ChatGPT-Account-Id" not in c._client.default_headers


def test_codex_mid_session_sign_in_installs_account_header(codex_llm, monkeypatch):
    """After a sign-in, the next turn must carry the fresh token AND the
    account header a signed-out boot omitted."""
    import asyncio

    import voice.llm.openai_codex as mod

    codex_llm._client._custom_headers.pop("ChatGPT-Account-Id", None)
    monkeypatch.setattr(
        mod, "resolve_codex_oauth", lambda: _codex_creds(token="tok-new", account="acct-new")
    )

    async def _no_call(context):
        pass

    monkeypatch.setattr(
        "pipecat.services.openai.responses.llm.OpenAIResponsesHttpLLMService._process_context",
        lambda self, context: _no_call(context),
    )
    asyncio.run(codex_llm._process_context(SimpleNamespace()))
    assert codex_llm._client.api_key == "tok-new"
    assert codex_llm._client._custom_headers["ChatGPT-Account-Id"] == "acct-new"


def test_default_gateway_path_is_untouched():
    from voice.llm.guarded import GuardedOpenAILLMService

    svc = make_llm(
        base_url="https://gw/v1", model="m", api_key="k",
        settings=OpenAILLMService.Settings(model="m"),
    )
    assert isinstance(svc, GuardedOpenAILLMService)
