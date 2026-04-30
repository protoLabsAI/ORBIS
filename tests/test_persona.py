"""Tests for the persona loader.

Covers defaults, YAML parsing, env overrides, malformed input, and
reload semantics.
"""

from __future__ import annotations

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


def test_tts_backend_case_normalized_from_yaml(tmp_path: Path):
    # Hand-edited YAML can use any case; downstream consumers
    # (MicroAckInjector ack pool, filler backend-style) string-match on
    # lowercase, so the loader must normalize. Regression: capital-K
    # "Kokoro" in YAML used to leak through and the ack pool fell
    # through to _PLAIN_ACKS instead of _KOKORO_ACKS.
    yaml_path = _write(tmp_path / "orbis.yaml", """
        voice:
          tts_backend: "  Kokoro  "
    """)
    p = load_persona(yaml_path)
    assert p.tts_backend == "kokoro"


def test_tts_backend_case_normalized_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    yaml_path = _write(tmp_path / "orbis.yaml", "")
    monkeypatch.setenv("TTS_BACKEND", "ElevenLabs")
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


# --- state/mood overrides (DECISIONS 2026-04-23) ---------------------------


def test_persona_loads_orb_state_and_mood_overrides(tmp_path: Path):
    p = tmp_path / "orbis.yaml"
    p.write_text(
        """
persona:
  name: ORBIS
orb:
  variant: fractal
  palette: Aurora
  params: {density: 2.4}
  state_overrides:
    idle:     {speed: -0.1}
    speaking: {density: 0.4, speed: 0.3}
  mood_overrides:
    valence: {atmosphereGlow: 0.2}
    arousal: {speed: 0.3}
""".lstrip(),
    )
    persona = load_persona(p)
    assert persona.orb_state_overrides["idle"]["speed"] == -0.1
    assert persona.orb_state_overrides["speaking"]["density"] == 0.4
    assert persona.orb_mood_overrides["valence"]["atmosphereGlow"] == 0.2
    assert persona.orb_mood_overrides["arousal"]["speed"] == 0.3


def test_persona_defaults_overrides_to_empty(tmp_path: Path):
    """No state_overrides / mood_overrides in yaml → empty dicts,
    not None. Keeps the frontend composition layer simple."""
    p = tmp_path / "orbis.yaml"
    p.write_text("persona: {name: ORBIS}\norb: {variant: fractal}\n")
    persona = load_persona(p)
    assert persona.orb_state_overrides == {}
    assert persona.orb_mood_overrides == {}


def test_persona_overrides_round_trip_through_write(tmp_path: Path):
    """Write a config via config_store, then load via persona loader —
    state/mood overrides survive the round-trip intact."""
    from agent.config_store import write_config

    p = tmp_path / "orbis.yaml"
    write_config({
        "persona": {"name": "ORBIS"},
        "orb": {
            "variant": "fractal",
            "state_overrides": {"idle": {"speed": -0.1}},
            "mood_overrides": {"valence": {"atmosphereGlow": 0.2}},
        },
    }, p)
    persona = load_persona(p)
    assert persona.orb_state_overrides == {"idle": {"speed": -0.1}}
    assert persona.orb_mood_overrides == {"valence": {"atmosphereGlow": 0.2}}


def test_persona_normalizes_hand_authored_override_keys(tmp_path: Path):
    """Hand-edited YAML skips config_store's validator — persona.load
    mirrors the same filtering so case + enum membership + numeric-
    only for mood deltas are enforced on the read path."""
    p = tmp_path / "orbis.yaml"
    p.write_text(
        """
orb:
  state_overrides:
    Speaking: {density: 0.4, primaryColor: "#ff00ff"}
    hyper:    {density: 9.9}
  mood_overrides:
    Valence:   {atmosphereGlow: 0.2, colorTag: "#ff0000"}
    curiosity: {speed: 0.5}
""".lstrip(),
    )
    persona = load_persona(p)
    # Case normalized, unknown keys dropped.
    assert "speaking" in persona.orb_state_overrides
    assert persona.orb_state_overrides["speaking"]["density"] == 0.4
    assert persona.orb_state_overrides["speaking"]["primaryColor"] == "#ff00ff"
    assert "hyper" not in persona.orb_state_overrides
    # Mood normalized + numeric-only (colorTag string gets dropped).
    assert "valence" in persona.orb_mood_overrides
    assert persona.orb_mood_overrides["valence"] == {"atmosphereGlow": 0.2}
    assert "curiosity" not in persona.orb_mood_overrides
