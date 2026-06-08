"""Widget catalog (config/widgets.yaml) loads and stays in lockstep with the
web render layer.

The catalog is the single source the voice sidecar reads (agent/widgets.py);
each entry's render lives in web/src/widgets/<id>/index.tsx. These tests fail
loud if the two drift — a widget declared on only one side is a bug.
"""

from __future__ import annotations

from pathlib import Path

from agent.widgets import known_widget_ids, load_widgets, render_catalog_text

REPO = Path(__file__).resolve().parents[1]
WIDGETS_YAML = REPO / "config" / "widgets.yaml"
WEB_WIDGETS = REPO / "web" / "src" / "widgets"


def test_catalog_loads_and_has_weather():
    widgets = load_widgets(WIDGETS_YAML)
    assert "weather" in {w.id for w in widgets}
    weather = next(w for w in widgets if w.id == "weather")
    assert any(p.name == "location" for p in weather.props)


def test_catalog_text_and_known_ids():
    widgets = load_widgets(WIDGETS_YAML)
    assert known_widget_ids(widgets) == {w.id for w in widgets}
    text = render_catalog_text(widgets)
    assert "weather" in text
    assert "location" in text


def test_loader_is_resilient_to_missing_file():
    assert load_widgets(REPO / "config" / "does-not-exist.yaml") == []


def _web_widget_ids() -> set[str]:
    """Widget folders the frontend auto-discovers: web/src/widgets/<id>/index.tsx."""
    return {
        d.name
        for d in WEB_WIDGETS.iterdir()
        if d.is_dir() and (d / "index.tsx").exists()
    }


def test_catalog_matches_web_render_folders():
    """The yaml catalog and the web render folders must declare the same set of
    widget ids — neither side may drift. Add a widget in both, or in neither."""
    catalog = known_widget_ids(load_widgets(WIDGETS_YAML))
    web = _web_widget_ids()
    missing_render = catalog - web
    missing_catalog = web - catalog
    assert not missing_render, (
        "widgets in config/widgets.yaml with no web/src/widgets/<id>/index.tsx "
        f"render: {sorted(missing_render)}"
    )
    assert not missing_catalog, (
        "widget render folders with no config/widgets.yaml entry "
        f"(voice can't open them): {sorted(missing_catalog)}"
    )
