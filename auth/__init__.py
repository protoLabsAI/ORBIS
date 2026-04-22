"""protoVoice auth — API-key-based user identity."""

from .users import (
    AuthError,
    ROLE_ADMIN,
    ROLE_USER,
    User,
    UserRegistry,
    load_users,
    require_admin,
    require_user,
    single_user_fallback,
    user_registry,
)

__all__ = [
    "AuthError",
    "ROLE_ADMIN",
    "ROLE_USER",
    "User",
    "UserRegistry",
    "load_users",
    "require_admin",
    "require_user",
    "single_user_fallback",
    "user_registry",
]
