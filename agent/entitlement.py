"""Stripe entitlement — checkout + webhook + local N-day cache.

ORBIS is offline-tolerant: entitlement state lives in the SQLite
``entitlement_cache`` table with an ``expires_at``. We verify with
Stripe on boot + periodically and extend the cache window on success.

Flow:

1. User clicks Unlock in the UI → POST /api/entitlement/checkout
2. Server creates a Stripe Checkout Session, returns its URL
3. User completes payment on Stripe-hosted page
4. Stripe POSTs a webhook event to /api/stripe/webhook
5. We verify the webhook signature, look up the customer, write
   an ``active`` row to the entitlement cache (expires_at = now +
   CACHE_DAYS)
6. On boot + every ``REFRESH_INTERVAL_HOURS``, we re-query Stripe
   for the owner's latest subscription / purchase and refresh the
   cache. If Stripe is unreachable, the local cache window gives
   us up to CACHE_DAYS of tolerance before the user loses access.

Required env vars (absent → endpoints return 503 cleanly):

    STRIPE_SECRET_KEY           sk_live_... or sk_test_...
    STRIPE_WEBHOOK_SECRET       whsec_...
    STRIPE_PRICE_CUSTOMIZATION  price_... (the customization unlock)
    STRIPE_SUCCESS_URL          where to send the user after pay
    STRIPE_CANCEL_URL           where to send the user on cancel

Cache keys used:
    customization — the paid orb-customization unlock. Present and
                    non-expired ⇒ full editor available.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_CUSTOMIZATION = os.environ.get("STRIPE_PRICE_CUSTOMIZATION", "")
STRIPE_SUCCESS_URL = os.environ.get(
    "STRIPE_SUCCESS_URL", "http://localhost:7866/?checkout=success"
)
STRIPE_CANCEL_URL = os.environ.get(
    "STRIPE_CANCEL_URL", "http://localhost:7866/?checkout=cancel"
)

# How long an entitlement stays "active" in the local cache after the
# most recent successful Stripe verification. Default 14 days, enough
# to comfortably survive most offline stretches.
CACHE_DAYS = int(os.environ.get("ENTITLEMENT_CACHE_DAYS", "14"))
# How often we re-query Stripe to extend the cache. Default 24 hours.
REFRESH_INTERVAL_HOURS = int(
    os.environ.get("ENTITLEMENT_REFRESH_INTERVAL_HOURS", "24")
)


class EntitlementError(Exception):
    """Raised when an entitlement operation cannot complete."""


def configured() -> bool:
    """Are the minimum Stripe env vars present?"""
    return bool(
        STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET and STRIPE_PRICE_CUSTOMIZATION
    )


def _expires_in_cache_days() -> str:
    expiry = datetime.now(timezone.utc) + timedelta(days=CACHE_DAYS)
    return expiry.isoformat()


def _stripe():
    """Lazy-import stripe. Raises EntitlementError on missing deps /
    missing env."""
    if not configured():
        raise EntitlementError(
            "Stripe is not configured. Set STRIPE_SECRET_KEY + "
            "STRIPE_WEBHOOK_SECRET + STRIPE_PRICE_CUSTOMIZATION."
        )
    try:
        import stripe  # type: ignore
    except ImportError as exc:
        raise EntitlementError(
            "stripe SDK not installed. `pip install stripe`."
        ) from exc
    stripe.api_key = STRIPE_SECRET_KEY
    return stripe


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------


def create_checkout_session() -> str:
    """Create a Stripe Checkout Session for the customization unlock.
    Returns the session's URL (client redirects the user there)."""
    stripe = _stripe()
    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=[{"price": STRIPE_PRICE_CUSTOMIZATION, "quantity": 1}],
        success_url=STRIPE_SUCCESS_URL,
        cancel_url=STRIPE_CANCEL_URL,
    )
    return session.url


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------


def handle_webhook_event(payload: bytes, signature: str, mem) -> dict:
    """Verify a Stripe webhook payload + signature, and update the
    entitlement cache on relevant events.

    Returns a small dict describing what happened. Raises
    EntitlementError on signature failure or unknown event shape.
    """
    stripe = _stripe()
    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=signature,
            secret=STRIPE_WEBHOOK_SECRET,
        )
    except stripe.error.SignatureVerificationError as exc:
        raise EntitlementError(f"webhook signature failed: {exc}")
    except Exception as exc:
        raise EntitlementError(f"webhook parse failed: {exc}")

    event_type = event.get("type") if isinstance(event, dict) else event["type"]
    logger.info(f"[entitlement] webhook event: {event_type}")

    # The events that grant / refresh entitlement. Expand this when
    # introducing subscriptions or additional products.
    grant_events = {
        "checkout.session.completed",
        "payment_intent.succeeded",
        "invoice.paid",
    }
    revoke_events = {
        "customer.subscription.deleted",
        "charge.refunded",
    }

    if event_type in grant_events:
        mem.entitlement.set(
            "customization",
            value="active",
            expires_at=_expires_in_cache_days(),
        )
        return {"ok": True, "action": "granted", "event": event_type}

    if event_type in revoke_events:
        mem.entitlement.clear("customization")
        return {"ok": True, "action": "revoked", "event": event_type}

    return {"ok": True, "action": "ignored", "event": event_type}


# ---------------------------------------------------------------------------
# Refresh — called from the lifespan + periodically
# ---------------------------------------------------------------------------


def refresh_from_stripe(mem) -> dict:
    """Ask Stripe for the most recent successful payment for the
    configured customization price; if found, extend the cache. If
    Stripe is unreachable, do nothing — the cache window we already
    have absorbs the outage.

    Returns a small dict with the result. Never raises — the refresh
    loop needs to be exception-free.
    """
    if not configured():
        return {"ok": False, "reason": "unconfigured"}

    try:
        stripe = _stripe()
    except EntitlementError as exc:
        return {"ok": False, "reason": str(exc)}

    try:
        # Pull the 10 most recent successful Checkout Sessions; any
        # paid session with our price extends entitlement.
        sessions = stripe.checkout.Session.list(
            limit=10, status="complete", expand=["data.line_items"],
        )
    except Exception as exc:
        logger.info(f"[entitlement] stripe refresh call failed: {exc}")
        return {"ok": False, "reason": f"stripe error: {exc}"}

    for sess in sessions.auto_paging_iter() if hasattr(sessions, "auto_paging_iter") else sessions.data:
        line_items = getattr(sess, "line_items", None) or {}
        items = line_items.get("data") if isinstance(line_items, dict) else getattr(line_items, "data", None)
        if not items:
            continue
        price_ids = {getattr(i.price, "id", None) if hasattr(i, "price") else i.get("price", {}).get("id") for i in items}
        if STRIPE_PRICE_CUSTOMIZATION in price_ids:
            mem.entitlement.set(
                "customization",
                value="active",
                expires_at=_expires_in_cache_days(),
            )
            logger.info("[entitlement] refreshed customization entitlement from Stripe")
            return {"ok": True, "action": "refreshed"}

    return {"ok": True, "action": "no_active_entitlement"}


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


def entitlement_state(mem) -> dict:
    """Return current entitlement state for the UI."""
    return {
        "customization": {
            "active": mem.entitlement.is_active("customization"),
            "configured": configured(),
        },
    }


def has_customization(mem) -> bool:
    """Single-function gate for callers that just need the boolean."""
    if not configured():
        # When Stripe isn't configured at all, we're in dev mode — the
        # customization unlock is open by default so local development
        # doesn't require commerce infrastructure.
        return True
    return mem.entitlement.is_active("customization")
