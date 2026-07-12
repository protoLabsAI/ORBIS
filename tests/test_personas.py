"""Tests for the persona catalog (agent/personas.py, epic #611 P1).

Mirrors the protoVoice skills/loader.py coverage the mechanic descends
from: parsing, discovery + shadowing, extends resolution, composition
over the default persona, and the authoring write path.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from agent.persona import Persona, load_persona
from agent.personas import (
    PersonaFile,
    compose_persona,
    delete_persona_file,
    load_persona_files,
    parse_frontmatter,
    serialize_persona_md,
    write_persona_file,
)


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    return path


_DEFAULT = Persona(
    slug="orbis",
    name="ORBIS",
    user_name="Josh",
    system_prompt="You are ORBIS.",
    temperature=0.7,
    tts_backend="kokoro",
    voice="af_heart",
    llm={"url": "https://gateway/v1", "model": "primary", "api_key": "sk-real"},
    orb_variant="fractal",
    orb_palette="Aurora",
)


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch: pytest.MonkeyPatch):
    """Persona-related env from the developer's machine must not leak in."""
    for var in (
        "ORBIS_PERSONAS_DIR", "ORBIS_BUNDLED_PERSONAS",
        "ORBIS_STARTER_ORBS", "SYSTEM_PROMPT",
    ):
        monkeypatch.delenv(var, raising=False)


# --- frontmatter parsing -----------------------------------------------------


def test_parse_valid_frontmatter():
    meta, body = parse_frontmatter("---\nname: Chef\n---\n\nThe prompt.\n")
    assert meta == {"name": "Chef"}
    assert body == "The prompt."


def test_parse_no_frontmatter_is_bare_prompt():
    meta, body = parse_frontmatter("Just a prompt, no fences.")
    assert meta == {}
    assert body == "Just a prompt, no fences."


def test_parse_unterminated_fence_is_invalid():
    assert parse_frontmatter("---\nname: Chef\n\nThe prompt.") is None


def test_parse_bad_yaml_is_invalid():
    assert parse_frontmatter("---\nname: [unclosed\n---\nprompt") is None


def test_parse_non_mapping_frontmatter_is_invalid():
    assert parse_frontmatter("---\n- a\n- list\n---\nprompt") is None


# --- discovery + shadowing ---------------------------------------------------


def test_discovery_across_both_dirs(tmp_path: Path):
    _write(tmp_path / "bundled" / "bruno.md", """
        ---
        name: Chef Bruno
        ---
        Bundled Bruno prompt.
    """)
    _write(tmp_path / "user" / "custom.md", """
        ---
        name: Custom
        ---
        User persona prompt.
    """)
    files = load_persona_files(
        bundled=tmp_path / "bundled", user=tmp_path / "user",
    )
    assert set(files) == {"bruno", "custom"}
    assert files["bruno"].source == "bundled"
    assert files["custom"].source == "user"


def test_user_file_shadows_bundled(tmp_path: Path):
    _write(tmp_path / "bundled" / "bruno.md", "Bundled prompt.")
    _write(tmp_path / "user" / "bruno.md", "User-edited prompt.")
    files = load_persona_files(
        bundled=tmp_path / "bundled", user=tmp_path / "user",
    )
    assert files["bruno"].source == "user"
    assert files["bruno"].body == "User-edited prompt."


def test_reserved_and_invalid_slugs_skipped(tmp_path: Path):
    d = tmp_path / "personas"
    _write(d / "default.md", "Nope — reserved.")
    _write(d / "UPPER CASE.md", "Nope — invalid slug.")
    _write(d / "ok.md", "Fine.")
    files = load_persona_files(bundled=d, user=d)
    assert set(files) == {"ok"}


def test_unreadable_file_skipped_not_fatal(tmp_path: Path):
    d = tmp_path / "personas"
    _write(d / "bad.md", "---\nname: [unclosed\n---\nprompt")
    _write(d / "good.md", "A valid prompt.")
    files = load_persona_files(bundled=d, user=d)
    assert set(files) == {"good"}


# --- composition -------------------------------------------------------------


def _files(**slug_to_text: str) -> dict[str, PersonaFile]:
    out = {}
    for slug, text in slug_to_text.items():
        parsed = parse_frontmatter(textwrap.dedent(text).lstrip())
        assert parsed is not None, slug
        meta, body = parsed
        from agent.personas import _filter_meta
        out[slug] = PersonaFile(
            slug=slug, source="user", path=Path(f"{slug}.md"),
            meta=_filter_meta(meta, origin=slug), body=body,
        )
    return out


def test_compose_overlays_and_inherits(tmp_path: Path):
    files = _files(bruno="""
        ---
        name: Chef Bruno
        voice:
          voice: am_michael
        temperature: 0.9
        ---
        You are Chef Bruno.
    """)
    p = compose_persona("bruno", _DEFAULT, files)
    assert p is not None
    assert p.slug == "bruno"
    assert p.name == "Chef Bruno"
    assert p.system_prompt == "You are Chef Bruno."
    assert p.voice == "am_michael"
    assert p.temperature == 0.9
    # Inherited from the default:
    assert p.tts_backend == "kokoro"
    assert p.max_tokens == _DEFAULT.max_tokens
    assert p.user_name == "Josh"          # machine-level, never persona
    assert p.llm == _DEFAULT.llm
    assert p.orb_variant == "fractal"     # no orb: in the file
    assert p.active_persona == ""         # pointer never survives composition


def test_compose_empty_body_inherits_prompt():
    files = _files(clone="""
        ---
        name: Voice Clone
        voice:
          voice: am_adam
        ---
    """)
    p = compose_persona("clone", _DEFAULT, files)
    assert p is not None
    assert p.system_prompt == "You are ORBIS."
    assert p.voice == "am_adam"


def test_compose_llm_one_level_merge():
    files = _files(fast="""
        ---
        llm:
          model: other-model
        ---
        Prompt.
    """)
    p = compose_persona("fast", _DEFAULT, files)
    assert p is not None
    # model overridden; url + key inherited from the default's llm.
    assert p.llm["model"] == "other-model"
    assert p.llm["url"] == "https://gateway/v1"
    assert p.llm["api_key"] == "sk-real"


def test_compose_strips_llm_api_key_from_file():
    # Persona files are shareable text — a pasted api_key must not load.
    files = _files(leaky="""
        ---
        llm:
          model: other-model
          api_key: sk-should-not-load
          fallback:
            url: http://backup/v1
            api_key: sk-also-not
        ---
        Prompt.
    """)
    p = compose_persona("leaky", _DEFAULT, files)
    assert p is not None
    assert p.llm["api_key"] == "sk-real"  # the default's, not the file's
    assert "api_key" not in p.llm["fallback"]
    assert p.llm["fallback"]["url"] == "http://backup/v1"


def test_compose_orb_starter_ref(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    starters = _write(tmp_path / "starters.yaml", """
        starters:
          - slug: ember
            name: Ember
            description: Warm.
            variant: fractal
            palette: Ember
            params: {density: 2.4}
    """)
    monkeypatch.setenv("ORBIS_STARTER_ORBS", str(starters))
    files = _files(bruno="""
        ---
        orb: ember
        ---
        Prompt.
    """)
    p = compose_persona("bruno", _DEFAULT, files)
    assert p is not None
    assert p.orb_variant == "fractal"
    assert p.orb_palette == "Ember"
    assert p.orb_params == {"density": 2.4}


def test_compose_orb_unknown_ref_passes_through_as_variant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    # Not a starter slug → treated as a variant / imported .orbis id;
    # the frontend registry lookup fails gracefully on true unknowns.
    monkeypatch.setenv("ORBIS_STARTER_ORBS", str(tmp_path / "absent.yaml"))
    files = _files(custom="""
        ---
        orb: my-imported-orb
        ---
        Prompt.
    """)
    p = compose_persona("custom", _DEFAULT, files)
    assert p is not None
    assert p.orb_variant == "my-imported-orb"
    assert p.orb_palette is None
    assert p.orb_params == {}


def test_compose_orb_inline_dict():
    files = _files(inline="""
        ---
        orb:
          variant: nebula
          palette: Helios
          params: {speed: 0.3}
        ---
        Prompt.
    """)
    p = compose_persona("inline", _DEFAULT, files)
    assert p is not None
    assert p.orb_variant == "nebula"
    assert p.orb_palette == "Helios"
    assert p.orb_params == {"speed": 0.3}


def test_compose_extends_chain():
    files = _files(
        base="""
            ---
            name: Base
            voice:
              voice: am_michael
            temperature: 0.9
            ---
            Base prompt.
        """,
        child="""
            ---
            name: Child
            extends: base
            temperature: 0.5
            ---
        """,
    )
    p = compose_persona("child", _DEFAULT, files)
    assert p is not None
    assert p.name == "Child"
    assert p.system_prompt == "Base prompt."   # inherited through the chain
    assert p.voice == "am_michael"             # from base
    assert p.temperature == 0.5                # leaf wins
    assert p.tts_backend == "kokoro"           # from the default at the root


def test_compose_extends_cycle_returns_none():
    files = _files(
        a="---\nextends: b\n---\nA.",
        b="---\nextends: a\n---\nB.",
    )
    assert compose_persona("a", _DEFAULT, files) is None


def test_compose_extends_null_opts_out_of_default():
    files = _files(bare="""
        ---
        extends: null
        ---
        Standalone prompt.
    """)
    p = compose_persona("bare", _DEFAULT, files)
    assert p is not None
    assert p.system_prompt == "Standalone prompt."
    assert p.llm is None            # nothing inherited from the default
    assert p.tts_backend is None


def test_compose_extends_null_without_prompt_is_unusable():
    files = _files(empty="---\nextends: null\n---\n")
    assert compose_persona("empty", _DEFAULT, files) is None


def test_compose_broken_extends_degrades_to_default():
    files = _files(orphan="""
        ---
        extends: gone
        ---
        Orphan prompt.
    """)
    p = compose_persona("orphan", _DEFAULT, files)
    assert p is not None
    assert p.system_prompt == "Orphan prompt."
    assert p.tts_backend == "kokoro"  # default still the base


def test_compose_unknown_slug_returns_none():
    assert compose_persona("ghost", _DEFAULT, {}) is None


def test_compose_reserved_slug_returns_default():
    assert compose_persona("default", _DEFAULT, {}) is _DEFAULT


# --- active-persona integration (yaml → composed) ----------------------------


def test_active_persona_composes_via_load_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    d = tmp_path / "personas"
    _write(d / "bruno.md", """
        ---
        name: Chef Bruno
        ---
        Bruno prompt.
    """)
    monkeypatch.setenv("ORBIS_PERSONAS_DIR", str(d))
    monkeypatch.setenv("ORBIS_BUNDLED_PERSONAS", str(d))
    yaml_path = _write(tmp_path / "orbis.yaml", """
        persona:
          name: ORBIS
          system_prompt: Default prompt.
          active_persona: bruno
    """)
    from agent.persona import _compose_active
    p = _compose_active(load_persona(yaml_path))
    assert p.slug == "bruno"
    assert p.name == "Chef Bruno"
    assert p.system_prompt == "Bruno prompt."


def test_active_persona_missing_falls_back_to_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ORBIS_PERSONAS_DIR", str(tmp_path / "absent"))
    monkeypatch.setenv("ORBIS_BUNDLED_PERSONAS", str(tmp_path / "absent"))
    yaml_path = _write(tmp_path / "orbis.yaml", """
        persona:
          name: ORBIS
          system_prompt: Default prompt.
          active_persona: gone
    """)
    from agent.persona import _compose_active
    p = _compose_active(load_persona(yaml_path))
    assert p.slug == "orbis"
    assert p.system_prompt == "Default prompt."


def test_active_persona_round_trips_config_store(tmp_path: Path):
    from agent.config_store import validate_and_normalize, write_config, read_config
    out = validate_and_normalize({"persona": {"active_persona": "bruno"}})
    assert out["persona"]["active_persona"] == "bruno"
    p = tmp_path / "orbis.yaml"
    write_config({"persona": {"active_persona": "bruno"}}, p)
    assert read_config(p)["persona"]["active_persona"] == "bruno"


# --- authoring (serialize / write / delete) ----------------------------------


def test_serialize_write_read_round_trip(tmp_path: Path):
    meta = {
        "name": "Custom",
        "description": "Mine.",
        "voice": {"voice": "af_sky"},
        "orb": "ember",
        "temperature": 0.8,
    }
    path = write_persona_file(
        "custom", meta, "The custom prompt.", user_dir=tmp_path,
    )
    assert path == tmp_path / "custom.md"
    files = load_persona_files(bundled=tmp_path, user=tmp_path)
    pf = files["custom"]
    assert pf.name == "Custom"
    assert pf.meta["voice"] == {"voice": "af_sky"}
    assert pf.meta["orb"] == "ember"
    assert pf.body == "The custom prompt."


def test_write_strips_secrets_and_unknown_keys(tmp_path: Path):
    write_persona_file(
        "custom",
        {"name": "X", "llm": {"model": "m", "api_key": "sk-no"}, "bogus": 1},
        "Prompt.",
        user_dir=tmp_path,
    )
    text = (tmp_path / "custom.md").read_text()
    assert "sk-no" not in text
    assert "bogus" not in text


def test_write_rejects_reserved_and_bad_slugs(tmp_path: Path):
    with pytest.raises(ValueError, match="reserved"):
        write_persona_file("default", {}, "Prompt.", user_dir=tmp_path)
    with pytest.raises(ValueError, match="slug"):
        write_persona_file("Bad Slug!", {}, "Prompt.", user_dir=tmp_path)


def test_delete_user_file_and_missing(tmp_path: Path):
    write_persona_file("custom", {"name": "X"}, "Prompt.", user_dir=tmp_path)
    assert delete_persona_file("custom", user_dir=tmp_path) is True
    assert delete_persona_file("custom", user_dir=tmp_path) is False


# --- the shipped starters must actually load ---------------------------------


def test_shipped_starters_parse_and_compose():
    # Repo-relative like test_checked_in_example_loads_bundled_persona_prompt.
    files = load_persona_files(bundled="config/personas", user="config/personas")
    assert {"bruno", "sage"} <= set(files)
    for slug in ("bruno", "sage"):
        p = compose_persona(slug, _DEFAULT, files)
        assert p is not None, slug
        assert p.system_prompt, slug
        assert p.voice, slug
        # Both reference real starter-orb slugs → full viz resolution.
        assert p.orb_palette in ("Ember", "Forest"), slug
