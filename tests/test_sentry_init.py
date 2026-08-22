"""Tests for the opt-in Sentry crash-reporting hook (#488).

The contract for ``app._init_sentry()``: ``ORBIS_SENTRY_DSN`` unset (or
blank) → no ``sentry_sdk.init`` call, no error; set → ``init`` is called
with exactly that DSN. Shipped builds carry no DSN, so the default path
MUST be a hard no-op — and an exploding SDK must never take boot down
with it (crash reporting can't be a crash source).
"""

from __future__ import annotations

import sys
import types

import app as app_module


def _stub_sentry(monkeypatch):
    """Install a fake ``sentry_sdk`` and return the list of init() calls."""
    calls: list[dict] = []
    stub = types.ModuleType("sentry_sdk")
    stub.init = lambda **kwargs: calls.append(kwargs)
    monkeypatch.setitem(sys.modules, "sentry_sdk", stub)
    return calls


def test_no_dsn_is_a_noop(monkeypatch):
    calls = _stub_sentry(monkeypatch)
    monkeypatch.delenv("ORBIS_SENTRY_DSN", raising=False)

    assert app_module._init_sentry() is False
    assert calls == []


def test_blank_dsn_is_a_noop(monkeypatch):
    # A templated-out launcher var (`ORBIS_SENTRY_DSN=""`, or whitespace)
    # means "not configured", not "initialize with an empty DSN".
    calls = _stub_sentry(monkeypatch)
    monkeypatch.setenv("ORBIS_SENTRY_DSN", "   ")

    assert app_module._init_sentry() is False
    assert calls == []


def test_dsn_initializes_sentry(monkeypatch):
    calls = _stub_sentry(monkeypatch)
    monkeypatch.setenv("ORBIS_SENTRY_DSN", "https://key@o0.ingest.sentry.io/1")

    assert app_module._init_sentry() is True
    assert len(calls) == 1
    assert calls[0]["dsn"] == "https://key@o0.ingest.sentry.io/1"
    # Errors only — no performance tracing on the realtime voice pipeline.
    assert calls[0]["traces_sample_rate"] == 0.0


def test_sdk_failure_never_breaks_boot(monkeypatch):
    stub = types.ModuleType("sentry_sdk")

    def _boom(**kwargs):
        raise RuntimeError("sentry exploded")

    stub.init = _boom
    monkeypatch.setitem(sys.modules, "sentry_sdk", stub)
    monkeypatch.setenv("ORBIS_SENTRY_DSN", "https://key@o0.ingest.sentry.io/1")

    # Swallowed: reported as not-initialized, and no exception escapes.
    assert app_module._init_sentry() is False
