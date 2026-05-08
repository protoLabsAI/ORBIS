"""Tests for the entitlement module's plumbing.

Covers configuration gating, the refresh no-op path, and the
webhook-event → entitlement-cache write/revoke. Real Stripe calls are
mocked — we care about the glue, not Stripe's API.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from memory import Memory


# --- fixtures ----------------------------------------------------------------


@pytest.fixture
def mem(tmp_path: Path) -> Memory:
    return Memory(tmp_path / "orbis.sqlite")


@pytest.fixture
def configured_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_fake")
    monkeypatch.setenv("STRIPE_PRICE_CUSTOMIZATION", "price_fake")


# --- configuration gating ---------------------------------------------------


def test_configured_returns_false_without_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("STRIPE_PRICE_CUSTOMIZATION", raising=False)
    # Force a re-read of the module-level constants.
    import importlib
    from agent import entitlement
    importlib.reload(entitlement)
    assert entitlement.configured() is False


def test_configured_returns_true_with_env(
    monkeypatch: pytest.MonkeyPatch, configured_env,
):
    import importlib
    from agent import entitlement
    importlib.reload(entitlement)
    assert entitlement.configured() is True


# --- has_customization gate behavior ---------------------------------------


def test_has_customization_open_in_dev_mode(
    mem: Memory, monkeypatch: pytest.MonkeyPatch,
):
    """Unconfigured Stripe → customization is open (dev mode).
    Ships with a sane default so local dev doesn't need commerce set up."""
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("STRIPE_PRICE_CUSTOMIZATION", raising=False)
    import importlib
    from agent import entitlement
    importlib.reload(entitlement)
    assert entitlement.has_customization(mem) is True


def test_has_customization_gated_when_configured(
    mem: Memory, configured_env,
):
    """Configured Stripe + no cached entitlement → gate closed."""
    import importlib
    from agent import entitlement
    importlib.reload(entitlement)
    assert entitlement.has_customization(mem) is False


def test_has_customization_open_when_cache_active(
    mem: Memory, configured_env,
):
    import importlib
    from agent import entitlement
    importlib.reload(entitlement)
    from datetime import datetime, timedelta, timezone
    future = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()
    mem.entitlement.set("customization", "active", expires_at=future)
    assert entitlement.has_customization(mem) is True


# --- ORBIS_GATE distribution policy -----------------------------------------


def test_orbis_gate_closed_locks_when_unconfigured(
    mem: Memory, monkeypatch: pytest.MonkeyPatch,
):
    """ORBIS_GATE=closed + Stripe unconfigured → gate stays shut.
    The right setting for a public distribution that intends to monetise
    the unlock but ships without Stripe credentials baked in."""
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("STRIPE_PRICE_CUSTOMIZATION", raising=False)
    monkeypatch.setenv("ORBIS_GATE", "closed")
    import importlib
    from agent import entitlement
    importlib.reload(entitlement)
    assert entitlement.GATE_MODE == "closed"
    assert entitlement.has_customization(mem) is False


def test_orbis_gate_open_default_unlocks_when_unconfigured(
    mem: Memory, monkeypatch: pytest.MonkeyPatch,
):
    """ORBIS_GATE unset (default 'open') preserves the dev-mode unlock —
    no behaviour change for existing local-dev setups."""
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("STRIPE_PRICE_CUSTOMIZATION", raising=False)
    monkeypatch.delenv("ORBIS_GATE", raising=False)
    import importlib
    from agent import entitlement
    importlib.reload(entitlement)
    assert entitlement.GATE_MODE == "open"
    assert entitlement.has_customization(mem) is True


def test_orbis_gate_unknown_value_falls_back_to_open(
    monkeypatch: pytest.MonkeyPatch, caplog,
):
    """A typo in ORBIS_GATE shouldn't silently lock an install — log a
    warning and preserve the safer (current-behaviour) default."""
    monkeypatch.setenv("ORBIS_GATE", "yarp")
    import importlib
    from agent import entitlement
    with caplog.at_level("WARNING"):
        importlib.reload(entitlement)
    assert entitlement.GATE_MODE == "open"
    assert any("ORBIS_GATE='yarp'" in r.message for r in caplog.records)


def test_orbis_gate_does_not_override_active_paid_cache(
    mem: Memory, configured_env, monkeypatch: pytest.MonkeyPatch,
):
    """When Stripe IS configured, ORBIS_GATE has no effect — the cache
    is the source of truth. A 'closed' gate doesn't lock out a user
    who's actually paid; an 'open' gate doesn't unlock a user who hasn't."""
    monkeypatch.setenv("ORBIS_GATE", "closed")
    import importlib
    from agent import entitlement
    importlib.reload(entitlement)
    from datetime import datetime, timedelta, timezone
    future = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()
    mem.entitlement.set("customization", "active", expires_at=future)
    assert entitlement.has_customization(mem) is True


def test_entitlement_state_exposes_gate_mode(
    mem: Memory, monkeypatch: pytest.MonkeyPatch,
):
    """The /api/entitlement payload carries gate_mode so the UI can
    distinguish 'locked by policy' from 'unconfigured, dev-open'."""
    monkeypatch.setenv("ORBIS_GATE", "closed")
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    import importlib
    from agent import entitlement
    importlib.reload(entitlement)
    state = entitlement.entitlement_state(mem)
    assert state["customization"]["gate_mode"] == "closed"
    assert state["customization"]["active"] is False
    assert state["customization"]["configured"] is False


# --- refresh_from_stripe ----------------------------------------------------


def test_refresh_from_stripe_noop_when_unconfigured(
    mem: Memory, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    import importlib
    from agent import entitlement
    importlib.reload(entitlement)
    result = entitlement.refresh_from_stripe(mem)
    assert result["ok"] is False
    assert result["reason"] == "unconfigured"


# --- webhook handling -------------------------------------------------------


def test_webhook_raises_when_unconfigured(
    mem: Memory, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    import importlib
    from agent import entitlement
    importlib.reload(entitlement)
    with pytest.raises(entitlement.EntitlementError):
        entitlement.handle_webhook_event(b"{}", "sig", mem)


def test_webhook_grants_entitlement_on_checkout_completed(
    mem: Memory, configured_env,
):
    """Simulate a 'checkout.session.completed' webhook — entitlement
    cache should be populated with a future expiry."""
    import importlib
    from agent import entitlement
    importlib.reload(entitlement)

    # Mock stripe.Webhook.construct_event to avoid needing real signatures.
    with patch.object(entitlement, "_stripe") as mock_stripe_fn:
        mock_stripe = mock_stripe_fn.return_value
        mock_stripe.Webhook.construct_event.return_value = {
            "type": "checkout.session.completed",
            "data": {},
        }
        result = entitlement.handle_webhook_event(b"payload", "sig", mem)

    assert result["action"] == "granted"
    assert mem.entitlement.is_active("customization")


def test_webhook_revokes_on_refund(mem: Memory, configured_env):
    import importlib
    from datetime import datetime, timedelta, timezone
    from agent import entitlement
    importlib.reload(entitlement)

    # Seed an active entitlement.
    future = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()
    mem.entitlement.set("customization", "active", expires_at=future)
    assert mem.entitlement.is_active("customization")

    with patch.object(entitlement, "_stripe") as mock_stripe_fn:
        mock_stripe = mock_stripe_fn.return_value
        mock_stripe.Webhook.construct_event.return_value = {
            "type": "charge.refunded", "data": {},
        }
        result = entitlement.handle_webhook_event(b"payload", "sig", mem)

    assert result["action"] == "revoked"
    assert not mem.entitlement.is_active("customization")


def test_webhook_ignores_unrelated_events(mem: Memory, configured_env):
    import importlib
    from agent import entitlement
    importlib.reload(entitlement)

    with patch.object(entitlement, "_stripe") as mock_stripe_fn:
        mock_stripe = mock_stripe_fn.return_value
        mock_stripe.Webhook.construct_event.return_value = {
            "type": "customer.updated", "data": {},
        }
        result = entitlement.handle_webhook_event(b"payload", "sig", mem)

    assert result["action"] == "ignored"
    assert not mem.entitlement.is_active("customization")


# --- entitlement_state response shape ----------------------------------------


def test_entitlement_state_shape(mem: Memory, configured_env):
    import importlib
    from agent import entitlement
    importlib.reload(entitlement)
    state = entitlement.entitlement_state(mem)
    assert "customization" in state
    assert state["customization"]["active"] is False
    assert state["customization"]["configured"] is True


def test_entitlement_state_dev_mode_active(
    mem: Memory, monkeypatch: pytest.MonkeyPatch,
):
    """Unconfigured Stripe → state reports active=True so the UI mirrors
    the open-by-default gate (has_customization)."""
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("STRIPE_PRICE_CUSTOMIZATION", raising=False)
    import importlib
    from agent import entitlement
    importlib.reload(entitlement)
    state = entitlement.entitlement_state(mem)
    assert state["customization"]["active"] is True
    assert state["customization"]["configured"] is False
