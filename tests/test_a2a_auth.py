"""Tests for the inbound /a2a auth gate.

Covers the new safer default: when A2A_AUTH_TOKEN is unset, requests
are rejected unless A2A_ALLOW_UNAUTH=1 is explicitly set. Old default
was "accept anonymous" which left the endpoint open whenever a deployer
forgot the token.
"""

from __future__ import annotations

import importlib

from starlette.requests import Request


def _make_request(headers: dict[str, str] | None = None) -> Request:
    """Build a minimal Starlette Request with the given headers."""
    raw = []
    for k, v in (headers or {}).items():
        raw.append((k.lower().encode("latin-1"), v.encode("latin-1")))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/a2a",
        "headers": raw,
        "query_string": b"",
    }
    return Request(scope)


def test_auth_rejects_anonymous_when_no_token_configured(monkeypatch):
    """The new safe default — no token, no explicit opt-in → 401."""
    monkeypatch.delenv("A2A_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("A2A_ALLOW_UNAUTH", raising=False)
    from a2a import server
    importlib.reload(server)
    assert server._auth_ok(_make_request()) is False


def test_auth_accepts_anonymous_with_explicit_opt_in(monkeypatch):
    """Local dev / lab use case — A2A_ALLOW_UNAUTH=1 keeps the legacy
    open-by-default behaviour available behind an explicit knob."""
    monkeypatch.delenv("A2A_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("A2A_ALLOW_UNAUTH", "1")
    from a2a import server
    importlib.reload(server)
    assert server._auth_ok(_make_request()) is True


def test_auth_accepts_x_api_key_match(monkeypatch):
    monkeypatch.setenv("A2A_AUTH_TOKEN", "shhh")
    monkeypatch.delenv("A2A_ALLOW_UNAUTH", raising=False)
    from a2a import server
    importlib.reload(server)
    assert server._auth_ok(_make_request({"X-API-Key": "shhh"})) is True


def test_auth_accepts_bearer_match(monkeypatch):
    monkeypatch.setenv("A2A_AUTH_TOKEN", "shhh")
    monkeypatch.delenv("A2A_ALLOW_UNAUTH", raising=False)
    from a2a import server
    importlib.reload(server)
    assert (
        server._auth_ok(_make_request({"Authorization": "Bearer shhh"})) is True
    )


def test_auth_rejects_wrong_token(monkeypatch):
    monkeypatch.setenv("A2A_AUTH_TOKEN", "shhh")
    monkeypatch.delenv("A2A_ALLOW_UNAUTH", raising=False)
    from a2a import server
    importlib.reload(server)
    assert server._auth_ok(_make_request({"X-API-Key": "wrong"})) is False
    assert (
        server._auth_ok(_make_request({"Authorization": "Bearer wrong"})) is False
    )


def test_auth_rejects_missing_headers_when_token_configured(monkeypatch):
    """Token configured + caller sends nothing → 401, even though the
    "no token" branch would have accepted anonymous (with opt-in)."""
    monkeypatch.setenv("A2A_AUTH_TOKEN", "shhh")
    monkeypatch.setenv("A2A_ALLOW_UNAUTH", "1")  # Should not matter when token is set
    from a2a import server
    importlib.reload(server)
    assert server._auth_ok(_make_request()) is False


def test_allow_unauth_accepts_truthy_variants(monkeypatch):
    """`true`, `yes`, `1` should all opt-in. Other strings shouldn't."""
    monkeypatch.delenv("A2A_AUTH_TOKEN", raising=False)
    for val in ("1", "true", "TRUE", "yes", "Yes"):
        monkeypatch.setenv("A2A_ALLOW_UNAUTH", val)
        from a2a import server
        importlib.reload(server)
        assert server.A2A_ALLOW_UNAUTH is True, f"value {val!r} should opt in"

    for val in ("0", "false", "no", "", "anything-else"):
        monkeypatch.setenv("A2A_ALLOW_UNAUTH", val)
        from a2a import server
        importlib.reload(server)
        assert server.A2A_ALLOW_UNAUTH is False, f"value {val!r} should NOT opt in"
