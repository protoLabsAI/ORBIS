"""Request-time auth + origin enforcement for the A2A endpoint.

``a2a-sdk`` advertises security schemes on the agent card but does NOT enforce
them on the wire — enforcement is the host's job. This Starlette middleware
guards the ``/a2a`` JSON-RPC path with the same posture ORBIS's hand-rolled
handler had (ported from protoAgent#453's ``a2a_auth.py``, with ORBIS's stricter
closed-by-default):

  - **Bearer** — ``Authorization: Bearer <token>`` validated against the
    configured token (``A2A_AUTH_TOKEN``).
  - **X-API-Key** — legacy header, validated when set.
  - **Origin** — ``A2A_ALLOWED_ORIGINS`` allowlist for browser callers.
  - **Closed by default** — unlike the fleet reference (open-when-unset), ORBIS
    **rejects** all ``/a2a`` traffic when no token/key is configured, unless
    ``A2A_ALLOW_UNAUTH=1`` is set explicitly. Preserves ORBIS's prior posture.

The active bearer token lives in a module-level holder so a wizard/drawer reload
can update it live via ``set_bearer_token`` without re-registering routes.
"""

from __future__ import annotations

import hmac
import logging
import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

# Live-updatable bearer token (None = no bearer configured).
_BEARER: list[str | None] = [None]
# X-API-Key (env-seeded at install; constant for the process).
_API_KEY: list[str] = [""]
# Allowed origins: None = verification disabled; list = allowlist.
_ALLOWED_ORIGINS: list[list[str] | None] = [None]
# Closed-by-default: when no bearer/key is configured, reject unless the
# operator opts into open mode via A2A_ALLOW_UNAUTH=1.
_CLOSED: list[bool] = [True]

# Path prefix the guard applies to. The agent card + health are public.
_GUARDED_PREFIX = "/a2a"


def set_bearer_token(token: str | None) -> None:
    """Update the active bearer token at runtime (wizard/drawer reload)."""
    _BEARER[0] = (token or "").strip() or None
    _recompute_closed()


def _recompute_closed() -> None:
    has_creds = bool(_BEARER[0]) or bool(_API_KEY[0])
    allow_unauth = os.environ.get("A2A_ALLOW_UNAUTH", "").strip() == "1"
    _CLOSED[0] = not has_creds and not allow_unauth


def configure(*, bearer_token: str | None, api_key: str, allowed_origins_raw: str) -> None:
    """Seed the guard at route-registration time.

    Args:
        bearer_token: from ``A2A_AUTH_TOKEN`` (caller applies the env fallback).
            Empty/whitespace → no bearer.
        api_key: the legacy X-API-Key value; "" disables the X-API-Key check.
        allowed_origins_raw: ``A2A_ALLOWED_ORIGINS`` ("" / "*" = disabled, else a
            comma-separated allowlist).
    """
    seed = (bearer_token or os.environ.get("A2A_AUTH_TOKEN", "") or "").strip()
    _BEARER[0] = seed or None
    _API_KEY[0] = api_key or ""

    raw = (allowed_origins_raw or "").strip()
    if not raw or raw == "*":
        if not raw:
            logger.warning("[a2a] A2A_ALLOWED_ORIGINS not set — origin verification disabled")
        _ALLOWED_ORIGINS[0] = None
    else:
        _ALLOWED_ORIGINS[0] = [o.strip().lower() for o in raw.split(",") if o.strip()]

    _recompute_closed()
    if _CLOSED[0]:
        logger.warning(
            "[a2a] no auth configured and A2A_ALLOW_UNAUTH!=1 — /a2a is CLOSED "
            "(set A2A_AUTH_TOKEN, or A2A_ALLOW_UNAUTH=1 to open it)"
        )
    elif _BEARER[0] is None and not _API_KEY[0]:
        logger.warning("[a2a] A2A_ALLOW_UNAUTH=1 — /a2a is OPEN (no auth)")


def _unauthorized(detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=401)


class A2AAuthMiddleware(BaseHTTPMiddleware):
    """Enforces closed-default / bearer / X-API-Key / origin on ``/a2a``."""

    async def dispatch(self, request: Request, call_next):
        if not request.url.path.startswith(_GUARDED_PREFIX):
            return await call_next(request)

        # Closed by default — no credentials configured and not opted-open.
        if _CLOSED[0]:
            return _unauthorized("Unauthorized: A2A endpoint is not configured for access")

        # X-API-Key (legacy) — enforced only when configured.
        api_key = _API_KEY[0]
        if api_key and request.headers.get("x-api-key") != api_key:
            return _unauthorized("Unauthorized")

        # Bearer — enforced only when configured.
        active = _BEARER[0]
        if active:
            header = request.headers.get("Authorization", "")
            if not header.startswith("Bearer "):
                return _unauthorized("Unauthorized: expected 'Authorization: Bearer <token>'")
            if not hmac.compare_digest(header[len("Bearer ") :], active):
                return _unauthorized("Unauthorized: invalid bearer token")

        # Origin — enforced only when an allowlist is set.
        allowed = _ALLOWED_ORIGINS[0]
        if allowed is not None:
            origin = request.headers.get("Origin", "").lower()
            if origin not in allowed:
                return JSONResponse({"detail": "Forbidden: origin not allowed"}, status_code=403)

        return await call_next(request)


def install(app, *, bearer_token: str | None, api_key: str, allowed_origins_raw: str) -> None:
    """Configure the guard and add the middleware to ``app``."""
    configure(
        bearer_token=bearer_token,
        api_key=api_key,
        allowed_origins_raw=allowed_origins_raw,
    )
    app.add_middleware(A2AAuthMiddleware)
