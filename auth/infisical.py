"""Infisical-backed user-key source.

The protoLabs fleet stores secrets in Infisical on pve01 (see
/home/ava/dev/CLAUDE.md). When Infisical env vars are set, protoVoice
fetches the user roster from Infisical instead of a local YAML file.
Falls back to ``config/users.yaml`` when Infisical isn't configured —
local dev workflow keeps working.

## Secret shape

A single secret ``USERS_YAML`` (name configurable via
``INFISICAL_USERS_SECRET_NAME``) whose value is the full YAML content
that ``config/users.yaml`` would contain:

    users:
      - id: alice
        api_key: pv_ak_...
        display_name: Alice

Rationale: one secret = one atomic update, preserves the full schema
without mapping each field to separate Infisical keys.

## Auth

Machine-identity / universal-auth flow (``INFISICAL_CLIENT_ID`` +
``INFISICAL_CLIENT_SECRET``). The token is cached in-process and
re-fetched on 401 or at boot. No refresh loop today; the registry's
``reload()`` method re-auths implicitly on each call.

## Env vars

    INFISICAL_API_URL          default https://app.infisical.com
    INFISICAL_CLIENT_ID        required to enable Infisical mode
    INFISICAL_CLIENT_SECRET    required (companion to client id)
    INFISICAL_PROJECT_ID       required — the workspace/project id
    INFISICAL_ENVIRONMENT      default "prod"
    INFISICAL_SECRET_PATH      default "/protovoice"
    INFISICAL_USERS_SECRET_NAME default "USERS_YAML"
"""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)


def enabled() -> bool:
    """True when both credentials + project id are set."""
    return bool(
        os.environ.get("INFISICAL_CLIENT_ID")
        and os.environ.get("INFISICAL_CLIENT_SECRET")
        and os.environ.get("INFISICAL_PROJECT_ID")
    )


def _api_url() -> str:
    return os.environ.get("INFISICAL_API_URL", "https://app.infisical.com").rstrip("/")


def _login() -> str | None:
    """Machine-identity login. Returns an access token or None on failure."""
    client_id = os.environ.get("INFISICAL_CLIENT_ID")
    client_secret = os.environ.get("INFISICAL_CLIENT_SECRET")
    if not (client_id and client_secret):
        return None
    try:
        resp = httpx.post(
            f"{_api_url()}/api/v1/auth/universal-auth/login",
            json={"clientId": client_id, "clientSecret": client_secret},
            timeout=10.0,
        )
        if resp.status_code != 200:
            logger.warning(f"[infisical] login failed {resp.status_code}: {resp.text[:200]}")
            return None
        token = resp.json().get("accessToken")
        if not token:
            logger.warning("[infisical] login response missing accessToken")
            return None
        return str(token)
    except Exception as e:
        logger.warning(f"[infisical] login error: {e}")
        return None


def fetch_users_yaml() -> str | None:
    """Return the raw YAML content stored at INFISICAL_USERS_SECRET_NAME,
    or None if Infisical isn't configured / reachable / the secret is missing.
    """
    if not enabled():
        return None
    token = _login()
    if not token:
        return None
    project_id = os.environ.get("INFISICAL_PROJECT_ID", "")
    env = os.environ.get("INFISICAL_ENVIRONMENT", "prod")
    path = os.environ.get("INFISICAL_SECRET_PATH", "/protovoice")
    name = os.environ.get("INFISICAL_USERS_SECRET_NAME", "USERS_YAML")
    try:
        resp = httpx.get(
            f"{_api_url()}/api/v3/secrets/raw/{name}",
            params={
                "workspaceId": project_id,
                "environment": env,
                "secretPath": path,
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
        )
        if resp.status_code == 404:
            logger.info(
                f"[infisical] secret {name} not found at {env}:{path} — "
                "treating as empty users list"
            )
            return ""
        if resp.status_code != 200:
            logger.warning(f"[infisical] fetch failed {resp.status_code}: {resp.text[:200]}")
            return None
        value = resp.json().get("secret", {}).get("secretValue")
        if value is None:
            logger.warning("[infisical] response missing secret.secretValue")
            return None
        return str(value)
    except Exception as e:
        logger.warning(f"[infisical] fetch error: {e}")
        return None
