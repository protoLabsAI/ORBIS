"""Entitlement — offline signed-license unlock + local cache.

ORBIS unlocks paid features with an Ed25519-signed license key the buyer
activates locally (see ``agent/license.py``). There is no Stripe secret in the
app, no webhook, and no required network call — the key verifies offline
against a public key baked into the build. Purchase + key issuance happen on
the protoLabs side (hosted Stripe Checkout → ``tools/license-issuer/`` signs a
key with the private half from Infisical → the buyer pastes it into ORBIS).

Flow:

1. Buyer purchases via the hosted checkout, receives a license key.
2. User pastes it into ORBIS → POST /api/entitlement/activate.
3. We verify the signature offline; on success we store the key in the
   SQLite ``entitlement_cache`` table. It's perpetual (one-time unlock), so
   the row never expires; ``has_customization`` re-verifies the stored key's
   signature each call, so the entitlement is cryptographic, not a trust flag.

Gate (``ORBIS_GATE``):
    "open"   — every install gets full customization (dev/homelab default;
               keeps things unblocked when no paywall is wired up).
    "closed" — customization requires a verified license. The setting a
               monetized distribution ships with.

Cache key used:
    customization — the paid orb-customization unlock.
"""

from __future__ import annotations

import logging
import os

from agent.license import LicenseError, verify_license

logger = logging.getLogger(__name__)

# The single paid feature today. A license must carry this in its ``feat``.
FEATURE = "customization"

# One-time unlock → the cache row never expires. Far-future sentinel so the
# DAL's ``is_active`` (which compares against ``expires_at``) stays satisfied;
# the real gate is the signature re-check in ``has_customization``.
PERPETUAL_EXPIRY = "9999-12-31T00:00:00+00:00"

# Default behaviour for the customization gate.
#   "open"   — unlocked for everyone (dev/homelab default).
#   "closed" — locked until a verified license is activated.
# Anything else logs a warning and falls back to "open" (a typo shouldn't
# silently lock the install).
_GATE_ENV = os.environ.get("ORBIS_GATE", "open").strip().lower()
if _GATE_ENV not in ("open", "closed"):
    logger.warning(
        f"[entitlement] unknown ORBIS_GATE={_GATE_ENV!r}; expected "
        "'open' or 'closed'. Falling back to 'open'."
    )
    _GATE_ENV = "open"
GATE_MODE = _GATE_ENV  # 'open' | 'closed'


class EntitlementError(Exception):
    """Raised when an entitlement operation cannot complete."""


# ---------------------------------------------------------------------------
# Activation
# ---------------------------------------------------------------------------


def activate_license(mem, token: str) -> dict:
    """Verify a license key offline and, if valid for this feature, store it.

    Returns the post-activation entitlement state. Raises ``EntitlementError``
    (with a user-facing message) if the key is malformed, doesn't verify, or
    isn't for the customization feature.
    """
    try:
        payload = verify_license(token)
    except LicenseError as exc:
        raise EntitlementError(f"Invalid license key: {exc}") from exc

    if payload.get("feat") != FEATURE:
        raise EntitlementError(
            f"This license is for {payload.get('feat')!r}, not {FEATURE!r}."
        )

    # Store the raw key so the gate can re-verify its signature on every check
    # (and so we can show the buyer/license id in the UI).
    mem.entitlement.set(FEATURE, value=token.strip(), expires_at=PERPETUAL_EXPIRY)
    logger.info(
        "[entitlement] customization activated (lid=%s sub=%s)",
        payload.get("lid"),
        payload.get("sub"),
    )
    return entitlement_state(mem)


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


def _stored_license(mem) -> dict | None:
    """Return the verified payload of the stored customization license, or
    None if there's no valid key on file."""
    row = mem.entitlement.get(FEATURE)
    if not row or not row.get("value"):
        return None
    try:
        payload = verify_license(row["value"])
    except LicenseError:
        # A stored key that no longer verifies (e.g. the build's public key
        # rotated) is treated as absent rather than fatal.
        return None
    return payload if payload.get("feat") == FEATURE else None


def has_customization(mem) -> bool:
    """Single-function gate for callers that just need the boolean.

    - ``GATE_MODE == "open"``  → always True (dev/homelab).
    - ``GATE_MODE == "closed"`` → True iff a stored license key verifies.
    """
    if GATE_MODE == "open":
        return True
    return _stored_license(mem) is not None


# ---------------------------------------------------------------------------
# State (for the UI)
# ---------------------------------------------------------------------------


def entitlement_state(mem) -> dict:
    """Return current entitlement state for the UI.

    ``active`` mirrors ``has_customization`` so the UI sees the same gate the
    backend enforces (including the dev-open fallback). ``licensed`` reports
    whether a valid key is actually on file (distinct from open-gate), and
    ``gate_mode`` exposes the ORBIS_GATE setting.
    """
    payload = _stored_license(mem)
    info: dict = {
        "active": has_customization(mem),
        "licensed": payload is not None,
        "gate_mode": GATE_MODE,
    }
    if payload is not None:
        # Non-secret, display-only provenance for "Licensed to …".
        if payload.get("sub"):
            info["sub"] = payload["sub"]
        if payload.get("lid"):
            info["lid"] = payload["lid"]
    return {"customization": info}


def deactivate(mem) -> dict:
    """Remove the stored license (e.g. to move it to another machine).
    Returns the post-removal state."""
    mem.entitlement.clear(FEATURE)
    return entitlement_state(mem)
