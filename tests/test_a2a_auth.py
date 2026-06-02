"""Tests for the A2A auth guard (a2a_auth) — the a2a-sdk migration's middleware.

The SDK advertises security schemes on the card but doesn't enforce them;
``a2a_auth`` is the Starlette middleware that does, with ORBIS's stricter
closed-by-default posture (rejects /a2a unless a token is set or
A2A_ALLOW_UNAUTH=1).
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def auth(monkeypatch):
    monkeypatch.delenv("A2A_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("A2A_ALLOW_UNAUTH", raising=False)
    import a2a_auth as mod
    importlib.reload(mod)
    return mod


def test_closed_by_default_when_no_creds(auth):
    auth.configure(bearer_token="", api_key="", allowed_origins_raw="")
    assert auth._CLOSED[0] is True


def test_open_with_allow_unauth(auth, monkeypatch):
    monkeypatch.setenv("A2A_ALLOW_UNAUTH", "1")
    auth.configure(bearer_token="", api_key="", allowed_origins_raw="")
    assert auth._CLOSED[0] is False


def test_bearer_token_closes_default_and_validates(auth):
    auth.configure(bearer_token="sekret", api_key="", allowed_origins_raw="")
    assert auth._CLOSED[0] is False
    assert auth._BEARER[0] == "sekret"


def test_set_bearer_token_recomputes_closed(auth):
    auth.configure(bearer_token="", api_key="", allowed_origins_raw="")
    assert auth._CLOSED[0] is True
    auth.set_bearer_token("live-token")
    assert auth._CLOSED[0] is False
    auth.set_bearer_token(None)
    assert auth._CLOSED[0] is True  # back to closed when the token is cleared
