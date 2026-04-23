"""Tests for the persona loader.

Covers defaults, YAML parsing, env overrides, malformed input, and
reload semantics.
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from agent.persona import Persona, _DEFAULT_SYSTEM_PROMPT, load_persona, reload_persona


def _write(path: Path, body: str) -> Path:
    path.write_text(textwrap.dedent(body).lstrip())
    return path


def test_missing_file_returns_defaults(tmp_path: Path):
    p = load_persona(tmp_path / "absent.yaml")
    assert p.slug == "orbis"
    assert p.name == "ORBIS"
    assert p.system_prompt == _DEFAULT_SYSTEM_PROMPT
    assert p.temperature == 0.7
    assert p.max_tokens == 150


def test_full_yaml_loads(tmp_path: Path):
    yaml_path = _write(tmp_path / "orbis.yaml", """
        persona:
          slug: custom
          name: Custom
          system_prompt: "You are a custom orb."
          temperature: 0.4
          max_tokens: 200
          filler_verbosity: narrated
        voice:
          tts_backend: elevenlabs
          voice: 21m00
        orb:
          variant: nebula
          palette: Ember
          params:
            density: 1.5
            speed: 0.7
    """)
    p = load_persona(yaml_path)
    assert p.slug == "custom"
    assert p.name == "Custom"
    assert p.system_prompt == "You are a custom orb."
    assert p.temperature == 0.4
    assert p.max_tokens == 200
    assert p.filler_verbosity == "narrated"
    assert p.orb_variant == "nebula"
    assert p.orb_palette == "Ember"
    assert p.orb_params == {"density": 1.5, "speed": 0.7}


def test_system_prompt_file_preferred_when_inline_absent(tmp_path: Path):
    prompt_path = tmp_path / "persona.md"
    prompt_path.write_text("You are ORBIS, drawn from a file.\n")
    yaml_path = _write(tmp_path / "orbis.yaml", """
        persona:
          slug: filed
          system_prompt_file: persona.md
    """)
    p = load_persona(yaml_path)
    assert p.system_prompt == "You are ORBIS, drawn from a file."


def test_env_override_for_system_prompt_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    yaml_path = _write(tmp_path / "orbis.yaml", """
        persona:
          system_prompt: "YAML says this."
    """)
    monkeypatch.setenv("SYSTEM_PROMPT", "ENV says this.")
    p = load_persona(yaml_path)
    assert p.system_prompt == "ENV says this."


def test_env_override_for_tts_backend_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    yaml_path = _write(tmp_path / "orbis.yaml", """
        voice:
          tts_backend: kokoro
    """)
    monkeypatch.setenv("TTS_BACKEND", "elevenlabs")
    p = load_persona(yaml_path)
    assert p.tts_backend == "elevenlabs"


def test_malformed_yaml_falls_back_to_defaults(tmp_path: Path):
    yaml_path = tmp_path / "orbis.yaml"
    yaml_path.write_text("this is not: valid: yaml: : :")
    p = load_persona(yaml_path)
    # Should still have defaults, not raise.
    assert p.slug == "orbis"
    assert p.system_prompt == _DEFAULT_SYSTEM_PROMPT


def test_unknown_filler_verbosity_falls_back_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
):
    yaml_path = _write(tmp_path / "orbis.yaml", """
        persona:
          filler_verbosity: raucous
    """)
    with caplog.at_level("WARNING"):
        p = load_persona(yaml_path)
    assert p.filler_verbosity == "brief"
    assert any("unknown filler_verbosity" in r.message for r in caplog.records)


def test_bad_numeric_types_fall_back(tmp_path: Path):
    yaml_path = _write(tmp_path / "orbis.yaml", """
        persona:
          temperature: "hot"
          max_tokens: "many"
    """)
    p = load_persona(yaml_path)
    assert p.temperature == 0.7
    assert p.max_tokens == 150


def test_persona_viz_compat_shape(tmp_path: Path):
    yaml_path = _write(tmp_path / "orbis.yaml", """
        orb:
          variant: crystal
          palette: Noir
          params:
            density: 2.2
    """)
    p = load_persona(yaml_path)
    viz = p.viz
    assert viz["variant"] == "crystal"
    assert viz["palette"] == "Noir"
    assert viz["params"] == {"density": 2.2}


def test_reload_replaces_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    yaml_path = _write(tmp_path / "orbis.yaml", """
        persona:
          slug: first
          name: First
    """)
    monkeypatch.setenv("ORBIS_CONFIG", str(yaml_path))
    p1 = reload_persona()
    assert p1.slug == "first"

    _write(yaml_path, """
        persona:
          slug: second
          name: Second
    """)
    p2 = reload_persona()
    assert p2.slug == "second"


def test_persona_is_immutable():
    p = Persona(slug="x", name="X", system_prompt="hello")
    with pytest.raises(Exception):
        p.slug = "y"  # frozen dataclass
