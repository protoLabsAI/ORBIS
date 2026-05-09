"""Pairing token for the hosted-SPA + local-sidecar topology.

When ORBIS is deployed in the historical "single process serves both
SPA and API" mode, same-origin is the trust boundary and no pairing
token is needed. Setting ``ORBIS_ALLOWED_ORIGINS`` flips the sidecar
into split-deployment mode — the SPA lives on a different origin and
posts cross-origin to localhost. CORS alone is not a trust boundary
against a malicious tab the user has open in another window; the
pairing token is what stops that tab from talking to the sidecar.

Token resolution order (each call to :func:`get_pairing_token`):

1. ``ORBIS_PAIR_TOKEN`` env var — explicit, useful for headless
   deployments / tests.
2. A persisted token under ``~/.orbis/pair_token`` (mode 600). Created
   on first boot when CORS is enabled and no env token is set, so the
   user can re-paste it after restart without typing a new code.

Token shape: 32 hex chars from ``secrets.token_hex(16)``. Short enough
to read aloud once, long enough to be unguessable.

The HTTP middleware lives in ``app.py`` (``require_pair_token``) — this
module only deals with token storage. Keeping the storage concern out
of the request path makes it easy to unit-test the rotation and disk
behavior without spinning up FastAPI.
"""

from __future__ import annotations

import logging
import os
import secrets
import stat
from pathlib import Path

logger = logging.getLogger("auth.pairing")

ENV_TOKEN = "ORBIS_PAIR_TOKEN"
ENV_ALLOWED_ORIGINS = "ORBIS_ALLOWED_ORIGINS"

# Persisted under the user's home dir rather than alongside config/
# so the token survives ``rm -rf config/`` (users iterate on
# orbis.yaml; pair tokens shouldn't be collateral damage) and so a
# single user with multiple checkouts shares one token.
_TOKEN_PATH = Path.home() / ".orbis" / "pair_token"


def is_pairing_enforced() -> bool:
    """True when the sidecar is in split-deployment mode (CORS allowlist
    populated). In single-process mode this returns False and the HTTP
    middleware short-circuits — the historical loopback-only posture."""
    return bool(os.environ.get(ENV_ALLOWED_ORIGINS, "").strip())


def get_pairing_token() -> str | None:
    """Resolve the active pairing token. Returns None when pairing is
    not enforced (same-origin install). When enforced, returns the env
    token if set, else loads / creates the on-disk token."""
    if not is_pairing_enforced():
        return None
    env_token = os.environ.get(ENV_TOKEN, "").strip()
    if env_token:
        return env_token
    return _load_or_create_disk_token()


def rotate_token() -> str:
    """Force-mint a new on-disk token and return it. For ``orbis pair
    --rotate`` style flows; not used by the boot path. Ignored if an
    env token is set (the env is the source of truth in that case)."""
    if os.environ.get(ENV_TOKEN, "").strip():
        raise RuntimeError(
            f"Cannot rotate while {ENV_TOKEN} is set. "
            "Unset the env var or rotate it externally.",
        )
    token = secrets.token_hex(16)
    _write_token(token)
    return token


def _load_or_create_disk_token() -> str:
    if _TOKEN_PATH.exists():
        try:
            existing = _TOKEN_PATH.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        except OSError as e:
            logger.warning(f"could not read {_TOKEN_PATH}: {e}; minting fresh")
    token = secrets.token_hex(16)
    _write_token(token)
    return token


def _write_token(token: str) -> None:
    _TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    _TOKEN_PATH.write_text(token + "\n", encoding="utf-8")
    # 600 so other accounts on the box can't read it. We're a single-
    # user product but the token can authenticate any caller who can
    # reach the loopback port — defense in depth.
    try:
        _TOKEN_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        # Windows / weird filesystems — file got written, just
        # couldn't tighten the mode. Worth logging.
        logger.warning(f"could not chmod 600 {_TOKEN_PATH}")
