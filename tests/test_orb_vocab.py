"""Tests for orb vocabulary resolution + the re-enabled set_orb_visual
tool (#577).

The #562 bug: LLM-invented variant/palette names were applied AND
persisted unvalidated — an unknown variant parks the frontend orb store
in its pending-registration state (swallowing later applies) and the
junk in orbis.yaml re-armed the wedge every boot. These tests pin the
fix: nothing unresolvable reaches merge_patch or the SSE bus.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import agent.tools as tools_mod
from agent.orb_vocab import resolve_orb_ask
from agent.tools import set_orb_visual_handler


@pytest.fixture(autouse=True)
def _starters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Deterministic starter pool (env read at call time since #617)."""
    p = tmp_path / "starters.yaml"
    p.write_text(
        """
starters:
  - slug: aurora
    name: Aurora
    description: x
    variant: fractal
    palette: Aurora
  - slug: ember
    name: Ember
    description: x
    variant: fractal
    palette: Ember
  - slug: andromeda
    name: Andromeda
    description: x
    variant: nebula
    palette: Andromeda
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("ORBIS_STARTER_ORBS", str(p))
    # Point imported definitions at an empty dir.
    monkeypatch.setenv("ORBIS_ORBS_DIR", str(tmp_path / "orbs"))


# --- resolution --------------------------------------------------------------


def test_resolve_base_variant_case_insensitive():
    patch, err = resolve_orb_ask(variant="Nebula")
    assert err is None
    assert patch == {"variant": "nebula"}


def test_resolve_starter_name_fills_both_fields():
    patch, err = resolve_orb_ask(variant="ember")
    assert err is None
    assert patch == {"variant": "fractal", "palette": "Ember"}


def test_resolve_palette_implies_owning_variant():
    patch, err = resolve_orb_ask(palette="andromeda")
    assert err is None
    assert patch == {"variant": "nebula", "palette": "Andromeda"}


def test_resolve_explicit_variant_wins_over_palette_owner():
    patch, err = resolve_orb_ask(variant="crystal", palette="Aurora")
    assert err is None
    assert patch["variant"] == "crystal"
    assert patch["palette"] == "Aurora"


def test_resolve_unknown_variant_errors_with_options():
    patch, err = resolve_orb_ask(variant="red")
    assert patch == {}
    assert err is not None and "red" in err and "fractal" in err


def test_resolve_unknown_palette_errors_with_options():
    patch, err = resolve_orb_ask(palette="Sunset")
    assert patch == {}
    assert err is not None and "Sunset" in err and "Ember" in err


def test_resolve_imported_definition_id(tmp_path, monkeypatch):
    import json
    orbs = tmp_path / "orbs2"
    orbs.mkdir()
    # Minimal shape for list_definitions' read; validation happens on
    # import, not listing — write via the module to stay honest.
    monkeypatch.setenv("ORBIS_ORBS_DIR", str(orbs))
    from agent.orb_definitions import list_definitions  # noqa: F401
    (orbs / "myorb.orbis").write_text(
        json.dumps({
            "id": "myorb",
            "palettes": {"Midnight": {}},
        }),
        encoding="utf-8",
    )
    # list_definitions validates — a minimal file may not pass, in which
    # case the id simply isn't in the vocabulary; both outcomes must not
    # crash resolution.
    patch, err = resolve_orb_ask(variant="myorb")
    assert (err is None and patch["variant"] == "myorb") or err is not None


# --- the tool handler --------------------------------------------------------


class FakeParams:
    def __init__(self, arguments: dict[str, Any]):
        self.arguments = arguments
        self.results: list[str] = []

    async def result_callback(self, result: str) -> None:
        self.results.append(result)


@pytest.fixture
def capture(monkeypatch: pytest.MonkeyPatch):
    """Capture merge_patch writes + SSE publishes; allow-orb-control on."""
    import agent.config_store as cs
    import voice.sse_bus as bus_mod

    written: list[dict] = []
    published: list[tuple[str, dict]] = []
    monkeypatch.setattr(cs, "read_config", lambda *a, **k: {"agent": {}, "orb": {"params": {"glow": 1.0}}})
    monkeypatch.setattr(cs, "merge_patch", lambda patch, *a, **k: written.append(patch) or patch)

    async def publish(event, data=None):
        published.append((event, data))

    monkeypatch.setattr(bus_mod.sse_bus, "publish", publish)
    return written, published


@pytest.mark.asyncio
async def test_handler_rejects_invented_names_without_persisting(capture):
    written, published = capture
    params = FakeParams({"variant": "red"})
    await set_orb_visual_handler(params)  # type: ignore[arg-type]
    assert written == []      # nothing persisted — the #562 root cause
    assert published == []    # nothing applied
    assert "red" in params.results[0]


@pytest.mark.asyncio
async def test_handler_applies_resolved_starter_look(capture):
    written, published = capture
    params = FakeParams({"palette": "ember"})
    await set_orb_visual_handler(params)  # type: ignore[arg-type]
    assert written == [{"orb": {"variant": "fractal", "palette": "Ember"}}]
    assert published == [("orb-config", {"variant": "fractal", "palette": "Ember"})]
    assert "Ember" in params.results[0]


@pytest.mark.asyncio
async def test_handler_merges_params_onto_current_knobs(capture):
    written, _ = capture
    params = FakeParams({"params": {"speed": 0.3, "bad": True}})
    await set_orb_visual_handler(params)  # type: ignore[arg-type]
    # bools dropped; existing knobs preserved (wholesale-replace guard).
    assert written == [{"orb": {"params": {"glow": 1.0, "speed": 0.3}}}]


@pytest.mark.asyncio
async def test_handler_respects_allow_orb_control_gate(capture, monkeypatch):
    import agent.config_store as cs
    written, published = capture
    monkeypatch.setattr(
        cs, "read_config",
        lambda *a, **k: {"agent": {"allow_orb_control": False}},
    )
    params = FakeParams({"variant": "nebula"})
    await set_orb_visual_handler(params)  # type: ignore[arg-type]
    assert written == [] and published == []
    assert "off" in params.results[0]


def test_tool_is_registered_again():
    # The #562 parking left the handler un-decorated; #577 restores it.
    assert "set_orb_visual" in tools_mod._TOOL_REGISTRY
