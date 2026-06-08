"""Widget catalog loader.

Reads ``config/widgets.yaml`` — the single source for which on-screen widgets
the voice agent can render and what props each accepts. The matching React
render lives in ``web/src/widgets/<id>/index.tsx``; the ``id`` links the two
(``tests/test_widgets.py`` asserts they stay in lockstep).

Consumed by ``agent/tools.py`` to gate the ``render_widget`` tool and to teach
the model the available widgets. Mirrors ``agent/starter_orbs.py``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

DEFAULT_PATH = os.environ.get("ORBIS_WIDGETS", "config/widgets.yaml")


@dataclass(frozen=True)
class WidgetProp:
    name: str
    description: str = ""


@dataclass(frozen=True)
class WidgetCatalogEntry:
    id: str
    title: str
    description: str = ""
    props: tuple[WidgetProp, ...] = ()


def load_widgets(path: str | Path | None = None) -> list[WidgetCatalogEntry]:
    """Read the widget catalog YAML. Returns [] on any error (never raises)."""
    p = Path(path) if path else Path(DEFAULT_PATH)
    if not p.exists():
        logger.info(f"[widgets] {p} not found; no widgets available")
        return []

    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as e:
        logger.error(f"[widgets] failed to parse {p}: {e}")
        return []

    raw = data.get("widgets") or []
    if not isinstance(raw, list):
        logger.warning(f"[widgets] {p}: 'widgets' is not a list")
        return []

    out: list[WidgetCatalogEntry] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        wid = (entry.get("id") or "").strip().lower()
        if not wid:
            logger.warning(f"[widgets] skipping entry with no id: {entry!r}")
            continue
        if wid in seen:
            logger.warning(f"[widgets] duplicate id {wid!r}; keeping first")
            continue
        seen.add(wid)
        title = (entry.get("title") or wid.title()).strip()
        description = (entry.get("description") or "").strip()
        props_raw = entry.get("props") or []
        props: list[WidgetProp] = []
        if isinstance(props_raw, list):
            for pr in props_raw:
                if not isinstance(pr, dict):
                    continue
                pname = (pr.get("name") or "").strip()
                if not pname:
                    continue
                props.append(WidgetProp(pname, (pr.get("description") or "").strip()))
        out.append(WidgetCatalogEntry(wid, title, description, tuple(props)))

    logger.info(f"[widgets] loaded {len(out)} widget(s) from {p}")
    return out


def known_widget_ids(
    widgets: list[WidgetCatalogEntry] | None = None,
) -> frozenset[str]:
    """The set of widget ids the model is allowed to render."""
    ws = widgets if widgets is not None else load_widgets()
    return frozenset(w.id for w in ws)


def render_catalog_text(
    widgets: list[WidgetCatalogEntry] | None = None,
) -> str:
    """One-line catalog for the render_widget tool description, e.g.
    ``weather (props: location — a city name)``. Empty string if none."""
    ws = widgets if widgets is not None else load_widgets()
    parts: list[str] = []
    for w in ws:
        if w.props:
            props = ", ".join(
                f"{p.name} — {p.description}" if p.description else p.name
                for p in w.props
            )
            parts.append(f"{w.id} (props: {props})")
        else:
            parts.append(w.id)
    return "; ".join(parts)
