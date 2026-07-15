"""Delegate registry + discovery routes — extracted from app.py (#app.py-decomposition).

Library names import from their origin module; app-defined names via
`from app import`; the monkeypatched/mutable set as `app.<name>` at call
time (so live values + test monkeypatches win).
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends
import app

from agent.user_state import all_user_states
from auth import require_user, user_registry
from auth.users import User
from fastapi.responses import JSONResponse
from app import logger


router = APIRouter()


@router.post("/api/users/reload")
async def reload_users_endpoint(user: User = Depends(require_user)):
    """Re-fetch the user roster from Infisical (if configured) or the
    YAML file. Safe to call mid-session — active clients keep their
    authenticated state until they reconnect; new connections use the
    refreshed registry."""
    names = user_registry.reload()
    return {"ok": True, "users": names, "source": user_registry.source}


@router.post("/api/delegates/reload")
async def reload_delegates_endpoint(user: User = Depends(require_user)):
    """Re-read config/delegates.yaml from disk.

    Safe mid-session — delegate lookup happens per `delegate_to()` call,
    so in-flight sessions see the new registry on their next dispatch.
    """
    names = app._DELEGATES.reload()
    _hot_refresh_delegate_sessions()
    return {"ok": True, "delegates": names}


# --- Delegates CRUD ---------------------------------------------------------


def _hot_refresh_delegate_sessions() -> None:
    """Push the updated delegate roster into any live voice session so a
    Settings change (add/edit/remove a delegate) takes effect on the next turn
    without a restart — see run_bot's _refresh_delegates."""
    for st in all_user_states():
        fn = getattr(st, "refresh_delegates", None)
        if callable(fn):
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[delegates/hot] session refresh failed: {e}")


def _decorate_delegates(entries: list[dict]) -> list[dict]:
    """Annotate raw delegate entries with ``configured`` (present in the live
    registry) + cached ``health`` — the shape the Settings UI expects. Used by
    the list AND the create/update responses so a freshly-saved delegate shows
    its real status immediately instead of a stale '⚠ unconfigured'."""
    parsed_names = set(app._DELEGATES.names())
    parsed_by_name = {d.name: d for d in app._DELEGATES.all()}
    out: list[dict] = []
    for entry in entries:
        name = entry.get("name")
        d = parsed_by_name.get(name) if isinstance(name, str) else None
        h = app._DELEGATES.health(name) if isinstance(name, str) else None
        out.append({
            **entry,
            "configured": name in parsed_names,
            "health": {
                "ok": h.ok if h else None,
                "latency_ms": h.latency_ms if h else None,
                "last_checked": h.last_checked if h else None,
                "last_error": h.last_error if h else None,
                "consecutive_failures": h.consecutive_failures if h else 0,
            } if d else None,
        })
    return out


@router.get("/api/delegates")
async def list_delegates_endpoint(user: User = Depends(require_user)):
    from agent.delegate_config_store import (
        DelegateValidationError,
        read_delegates,
    )
    try:
        # Uses the config-store DEFAULT_PATH, now unified to resolve the same
        # way the live registry does (DELEGATES_YAML) so it reads the file the
        # registry loaded. The registry fallback below covers any residual
        # path divergence in the bundle.
        entries = read_delegates()
    except DelegateValidationError as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

    # Belt-and-suspenders: if the file read produced nothing but the live
    # registry has delegates, surface those so the UI reflects reality.
    if not entries and app._DELEGATES.all():
        entries = [
            {"name": d.name, "type": d.type, "url": d.url,
             "description": d.description}
            for d in app._DELEGATES.all()
        ]

    return {"delegates": _decorate_delegates(entries)}


@router.post("/api/delegates")
async def create_delegate_endpoint(
    body: dict, user: User = Depends(require_user),
):
    from agent.delegate_config_store import (
        DelegateValidationError,
        read_delegates,
        upsert_delegate,
    )
    try:
        name = (body or {}).get("name")
        if isinstance(name, str) and any(e.get("name") == name for e in read_delegates()):
            return JSONResponse(
                status_code=409,
                content={"error": f"delegate {name!r} already exists; use PUT to update"},
            )
        entries = upsert_delegate(body)
    except DelegateValidationError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    app._DELEGATES.reload()
    _hot_refresh_delegate_sessions()
    return {"ok": True, "delegates": _decorate_delegates(entries)}


@router.put("/api/delegates/{name}")
async def update_delegate_endpoint(
    name: str, body: dict, user: User = Depends(require_user),
):
    from agent.delegate_config_store import (
        DelegateValidationError,
        read_delegates,
        upsert_delegate,
    )
    body = body or {}
    body_name = body.get("name")
    if body_name and body_name != name:
        return JSONResponse(
            status_code=400,
            content={
                "error": (
                    f"path name {name!r} does not match body name {body_name!r}; "
                    "DELETE then POST to rename"
                ),
            },
        )
    body["name"] = name
    if not any(e.get("name") == name for e in read_delegates()):
        return JSONResponse(
            status_code=404, content={"error": f"delegate {name!r} not found"},
        )
    try:
        entries = upsert_delegate(body)
    except DelegateValidationError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    app._DELEGATES.reload()
    _hot_refresh_delegate_sessions()
    return {"ok": True, "delegates": _decorate_delegates(entries)}


@router.delete("/api/delegates/{name}")
async def delete_delegate_endpoint(name: str, user: User = Depends(require_user)):
    from agent.delegate_config_store import delete_delegate
    try:
        entries = delete_delegate(name)
    except KeyError:
        return JSONResponse(
            status_code=404, content={"error": f"delegate {name!r} not found"},
        )
    app._DELEGATES.reload()
    _hot_refresh_delegate_sessions()
    return {"ok": True, "delegates": _decorate_delegates(entries)}


@router.get("/api/delegate-types")
async def delegate_types_endpoint(user: User = Depends(require_user)):
    """The registered delegate types with their capabilities + field schema.

    Drives the generic Settings New/Edit form and the delegation-monitor
    widget (a type's ``capabilities`` decide what's renderable). Adding a
    delegate type = register one adapter; it shows up here automatically."""
    from agent.delegate_adapters import delegate_type_specs
    return {"types": delegate_type_specs()}


@router.post("/api/delegates/test")
async def test_delegate_endpoint(body: dict, user: User = Depends(require_user)):
    return await _probe_delegate(body or {})


@router.get("/api/delegates/discover")
async def discover_delegates_endpoint(user: User = Depends(require_user)):
    """A2A agents broadcasting nearby (local port-scan + mDNS `_protoagent._tcp`
    browse + tailnet probe — protoAgent ADR 0042 §I interop) — candidates to
    add as a2a delegates. Excludes agents already configured (+ ORBIS itself)."""
    from urllib.parse import urlparse

    from agent import discovery

    known: set[tuple[str, int]] = set()
    # ORBIS itself — both its loopback and LAN addresses.
    bound_port = int(os.environ.get("ORBIS_BOUND_PORT", "0") or 0)
    if bound_port:
        known.add(("127.0.0.1", bound_port))
        try:
            known.add((discovery._local_ip(), bound_port))
        except Exception:  # noqa: BLE001
            pass
    # Already-configured a2a delegates, by their endpoint's (host, port).
    for d in app._DELEGATES.all():
        if d.type != "a2a" or not d.url:
            continue
        try:
            parsed = urlparse(d.url)
            if parsed.hostname and parsed.port:
                known.add((parsed.hostname, parsed.port))
        except ValueError:
            continue
    return {"discovered": await discovery.discover(known=known)}


async def _probe_delegate(entry: dict) -> dict:
    from agent.delegate_config_store import (
        DelegateValidationError,
        validate_entry,
    )
    from agent.delegates import _parse_entry, probe
    try:
        normalized = validate_entry(entry)
    except DelegateValidationError as e:
        return {"ok": False, "error": str(e)}
    delegate = _parse_entry(normalized)
    if delegate is None:
        return {"ok": False, "error": "registry rejected normalized entry"}
    return await probe(delegate)
