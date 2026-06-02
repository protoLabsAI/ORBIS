"""Offline license-key verification (Ed25519).

ORBIS unlocks paid features (currently orb customization) with a signed
license key the buyer activates locally. The key is an Ed25519-signed token
verified against a PUBLIC key baked into the app — no secret ships in the
binary, no network call, no webhook. The matching PRIVATE key lives only on
the protoLabs issuer side (Infisical); see ``tools/license-issuer/``.

Token format (URL-safe, single line, paste-able)::

    ORBIS-<b64url(payload_json)>.<b64url(ed25519_sig)>

``payload_json`` is canonical (sorted keys, no spaces)::

    {"v":1,"feat":"customization","iat":<unix>,"lid":"<uuid>","sub":"<buyer>"}

The signature covers the exact ``<b64url(payload_json)>`` segment bytes, so
verification re-checks the same string it received — no re-serialization.

Public key resolution (release builds inject the production key; the committed
``config/license_pubkey.pem`` is the dev default):

    1. ``ORBIS_LICENSE_PUBKEY``       — PEM text or raw-32-byte base64url
    2. ``ORBIS_LICENSE_PUBKEY_PATH``  — path to a PEM file
    3. bundled ``config/license_pubkey.pem``
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization as _ser
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

TOKEN_PREFIX = "ORBIS-"
SUPPORTED_VERSION = 1

_PUBKEY_PEM_DEFAULT = (
    Path(__file__).resolve().parent.parent / "config" / "license_pubkey.pem"
)


class LicenseError(Exception):
    """Raised when a license token is malformed, unsupported, or its
    signature does not verify."""


def _b64url_decode(segment: str) -> bytes:
    pad = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + pad)


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _parse_public_key(material: str) -> Ed25519PublicKey:
    material = material.strip()
    if "BEGIN PUBLIC KEY" in material:
        key = _ser.load_pem_public_key(material.encode())
    else:
        # Raw 32-byte Ed25519 public key, base64url.
        key = Ed25519PublicKey.from_public_bytes(_b64url_decode(material))
    if not isinstance(key, Ed25519PublicKey):
        raise LicenseError("license public key is not Ed25519")
    return key


def load_public_key() -> Ed25519PublicKey:
    """Load the verification public key (see module docstring for order)."""
    env = os.environ.get("ORBIS_LICENSE_PUBKEY", "").strip()
    if env:
        return _parse_public_key(env)
    path = Path(os.environ.get("ORBIS_LICENSE_PUBKEY_PATH") or _PUBKEY_PEM_DEFAULT)
    try:
        return _parse_public_key(path.read_text())
    except FileNotFoundError as exc:
        raise LicenseError(f"license public key not found at {path}") from exc


def verify_license(token: str, pubkey: Ed25519PublicKey | None = None) -> dict:
    """Verify a license token offline and return its payload dict.

    Raises ``LicenseError`` on any malformed token, bad signature, or
    unsupported version. Does NOT check the feature — callers decide which
    feature a valid license must carry.
    """
    if pubkey is None:
        pubkey = load_public_key()
    body = token.strip()
    if not body.startswith(TOKEN_PREFIX):
        raise LicenseError("not an ORBIS license key")
    body = body[len(TOKEN_PREFIX) :]
    try:
        body_seg, sig_seg = body.split(".", 1)
    except ValueError as exc:
        raise LicenseError("malformed license key (missing signature)") from exc
    try:
        signature = _b64url_decode(sig_seg)
    except Exception as exc:
        raise LicenseError("malformed signature encoding") from exc
    try:
        pubkey.verify(signature, body_seg.encode())
    except InvalidSignature as exc:
        raise LicenseError("license signature does not verify") from exc
    try:
        payload = json.loads(_b64url_decode(body_seg))
    except Exception as exc:
        raise LicenseError("malformed license payload") from exc
    if not isinstance(payload, dict) or payload.get("v") != SUPPORTED_VERSION:
        raise LicenseError("unsupported license version")
    return payload


def sign_license(payload: dict, private_key: Ed25519PrivateKey) -> str:
    """Sign a payload into a license token. Used by the issuer + tests; the
    app never signs (it only has the public key)."""
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    body_seg = _b64url_encode(body)
    signature = private_key.sign(body_seg.encode())
    return f"{TOKEN_PREFIX}{body_seg}.{_b64url_encode(signature)}"
