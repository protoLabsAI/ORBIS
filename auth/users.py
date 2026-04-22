"""User registry + API-key auth.

protoVoice identifies each connected human by an API key they hold.
The roster lives in ``config/users.yaml``:

    users:
      - id: alice
        api_key: pv_ak_aXXXXXXXXXXXXXX
        display_name: Alice
        allowed_skills: [josh, chef]
      - id: bob
        api_key: pv_ak_bYYYYYYYYYYYYYY
        display_name: Bob
        role: admin

Every HTTP request to ``/api/*`` carries ``X-API-Key: <key>``. The key
resolves to a ``User``; the app scopes per-user state (skill selection,
verbosity, session memory, delivery controller, trace session) on
``user.id``.

Single-user fallback: if ``config/users.yaml`` is missing or empty,
the registry returns a synthetic ``User(id="default", ...)`` for every
request regardless of ``X-API-Key``. Keeps the existing tailnet
single-user dev workflow intact. The moment you add even one entry
to the YAML, auth enforcement kicks in.
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


ROLE_USER = "user"
ROLE_ADMIN = "admin"


@dataclass(frozen=True)
class User:
    """A known protoVoice user."""
    id: str
    display_name: str
    # Stored as a hash in memory; compared constant-time against incoming keys.
    api_key_hash: str
    # Permission role. "admin" can freely pick skill / viz + edit other
    # users. "user" (default) is constrained to ``allowed_skills`` and
    # their optional ``pinned_viz``.
    role: str = ROLE_USER
    # List of skill slugs this user is allowed to activate. ``None`` means
    # unconstrained (all catalog skills). A one-element list locks the
    # user to that single skill (client renders a read-only chip). Admins
    # bypass this check entirely.
    allowed_skills: tuple[str, ...] | None = None
    # Optional orb viz override — overrides the active skill's own viz on
    # session start. Works for any role; admins usually leave it unset.
    pinned_viz: dict | None = None

    @staticmethod
    def hash_key(api_key: str) -> str:
        return hashlib.sha256(api_key.encode("utf-8")).hexdigest()

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    def allows_skill(self, slug: str) -> bool:
        """True if this user is permitted to activate ``slug``.

        Admins are always allowed. For regular users, ``allowed_skills=None``
        means all slugs pass; otherwise the slug must be in the list.
        """
        if self.is_admin:
            return True
        if self.allowed_skills is None:
            return True
        return slug in self.allowed_skills


# Sentinel used when no config/users.yaml is present — the single-user
# fallback every request resolves to. Runs as admin so dev doesn't get
# locked out.
DEFAULT_USER = User(
    id="default", display_name="Default", api_key_hash="", role=ROLE_ADMIN,
)


class UserRegistry:
    """In-memory lookup keyed by the sha256 hash of the api key.

    Sources, in priority order:
      1. Infisical (when INFISICAL_CLIENT_ID + CLIENT_SECRET + PROJECT_ID
         env vars are set) — fetches a single YAML secret whose content
         is the same shape as the on-disk file.
      2. On-disk YAML at ``path`` (typically ``config/users.yaml``).
      3. Empty registry → single-user fallback (no auth enforced).

    ``reload()`` re-fetches from whichever source is active.
    """

    def __init__(self, path: str | Path | None = None, *, auto_load: bool = True):
        self._path = Path(path) if path else None
        self._by_hash: dict[str, User] = {}
        self._source = "empty"
        if auto_load:
            self._refresh()

    @property
    def source(self) -> str:
        return self._source

    def _refresh(self) -> None:
        """Pull from Infisical or disk and rebuild the index."""
        new_index: dict[str, User] = {}
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
            for raw in data.get("users") or []:
                if not isinstance(raw, dict):
                    continue
                uid = (raw.get("id") or "").strip()
                key = raw.get("api_key") or ""
                if not uid or not key:
                    logger.warning(f"[auth] skipping malformed user entry: {raw!r}")
                    continue
                name = (raw.get("display_name") or uid).strip()
                role = (raw.get("role") or ROLE_USER).strip().lower()
                if role not in (ROLE_USER, ROLE_ADMIN):
                    logger.warning(f"[auth] {uid}: unknown role {role!r}, defaulting to 'user'")
                    role = ROLE_USER
                allowed_raw = raw.get("allowed_skills")
                allowed_skills: tuple[str, ...] | None
                if allowed_raw is None:
                    allowed_skills = None
                elif isinstance(allowed_raw, list):
                    cleaned = [
                        s.strip() for s in allowed_raw
                        if isinstance(s, str) and s.strip()
                    ]
                    # Empty-list means "no skills allowed" — almost never
                    # intentional. Log + treat as unconstrained so the user
                    # doesn't get soft-locked out of every skill.
                    if not cleaned:
                        logger.warning(
                            f"[auth] {uid}: allowed_skills is empty; "
                            "treating as unconstrained"
                        )
                        allowed_skills = None
                    else:
                        allowed_skills = tuple(cleaned)
                else:
                    logger.warning(
                        f"[auth] {uid}: allowed_skills must be a list of "
                        "slugs, ignoring"
                    )
                    allowed_skills = None
                pinned_viz = raw.get("pinned_viz") or None
                if pinned_viz is not None and not isinstance(pinned_viz, dict):
                    logger.warning(f"[auth] {uid}: pinned_viz must be a mapping, ignoring")
                    pinned_viz = None
                new_index[User.hash_key(key)] = User(
                    id=uid,
                    display_name=name,
                    api_key_hash=User.hash_key(key),
                    role=role,
                    allowed_skills=allowed_skills,
                    pinned_viz=pinned_viz,
                )

        self._by_hash = new_index
        self._source = source
        logger.info(
            f"[auth] source={source} users={sorted(u.id for u in new_index.values())}"
        )

    def reload(self) -> list[str]:
        """Re-fetch from the active source (Infisical or file) and rebuild."""
        self._refresh()
        return sorted(u.id for u in self._by_hash.values())

    def resolve(self, api_key: str | None) -> User | None:
        """Return the User for a key, or None. Constant-time comparison
        to prevent timing attacks if two users' hashes share a prefix."""
        if not api_key:
            return None
        candidate_hash = User.hash_key(api_key)
        # Walk the whole map to keep lookup constant-time vs a dict lookup
        # that can short-circuit. Small N makes this fine.
        matched: User | None = None
        for h, user in self._by_hash.items():
            if hmac.compare_digest(h, candidate_hash):
                matched = user
        return matched

    def single_user_mode(self) -> bool:
        """True when the registry is empty — dev / tailnet-homelab mode."""
        return not self._by_hash

    def all(self) -> list[User]:
        return list(self._by_hash.values())

    def by_id(self, user_id: str) -> User | None:
        """Look up a user by id rather than api key. Used by code that
        already has a user_id (e.g. from a ContextVar) and needs role +
        allowed_skills / pinned_viz info."""
        if user_id == DEFAULT_USER.id and self.single_user_mode():
            return DEFAULT_USER
        for u in self._by_hash.values():
            if u.id == user_id:
                return u
        return None


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
    """Return a stable placeholder user for single-tenant mode."""
    return DEFAULT_USER


def require_user(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None),
) -> User:
    """FastAPI dependency — resolves the request's API key into a User.

    Accepts either ``X-API-Key: <key>`` or ``Authorization: Bearer <key>``.
    In single-user mode (no users.yaml loaded), every call returns the
    DEFAULT user regardless of credentials.
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


def require_admin(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None),
) -> User:
    """Like require_user, but 403 unless the resolved user's role is 'admin'.
    Single-user fallback mode resolves to DEFAULT_USER (role=admin), so dev
    + existing tailnet deployments keep working."""
    user = require_user(x_api_key=x_api_key, authorization=authorization)
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin role required")
    return user


def current_user_or_none(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None),
) -> User | None:
    """Non-enforcing variant — returns the User if the key resolves, None
    otherwise. Useful for routes that want to scope by user when present
    but stay public when absent."""
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
