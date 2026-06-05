"""Cross-language interop: a license minted by the Cloudflare Worker (Node /
Web Crypto, sites/license-issuer/src/license.js) must verify in the app's
offline verifier (agent/license.py). This pins the token contract across the
Python<->JS boundary so a Worker-issued key actually unlocks the paid feature.

Skipped when `node` isn't on PATH (e.g. a minimal CI image)."""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent.entitlement import FEATURE
from agent.license import LicenseError, _parse_public_key, verify_license

ROOT = Path(__file__).resolve().parent.parent
SIGN_MJS = ROOT / "sites" / "license-issuer" / "test" / "sign.mjs"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available"
)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _mint_via_node(private_jwk: dict, opts: dict) -> str:
    payload = json.dumps({"privateJwk": private_jwk, "opts": opts})
    proc = subprocess.run(
        ["node", str(SIGN_MJS)],
        input=payload,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise AssertionError(f"node signer failed: {proc.stderr}")
    return proc.stdout.strip()


def _keypair():
    priv = Ed25519PrivateKey.generate()
    seed = priv.private_bytes_raw()
    pub = priv.public_key().public_bytes_raw()
    private_jwk = {"kty": "OKP", "crv": "Ed25519", "d": _b64url(seed), "x": _b64url(pub)}
    return private_jwk, pub


def test_worker_minted_license_verifies_in_app():
    private_jwk, pub = _keypair()
    token = _mint_via_node(
        private_jwk,
        {"sub": "buyer@example.com", "lid": "lid-abc-123", "iat": 1700000000},
    )

    assert token.startswith("ORBIS-")
    payload = verify_license(token, _parse_public_key(_b64url(pub)))
    assert payload["v"] == 1
    assert payload["feat"] == FEATURE  # entitlement.activate_license requires this
    assert payload["sub"] == "buyer@example.com"
    assert payload["lid"] == "lid-abc-123"
    assert payload["iat"] == 1700000000


def test_worker_minted_license_rejected_by_wrong_pubkey():
    private_jwk, _ = _keypair()
    token = _mint_via_node(private_jwk, {"sub": "buyer@example.com"})

    _, other_pub = _keypair()
    with pytest.raises(LicenseError):
        verify_license(token, _parse_public_key(_b64url(other_pub)))
