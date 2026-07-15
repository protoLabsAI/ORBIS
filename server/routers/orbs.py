"""Orb catalogue + starter-orb routes — extracted from app.py (#app.py-decomposition).

Library names import from their origin module; app-defined names via
`from app import`; the monkeypatched/mutable set as `app.<name>` at call
time (so live values + test monkeypatches win).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from agent.persona import reload_persona
from auth import require_user
from auth.users import User
from fastapi.responses import JSONResponse


router = APIRouter()


@router.get("/api/starter_orbs")
async def get_starter_orbs():
    """Return the curated starter-orb pool. The setup wizard calls this
    at first boot so the user can pick one; no auth required so the
    wizard can run before the user has their API key.

    Response shape::
        {"starters": [{slug, name, description, variant, palette, params}, ...]}
    """
    from agent.starter_orbs import load_starters
    starters = load_starters()
    return {"starters": [s.to_dict() for s in starters]}


@router.get("/api/orbs")
async def list_orbs(user: User = Depends(require_user)):
    """User-imported ``.orbis`` orb definitions (app-data orbs dir).
    The frontend fetches this at boot and registers each definition
    with the raymarch-v1 engine. See agent/orb_definitions.py +
    docs/internal/orb-format-and-editor.md.

    Response shape::
        {"orbs": [<OrbDefinition>, ...]}
    """
    from agent.orb_definitions import list_definitions
    return {"orbs": list_definitions()}


@router.post("/api/orbs")
async def import_orb(body: dict, user: User = Depends(require_user)):
    """Import (or update — same id replaces) a ``.orbis`` definition.

    Validation mirrors the frontend package's validator; a definition
    accepted here is one the engine will load."""
    from agent.orb_definitions import OrbDefinitionError, save_definition

    try:
        _, replaced = save_definition(body)
    except OrbDefinitionError as e:
        return JSONResponse(
            status_code=400,
            content={"error": str(e), "errors": e.errors},
        )
    return {"ok": True, "id": body["id"], "replaced": replaced}


@router.delete("/api/orbs/{orb_id}")
async def delete_orb(orb_id: str, user: User = Depends(require_user)):
    """Remove an imported orb definition. The frontend deregisters the
    variant and falls back to a starter when the deleted orb was active."""
    from agent.orb_definitions import delete_definition
    if not delete_definition(orb_id):
        return JSONResponse(
            status_code=404, content={"error": f"orb {orb_id!r} not found"},
        )
    return {"ok": True}


@router.post("/api/orb/select_starter")
async def select_starter(body: dict, user: User = Depends(require_user)):
    """Commit a starter-orb pick to config/orbis.yaml. Called by the
    setup wizard after the user picks. Validates the slug against
    the pool, writes the orb block, reloads persona.

    Body: ``{"slug": "<starter_slug>"}``."""
    slug = (body.get("slug") or "").strip()
    if not slug:
        raise HTTPException(status_code=400, detail="slug is required")
    from agent.starter_orbs import find_starter
    from agent.config_store import merge_patch
    hit = find_starter(slug)
    if not hit:
        raise HTTPException(
            status_code=404, detail=f"unknown starter: {slug!r}",
        )
    merge_patch({
        "orb": {
            "variant": hit.variant,
            "palette": hit.palette,
            "params": dict(hit.params),
        },
    })
    reload_persona()
    return {
        "ok": True,
        "starter": hit.to_dict(),
    }
