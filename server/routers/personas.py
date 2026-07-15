"""Persona listing / switching / editing routes — extracted from app.py (#app.py-decomposition).

Library names import from their origin module; app-defined names via
`from app import`; the monkeypatched/mutable set as `app.<name>` at call
time (so live values + test monkeypatches win).
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends
import app

from agent.persona import load_persona, reload_persona
from auth import require_user
from auth.users import User
from fastapi.responses import JSONResponse
from voice.sse_bus import sse_bus


router = APIRouter()


@router.post("/api/persona/reload")
async def reload_persona_endpoint(user: User = Depends(require_user)):
    """Re-read config/orbis.yaml from disk.

    Applied on the next voice session (persona is snapshotted at
    connect time). Returns the loaded persona's slug + name."""
    persona = reload_persona()
    return {"ok": True, "slug": persona.slug, "name": persona.name}


@router.get("/api/personas")
async def list_personas(user: User = Depends(require_user)):
    """Persona catalog (epic #611): the yaml default + every persona
    file across the bundled + user dirs, plus which one is active.
    ``meta``/``prompt`` are the raw file contents so the manager dialog
    can populate its editor without a second round-trip (persona files
    can't hold api_key — the loader refuses it — so nothing to redact).
    """
    from agent.personas import load_persona_files
    default = load_persona(os.environ.get("ORBIS_CONFIG", "config/orbis.yaml"))
    files = load_persona_files()
    active = default.active_persona
    if active and active not in files:
        active = ""  # broken pointer — surface the fallback the boot uses
    return {
        "active": active or "default",
        "personas": [
            {
                "slug": "default",
                "name": default.name,
                "description": "The persona configured in orbis.yaml.",
                "source": "config",
                "editable": False,
            },
            *(
                {
                    "slug": pf.slug,
                    "name": pf.name,
                    "description": pf.description,
                    "source": pf.source,
                    "editable": pf.source == "user",
                    "meta": pf.meta,
                    "prompt": pf.body,
                }
                for pf in sorted(files.values(), key=lambda p: p.slug)
            ),
        ],
    }


@router.post("/api/personas/active")
async def set_active_persona(body: dict, user: User = Depends(require_user)):
    """Select the active persona. Persists ``persona.active_persona``
    to orbis.yaml, recomposes, and hot-swaps every live session —
    prompt/tools next turn, LLM + voice + filler immediately, orb via
    the ``persona-switched`` SSE event. ``notes`` carries anything that
    still needs a restart (TTS engine change, temperature)."""
    from agent.config_store import merge_patch
    from agent.personas import load_persona_files
    slug = str(body.get("slug") or "").strip().lower()
    if not slug:
        return JSONResponse(status_code=400, content={"error": "slug is required"})
    if slug in ("default", "orbis"):
        slug = ""
    elif slug not in load_persona_files():
        return JSONResponse(
            status_code=404, content={"error": f"unknown persona {slug!r}"},
        )
    merge_patch({"persona": {"active_persona": slug}})
    persona = reload_persona()
    result = app._apply_persona_switch(persona)
    await sse_bus.publish(
        "persona-switched",
        {"slug": slug or "default", "name": persona.name, **result},
    )
    return {
        "ok": True,
        "active": slug or "default",
        "name": persona.name,
        **result,
    }


@router.put("/api/personas/{slug}")
async def put_persona(slug: str, body: dict, user: User = Depends(require_user)):
    """Create or update a USER persona file. Body: the frontmatter
    fields (name, description, extends, voice, llm, orb, temperature,
    max_tokens, filler_verbosity, tools) + ``prompt`` (the markdown
    body). Writing a bundled starter's slug creates a user *shadow* —
    that's the edit path for shipped personas; deleting the shadow
    restores the original."""
    from agent.personas import write_persona_file
    meta = {k: v for k, v in body.items() if k != "prompt"}
    prompt = str(body.get("prompt") or "")
    try:
        path = write_persona_file(slug, meta, prompt)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    slug = slug.strip().lower()
    # Editing the ACTIVE persona re-applies it live, same as switching
    # to it (the composed cache + live sessions would otherwise keep
    # serving the pre-edit text).
    if load_persona(
        os.environ.get("ORBIS_CONFIG", "config/orbis.yaml")
    ).active_persona == slug:
        persona = reload_persona()
        result = app._apply_persona_switch(persona)
        await sse_bus.publish(
            "persona-switched",
            {"slug": slug, "name": persona.name, **result},
        )
    return {
        "ok": True,
        "slug": slug,
        "path": str(path),
        "shadows_bundled": _persona_shadows_bundled(slug),
    }


def _persona_shadows_bundled(slug: str) -> bool:
    """True when a user persona file sits over a bundled one with the
    same slug (deleting the user file un-shadows the original)."""
    from agent.personas import bundled_personas_dir
    return (bundled_personas_dir() / f"{slug}.md").is_file()


@router.delete("/api/personas/{slug}")
async def delete_persona(slug: str, user: User = Depends(require_user)):
    """Delete a USER persona file (bundled starters are read-only; a
    shadow delete un-shadows the bundled original). Clears the active
    pointer when it referenced the deleted file."""
    from agent.config_store import merge_patch
    from agent.personas import delete_persona_file, load_persona_files
    slug = slug.strip().lower()
    if not delete_persona_file(slug):
        files = load_persona_files()
        detail = (
            "bundled personas are read-only"
            if slug in files
            else f"unknown persona {slug!r}"
        )
        return JSONResponse(status_code=404, content={"error": detail})
    was_active = load_persona(
        os.environ.get("ORBIS_CONFIG", "config/orbis.yaml")
    ).active_persona == slug
    if was_active and slug not in load_persona_files():
        # No bundled file un-shadowed — the pointer now dangles; clear it.
        merge_patch({"persona": {"active_persona": ""}})
    persona = reload_persona()
    if was_active:
        # Whatever the delete resolved to (un-shadowed bundled original
        # or the default) should take over live, same as a switch.
        result = app._apply_persona_switch(persona)
        await sse_bus.publish(
            "persona-switched",
            {"slug": persona.slug, "name": persona.name, **result},
        )
    return {"ok": True}


@router.get("/api/personality")
async def get_personality(user: User = Depends(require_user)):
    """Return current personality state: axes + mood + recent drift
    events + session stats. Drives the drawer's Profile panel so the
    user can see why the orb feels a certain way."""
    mem = app.get_memory()
    try:
        axes = [
            {"axis": a.axis, "value": a.value, "updated_at": a.updated_at}
            for a in mem.personality.all_axes()
        ]
    except Exception:
        axes = []
    try:
        mood = mem.personality.get_mood()
        mood_dict = {
            "valence": mood.valence,
            "arousal": mood.arousal,
            "guardedness": mood.guardedness,
            "updated_at": mood.updated_at,
        }
    except Exception:
        mood_dict = None
    try:
        events = mem.personality.recent_events(limit=20)
    except Exception:
        events = []
    try:
        session_count = mem.sessions.count()
        last_session_ended_at = mem.sessions.last_ended_at()
    except Exception:
        session_count = 0
        last_session_ended_at = None
    return {
        "axes": axes,
        "mood": mood_dict,
        "recent_events": events,
        "sessions": {
            "count": session_count,
            "last_ended_at": last_session_ended_at,
        },
    }
