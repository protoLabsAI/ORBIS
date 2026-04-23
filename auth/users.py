"""Single-owner API-key auth.

ORBIS is a single-user product (one owner per install) designed for
tailnet hosting — you access your orb from your phone, laptop, or
whatever else, all against the same instance. Auth exists to keep
tailnet neighbors out, not to separate multiple users.

The owner's credential lives in ``config/users.yaml``:

    users:
      - id: owner
        api_key: pv_ak_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
        display_name: Your Name

Only the first entry is read. Additional entries log a warning and are
ignored — migrate any extras out of the file. Every HTTP request to
``/api/*`` carries ``X-API-Key: <key>`` or ``Authorization: Bearer
<key>`` and is matched against the owner's hash.

Single-user fallback: if ``config/users.yaml`` is missing or empty,
every request resolves to the synthetic DEFAULT_USER. Keeps first-boot
and local dev workflows unblocked. The moment an entry exists in YAML,
auth enforcement kicks in.

Infisical: when ``INFISICAL_CLIENT_ID`` + ``CLIENT_SECRET`` +
``PROJECT_ID`` are set, the owner YAML is fetched from Infisical
instead of disk — same shape.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from fastapi import Header, HTTPException

from . import infisical

logger = logging.getLogger(__name__)


class AuthError(HTTPException):
    def __init__(self, detail: str = "unauthorized") -> None:
        super().__init__(status_code=401, detail=detail)


@dataclass(frozen=True)
class User:
    """The owner of this ORBIS install."""
    id: str
    display_name: str
    # sha256 of the api_key; constant-time compared against incoming keys.
    api_key_hash: str

    @staticmethod
    def hash_key(api_key: str) -> str:
        return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


# Synthetic owner used when no users.yaml is configured. Keeps first
# boot + tailnet-less local dev working without credentials.
DEFAULT_USER = User(
    id="default",
    display_name="Owner",
    api_key_hash="",
)


class UserRegistry:
    """In-memory owner credential.

    Sources, in priority order:
      1. Infisical (when credentials are set in env) — fetches a single
         YAML secret whose content is the same shape as the on-disk file.
      2. On-disk YAML at ``path`` (typically ``config/users.yaml``).
      3. Empty → single-user fallback (no auth enforced).

    ``reload()`` re-fetches from the active source.
    """

    def __init__(self, path: str | Path | None = None, *, auto_load: bool = True):
        self._path = Path(path) if path else None
        self._owner: User | None = None
        self._source = "empty"
        if auto_load:
            self._refresh()

    @property
    def source(self) -> str:
        return self._source

    def _refresh(self) -> None:
        """Pull the owner entry from Infisical or disk."""
        owner: User | None = None
        source = "empty"

        raw_yaml: str | None = None
        if infisical.enabled():
            raw_yaml = infisical.fetch_users_yaml()
            if raw_yaml is not None:
                source = "infisical"
        if raw_yaml is None and self._path and self._path.exists():
            try:
                raw_yaml = self._path.read_text()
                source = "file"
            except Exception as e:
                logger.error(f"[auth] failed to read {self._path}: {e}")
                raw_yaml = None

        if raw_yaml:
            try:
                data = yaml.safe_load(raw_yaml) or {}
            except Exception as e:
                logger.error(f"[auth] failed to parse {source} users yaml: {e}")
                data = {}
            entries = data.get("users") or []
            if len(entries) > 1:
                logger.warning(
                    f"[auth] {len(entries)} entries in users.yaml; ORBIS is single-owner. "
                    f"Only the first entry is read; drop the rest."
                )
            for raw in entries[:1]:
                if not isinstance(raw, dict):
                    continue
                uid = (raw.get("id") or "").strip()
                key = raw.get("api_key") or ""
                if not uid or not key:
                    logger.warning(f"[auth] malformed owner entry: {raw!r}")
                    continue
                name = (raw.get("display_name") or uid).strip()
                owner = User(
                    id=uid,
                    display_name=name,
                    api_key_hash=User.hash_key(key),
                )
                break

        self._owner = owner
        self._source = source
        if owner:
            logger.info(f"[auth] source={source} owner={owner.id!r}")
        else:
            logger.info(f"[auth] source={source} owner=<none> (single-user fallback)")

    def reload(self) -> list[str]:
        """Re-fetch from the active source."""
        self._refresh()
        return [self._owner.id] if self._owner else []

    def resolve(self, api_key: str | None) -> User | None:
        """Return the owner if the key matches, else None. Constant-time."""
        if not api_key or self._owner is None:
            return None
        candidate_hash = User.hash_key(api_key)
        if hmac.compare_digest(self._owner.api_key_hash, candidate_hash):
            return self._owner
        return None

    def single_user_mode(self) -> bool:
        """True when no owner is configured — first-boot / local dev."""
        return self._owner is None

    def owner(self) -> User | None:
        return self._owner


# ---------------------------------------------------------------------------
# Module-level registry + FastAPI dependency.
# ---------------------------------------------------------------------------

# Module-level registry. Inert until ``load_users()`` is called at boot —
# constructor with auto_load=False skips the Infisical round-trip at import.
user_registry = UserRegistry(None, auto_load=False)


def load_users(path: str | Path | None = None) -> UserRegistry:
    """Replace the module registry with one loaded from Infisical (if
    configured) or the given YAML path. Call once at app boot."""
    global user_registry
    user_registry = UserRegistry(path, auto_load=True)
    return user_registry


def single_user_fallback() -> User:
    """Return the synthetic owner for first-boot / fallback mode."""
    return DEFAULT_USER


def require_user(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None),
) -> User:
    """FastAPI dependency — resolves the request's API key into the owner.

    Accepts ``X-API-Key: <key>`` or ``Authorization: Bearer <key>``.
    In single-user fallback mode (no users.yaml loaded), every call
    returns DEFAULT_USER regardless of credentials.
    """
    if user_registry.single_user_mode():
        return DEFAULT_USER

    key: str | None = x_api_key
    if not key and authorization and authorization.lower().startswith("bearer "):
        key = authorization[7:].strip()

    user = user_registry.resolve(key)
    if not user:
        raise AuthError("unknown or missing api key")
    return user


def current_user_or_none(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None),
) -> User | None:
    """Non-enforcing variant — returns the owner if the key resolves,
    None otherwise. Used by routes that scope by user when a key is
    present but stay public when absent."""
    if user_registry.single_user_mode():
        return DEFAULT_USER
    key: str | None = x_api_key
    if not key and authorization and authorization.lower().startswith("bearer "):
        key = authorization[7:].strip()
    return user_registry.resolve(key) if key else None


def key_from_scope(scope: dict[str, Any]) -> str | None:
    """Extract the key from raw ASGI scope.headers (used where a
    FastAPI Depends() isn't practical — e.g. the raw /api/offer
    handler that receives the SmallWebRTCRequest body)."""
    for name, value in scope.get("headers", []) or []:
        if name.lower() == b"x-api-key":
            return value.decode("utf-8", errors="ignore")
        if name.lower() == b"authorization":
            raw = value.decode("utf-8", errors="ignore")
            if raw.lower().startswith("bearer "):
                return raw[7:].strip()
    return None
