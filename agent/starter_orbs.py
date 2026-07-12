"""Starter orb pool loader.

Reads ``config/starter_orbs.yaml`` and exposes the curated pool the
setup wizard presents to new users. Fresh installs pick one of these;
the picked orb becomes the ``orb:`` section of ``config/orbis.yaml``.

Non-paid users can't customize beyond their starter; the paid
customization unlock lifts that restriction (see DECISIONS.md).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

DEFAULT_PATH = "config/starter_orbs.yaml"


@dataclass(frozen=True)
class StarterOrb:
    slug: str
    name: str
    description: str
    variant: str
    palette: str
    params: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "variant": self.variant,
            "palette": self.palette,
            "params": dict(self.params),
        }


def load_starters(path: str | Path | None = None) -> list[StarterOrb]:
    """Read the starter pool YAML. Returns [] on any error (never raises).

    ``ORBIS_STARTER_ORBS`` is read at call time (not import time) so
    late importers — e.g. agent/personas.py resolving an ``orb:`` ref —
    see the same file the wizard does regardless of import order.
    """
    p = Path(path or os.environ.get("ORBIS_STARTER_ORBS") or DEFAULT_PATH)
    if not p.exists():
        logger.info(f"[starter_orbs] {p} not found; no starters available")
        return []

    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as e:
        logger.error(f"[starter_orbs] failed to parse {p}: {e}")
        return []

    raw = data.get("starters") or []
    if not isinstance(raw, list):
        logger.warning(f"[starter_orbs] {p}: 'starters' is not a list")
        return []

    starters: list[StarterOrb] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        slug = (entry.get("slug") or "").strip()
        variant = (entry.get("variant") or "").strip()
        palette = (entry.get("palette") or "").strip()
        if not (slug and variant and palette):
            logger.warning(f"[starter_orbs] skipping malformed entry: {entry!r}")
            continue
        if slug in seen:
            logger.warning(f"[starter_orbs] duplicate slug {slug!r}; keeping first")
            continue
        seen.add(slug)
        name = (entry.get("name") or slug.title()).strip()
        description = (entry.get("description") or "").strip()
        params_raw = entry.get("params") or {}
        params = dict(params_raw) if isinstance(params_raw, dict) else {}
        starters.append(StarterOrb(
            slug=slug,
            name=name,
            description=description,
            variant=variant,
            palette=palette,
            params=params,
        ))

    logger.info(
        f"[starter_orbs] loaded {len(starters)} starter(s) from {p}"
    )
    return starters


def find_starter(slug: str, starters: list[StarterOrb] | None = None) -> StarterOrb | None:
    """Look up a starter by slug. Returns None if not found."""
    for s in (starters if starters is not None else load_starters()):
        if s.slug == slug:
            return s
    return None
