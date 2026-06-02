"""Tests for offline license verification + the entitlement gate.

Licenses are Ed25519-signed tokens verified against a public key baked into
the build (no Stripe secret, no network). Tests use an ephemeral keypair and
wire its public half in via ORBIS_LICENSE_PUBKEY.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization as ser
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent import entitlement
from agent import license as lic
from memory import Memory


# --- fixtures ----------------------------------------------------------------


@pytest.fixture
def mem(tmp_path: Path) -> Memory:
    return Memory(tmp_path / "orbis.sqlite")


@pytest.fixture
def signer(monkeypatch: pytest.MonkeyPatch) -> Ed25519PrivateKey:
    """Ephemeral signing key; its public half is wired in as the app's
    verification key via ORBIS_LICENSE_PUBKEY (raw base64url)."""
    priv = Ed25519PrivateKey.generate()
    raw_pub = priv.public_key().public_bytes(ser.Encoding.Raw, ser.PublicFormat.Raw)
    monkeypatch.setenv(
        "ORBIS_LICENSE_PUBKEY",
        base64.urlsafe_b64encode(raw_pub).decode().rstrip("="),
    )
    return priv


def _license(
    priv: Ed25519PrivateKey,
    *,
    feat: str = "customization",
    sub: str = "buyer@example.com",
    lid: str = "lid-1",
) -> str:
    return lic.sign_license(
        {"v": 1, "feat": feat, "sub": sub, "lid": lid, "iat": 1_700_000_000}, priv
    )


def _raw_pubkey_b64(priv: Ed25519PrivateKey) -> str:
    raw = priv.public_key().public_bytes(ser.Encoding.Raw, ser.PublicFormat.Raw)
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


# --- license.py: verification -----------------------------------------------


def test_sign_verify_roundtrip(signer: Ed25519PrivateKey):
    token = _license(signer)
    assert token.startswith("ORBIS-")
    payload = lic.verify_license(token)
    assert payload["feat"] == "customization"
    assert payload["sub"] == "buyer@example.com"


def test_tampered_payload_rejected(signer: Ed25519PrivateKey):
    token = _license(signer)
    seg, sig = token[len(lic.TOKEN_PREFIX) :].split(".", 1)
    flipped = seg[:-1] + ("A" if seg[-1] != "A" else "B")
    with pytest.raises(lic.LicenseError):
        lic.verify_license(f"{lic.TOKEN_PREFIX}{flipped}.{sig}")


def test_wrong_key_rejected(signer: Ed25519PrivateKey, monkeypatch: pytest.MonkeyPatch):
    token = _license(signer)
    monkeypatch.setenv(
        "ORBIS_LICENSE_PUBKEY", _raw_pubkey_b64(Ed25519PrivateKey.generate())
    )
    with pytest.raises(lic.LicenseError):
        lic.verify_license(token)


def test_non_orbis_token_rejected(signer: Ed25519PrivateKey):
    with pytest.raises(lic.LicenseError):
        lic.verify_license("not-a-license")


def test_pem_public_key_supported(monkeypatch: pytest.MonkeyPatch):
    priv = Ed25519PrivateKey.generate()
    pem = (
        priv.public_key()
        .public_bytes(ser.Encoding.PEM, ser.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    monkeypatch.setenv("ORBIS_LICENSE_PUBKEY", pem)
    token = lic.sign_license({"v": 1, "feat": "customization"}, priv)
    assert lic.verify_license(token)["feat"] == "customization"


# --- entitlement gate -------------------------------------------------------


def test_open_gate_unlocked_without_license(mem: Memory, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(entitlement, "GATE_MODE", "open")
    assert entitlement.has_customization(mem) is True
    state = entitlement.entitlement_state(mem)["customization"]
    assert state["active"] is True
    assert state["licensed"] is False


def test_closed_gate_locked_without_license(mem: Memory, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(entitlement, "GATE_MODE", "closed")
    assert entitlement.has_customization(mem) is False


def test_activate_unlocks_closed_gate(
    mem: Memory, signer: Ed25519PrivateKey, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(entitlement, "GATE_MODE", "closed")
    state = entitlement.activate_license(mem, _license(signer))["customization"]
    assert state["active"] is True
    assert state["licensed"] is True
    assert state["sub"] == "buyer@example.com"
    assert state["lid"] == "lid-1"
    assert entitlement.has_customization(mem) is True


def test_activate_wrong_feature_rejected(
    mem: Memory, signer: Ed25519PrivateKey, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(entitlement, "GATE_MODE", "closed")
    with pytest.raises(entitlement.EntitlementError):
        entitlement.activate_license(mem, _license(signer, feat="something-else"))
    assert entitlement.has_customization(mem) is False


def test_activate_invalid_key_rejected(
    mem: Memory, signer: Ed25519PrivateKey, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(entitlement, "GATE_MODE", "closed")
    with pytest.raises(entitlement.EntitlementError):
        entitlement.activate_license(mem, "ORBIS-garbage.signature")
    assert entitlement.has_customization(mem) is False


def test_deactivate_removes_license(
    mem: Memory, signer: Ed25519PrivateKey, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(entitlement, "GATE_MODE", "closed")
    entitlement.activate_license(mem, _license(signer))
    assert entitlement.has_customization(mem) is True
    entitlement.deactivate(mem)
    assert entitlement.has_customization(mem) is False


def test_stored_license_treated_absent_when_key_rotates(
    mem: Memory, signer: Ed25519PrivateKey, monkeypatch: pytest.MonkeyPatch
):
    """A stored key that no longer verifies (build's public key rotated) is
    treated as absent rather than crashing the gate."""
    monkeypatch.setattr(entitlement, "GATE_MODE", "closed")
    entitlement.activate_license(mem, _license(signer))
    assert entitlement.has_customization(mem) is True
    monkeypatch.setenv(
        "ORBIS_LICENSE_PUBKEY", _raw_pubkey_b64(Ed25519PrivateKey.generate())
    )
    assert entitlement.has_customization(mem) is False
