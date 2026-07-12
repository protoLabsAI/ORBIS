"""Server-side orb vocabulary for voice control (#577).

Why this exists: `set_orb_visual` (#560) was disabled in #562 because it
was buggy in practice. Root cause: the handler passed **unvalidated,
LLM-invented names** straight through — and the frontend orb store
treats an unknown variant as a not-yet-registered runtime import, so a
single hallucinated name ("make it red" → variant "red") parks the
store in a *pending* state that swallows every later palette/param
apply. Worse, the junk name was persisted to orbis.yaml first, so the
wedge re-armed on every boot.

The fix: resolve the ask against the REAL vocabulary before anything is
applied or persisted, and answer unresolvable asks with the valid
options so the LLM can self-correct out loud.

Vocabulary sources (server-visible):
- ``BASE_VARIANTS`` — the shader variants registered in
  ``web/src/plugins/orb/variants/`` (kept in sync by hand; drift costs a
  "valid options" reply, never breakage).
- Starter orbs (``config/starter_orbs.yaml``) — slugs, names, and their
  variant+palette pairs. This is the natural voice vocabulary ("the
  aurora one", "switch to ember").
- Imported ``.orbis`` definitions — ids + their palette sets (the
  definition json carries ``palettes``).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# web/src/plugins/orb/variants/*/index.tsx ids (edison is disabled).
BASE_VARIANTS = (
    "fractal", "nebula", "crystal", "particles",
    "tetra", "lattice", "spectrum", "galaxy", "reactor", "flux",
)


def _vocabulary() -> tuple[dict, dict, dict]:
    """(variants, palettes, starters) — all keyed lowercase.

    variants: lower → canonical variant/definition id
    palettes: lower → (canonical palette, owning variant id or None)
    starters: lower slug/name → {variant, palette}
    """
    from agent.orb_definitions import list_definitions
    from agent.starter_orbs import load_starters

    variants = {v: v for v in BASE_VARIANTS}
    palettes: dict[str, tuple[str, str | None]] = {}
    starters: dict[str, dict] = {}

    for s in load_starters():
        variants.setdefault(s.variant.lower(), s.variant)
        palettes.setdefault(s.palette.lower(), (s.palette, s.variant))
        entry = {"variant": s.variant, "palette": s.palette}
        starters[s.slug.lower()] = entry
        starters.setdefault(s.name.lower(), entry)

    try:
        for d in list_definitions():
            did = str(d.get("id") or "")
            if not did:
                continue
            variants[did.lower()] = did
            pals = d.get("palettes")
            if isinstance(pals, dict):
                for p in pals:
                    palettes.setdefault(str(p).lower(), (str(p), did))
    except Exception as e:  # noqa: BLE001 — vocabulary, not correctness
        logger.warning(f"[orb_vocab] imported definitions unavailable: {e}")

    return variants, palettes, starters


def describe_options() -> str:
    """Human/LLM-readable summary of what's valid right now. Used in the
    tool description and in the can't-resolve reply."""
    variants, palettes, starters = _vocabulary()
    return (
        f"variants: {', '.join(sorted(set(variants.values())))}; "
        f"palettes: {', '.join(sorted({p for p, _ in palettes.values()}))}; "
        f"named looks: {', '.join(sorted(starters))}"
    )


def resolve_orb_ask(
    variant: str = "", palette: str = "",
) -> tuple[dict, str | None]:
    """Resolve a (variant, palette) ask into a canonical orb patch.

    Returns ``(patch, None)`` on success — patch holds only the keys
    that resolved — or ``({}, error)`` where error is a spoken-style
    message listing the valid options. Either input may also be a
    starter slug/name ("ember", "aurora"): that resolves to the
    starter's variant+palette pair (an explicit other field still wins).
    """
    variants, palettes, starters = _vocabulary()
    v_ask = (variant or "").strip()
    p_ask = (palette or "").strip()
    patch: dict = {}

    # Starter slugs/names are the strongest signal — they carry both.
    hit = starters.get(v_ask.lower()) or starters.get(p_ask.lower())
    if hit:
        patch["variant"] = hit["variant"]
        patch["palette"] = hit["palette"]

    if v_ask and v_ask.lower() not in starters:
        canon = variants.get(v_ask.lower())
        if canon is None:
            return {}, (
                f"I don't know an orb variant called '{v_ask}'. "
                f"Valid options — {describe_options()}."
            )
        patch["variant"] = canon

    if p_ask and p_ask.lower() not in starters:
        entry = palettes.get(p_ask.lower())
        if entry is None:
            return {}, (
                f"I don't know a palette called '{p_ask}'. "
                f"Valid options — {describe_options()}."
            )
        canon_p, owner = entry
        patch["palette"] = canon_p
        # A palette implies its owning variant when none was asked for —
        # applying Ember against the crystal variant is a no-op wipe.
        if "variant" not in patch and owner:
            patch["variant"] = owner

    return patch, None
