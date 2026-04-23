"""Tests for the starter orb pool loader."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from agent.starter_orbs import StarterOrb, find_starter, load_starters


def _write(p: Path, body: str) -> Path:
    p.write_text(textwrap.dedent(body).lstrip())
    return p


def test_missing_file_returns_empty_list(tmp_path: Path):
    assert load_starters(tmp_path / "absent.yaml") == []


def test_basic_pool_loads(tmp_path: Path):
    yaml_path = _write(tmp_path / "starter_orbs.yaml", """
        starters:
          - slug: aurora
            name: Aurora
            description: Shifting light.
            variant: fractal
            palette: Aurora
          - slug: ember
            name: Ember
            description: Warm fire.
            variant: fractal
            palette: Ember
    """)
    pool = load_starters(yaml_path)
    assert len(pool) == 2
    assert pool[0].slug == "aurora"
    assert pool[0].variant == "fractal"
    assert pool[1].palette == "Ember"


def test_missing_required_fields_are_skipped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
):
    yaml_path = _write(tmp_path / "starter_orbs.yaml", """
        starters:
          - slug: ok
            variant: fractal
            palette: Aurora
          - slug: ""
            variant: fractal
            palette: Ember
          - name: no-slug
            variant: fractal
            palette: Forest
    """)
    with caplog.at_level("WARNING"):
        pool = load_starters(yaml_path)
    assert [s.slug for s in pool] == ["ok"]
    assert any("malformed" in r.message for r in caplog.records)


def test_duplicate_slugs_keep_first(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
):
    yaml_path = _write(tmp_path / "starter_orbs.yaml", """
        starters:
          - slug: one
            variant: fractal
            palette: Aurora
          - slug: one
            variant: nebula
            palette: Helios
    """)
    with caplog.at_level("WARNING"):
        pool = load_starters(yaml_path)
    assert len(pool) == 1
    assert pool[0].variant == "fractal"
    assert any("duplicate slug" in r.message for r in caplog.records)


def test_malformed_yaml_falls_back_to_empty(tmp_path: Path):
    yaml_path = tmp_path / "starter_orbs.yaml"
    yaml_path.write_text("this is not: valid: yaml: : :")
    assert load_starters(yaml_path) == []


def test_find_starter_by_slug(tmp_path: Path):
    yaml_path = _write(tmp_path / "starter_orbs.yaml", """
        starters:
          - slug: aurora
            variant: fractal
            palette: Aurora
          - slug: ember
            variant: fractal
            palette: Ember
    """)
    pool = load_starters(yaml_path)
    hit = find_starter("ember", pool)
    assert hit is not None
    assert hit.slug == "ember"
    miss = find_starter("nonexistent", pool)
    assert miss is None


def test_shipped_pool_loads(tmp_path: Path):
    """Verify the checked-in config/starter_orbs.yaml parses + every
    entry has the required fields. Catches accidental edits that
    would break the setup wizard at runtime."""
    pool = load_starters("config/starter_orbs.yaml")
    assert len(pool) >= 4   # at minimum one per variant
    variants_seen = {s.variant for s in pool}
    # Every registered shader variant should have at least one starter.
    for expected in ("fractal", "nebula", "crystal", "particles"):
        assert expected in variants_seen, f"no starter for {expected!r}"
    # All slugs unique.
    slugs = [s.slug for s in pool]
    assert len(slugs) == len(set(slugs))


def test_starter_orb_to_dict_shape():
    s = StarterOrb(
        slug="x", name="X", description="desc",
        variant="fractal", palette="Aurora", params={"density": 2.0},
    )
    d = s.to_dict()
    assert set(d.keys()) == {"slug", "name", "description", "variant", "palette", "params"}
    assert d["params"] == {"density": 2.0}
