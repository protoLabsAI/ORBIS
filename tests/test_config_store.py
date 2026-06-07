"""Tests for the config_store read/write helpers."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

import yaml

from agent.config_store import (
    merge_patch,
    read_config,
    validate_and_normalize,
    write_config,
)


def _write(p: Path, body: str) -> Path:
    p.write_text(textwrap.dedent(body).lstrip())
    return p


# --- read_config ------------------------------------------------------------


def test_read_missing_file_returns_empty(tmp_path: Path):
    assert read_config(tmp_path / "absent.yaml") == {}


def test_read_valid_yaml(tmp_path: Path):
    p = _write(tmp_path / "orbis.yaml", """
        persona:
          slug: test
          name: Test
        voice:
          tts_backend: kokoro
    """)
    data = read_config(p)
    assert data["persona"]["slug"] == "test"
    assert data["voice"]["tts_backend"] == "kokoro"


def test_read_malformed_yaml_returns_empty(tmp_path: Path):
    p = tmp_path / "orbis.yaml"
    p.write_text("this is not: valid: yaml: : :")
    assert read_config(p) == {}


# --- validate_and_normalize -------------------------------------------------


def test_validate_strips_unknown_top_level_keys(
    caplog: pytest.LogCaptureFixture,
):
    with caplog.at_level("WARNING"):
        out = validate_and_normalize({
            "persona": {"name": "X"},
            "strangers": {"hello": "world"},
        })
    assert "strangers" not in out
    assert any("unknown top-level key" in r.message for r in caplog.records)


def test_validate_strips_unknown_persona_keys(
    caplog: pytest.LogCaptureFixture,
):
    with caplog.at_level("WARNING"):
        out = validate_and_normalize({
            "persona": {"name": "X", "mystery": 1},
        })
    assert "mystery" not in out["persona"]


def test_validate_rejects_non_numeric_temperature():
    with pytest.raises(ValueError, match="temperature"):
        validate_and_normalize({"persona": {"temperature": "hot"}})


def test_validate_rejects_bad_tts_backend():
    with pytest.raises(ValueError, match="tts_backend"):
        validate_and_normalize({"voice": {"tts_backend": "festival"}})


def test_validate_accepts_voice_tts_endpoint_overrides():
    out = validate_and_normalize({
        "voice": {
            "tts_backend": "OpenAI",
            "voice": "nova",
            "tts_url": " http://localhost:8080/v1 ",
            "tts_model": " local-tts ",
            "tts_api_key": " test-key ",
        },
    })
    assert out["voice"] == {
        "tts_backend": "openai",
        "voice": "nova",
        "tts_url": "http://localhost:8080/v1",
        "tts_model": "local-tts",
        "tts_api_key": "test-key",
    }


def test_validate_voice_tts_endpoint_blanks_clear_to_none():
    out = validate_and_normalize({
        "voice": {
            "tts_backend": "openai",
            "tts_url": " ",
            "tts_model": "",
            "tts_api_key": "   ",
        },
    })
    assert out["voice"]["tts_url"] is None
    assert out["voice"]["tts_model"] is None
    assert out["voice"]["tts_api_key"] is None


def test_validate_accepts_stt_block():
    out = validate_and_normalize({
        "stt": {
            "backend": "OpenAI",
            "whisper_model": " openai/whisper-large-v3-turbo ",
            "url": " http://localhost:8080/v1 ",
            "model": " whisper-large ",
            "api_key": " test-key ",
        },
    })
    assert out["stt"] == {
        "backend": "openai",
        "whisper_model": "openai/whisper-large-v3-turbo",
        "url": "http://localhost:8080/v1",
        "model": "whisper-large",
        "api_key": "test-key",
    }


def test_validate_rejects_bad_stt_backend():
    with pytest.raises(ValueError, match="stt.backend"):
        validate_and_normalize({"stt": {"backend": "browser"}})


def test_validate_rejects_bad_verbosity():
    with pytest.raises(ValueError, match="filler_verbosity"):
        validate_and_normalize({"persona": {"filler_verbosity": "raucous"}})


def test_validate_accepts_full_config():
    data = {
        "persona": {
            "slug": "x", "name": "X", "user_name": "Alice",
            "system_prompt": "hi",
            "temperature": 0.5, "max_tokens": 180,
            "filler_verbosity": "narrated",
        },
        "voice": {"tts_backend": "elevenlabs", "voice": "abc"},
        "llm": {
            "url": "https://api.openai.com/v1",
            "model": "gpt-4o-mini",
            "api_key": "sk-test",
        },
        "orb": {
            "variant": "nebula",
            "palette": "Helios",
            "params": {"density": 1.7, "note": "pretty"},
        },
    }
    out = validate_and_normalize(data)
    assert out["persona"]["user_name"] == "Alice"
    assert out["persona"]["temperature"] == 0.5
    assert out["voice"]["tts_backend"] == "elevenlabs"
    assert out["llm"]["url"] == "https://api.openai.com/v1"
    assert out["llm"]["api_key"] == "sk-test"
    assert out["orb"]["params"]["density"] == 1.7


def test_validate_llm_strips_unknown_keys(caplog: pytest.LogCaptureFixture):
    with caplog.at_level("WARNING"):
        out = validate_and_normalize({
            "llm": {"url": "http://x", "mystery": "field"},
        })
    assert "mystery" not in out["llm"]


def test_validate_llm_rejects_non_dict_extra_body():
    with pytest.raises(ValueError, match="extra_body"):
        validate_and_normalize({
            "llm": {"url": "http://x", "extra_body": "not-a-dict"},
        })


def test_validate_llm_accepts_env_ref_or_direct_key():
    # Either api_key or api_key_env is valid — the run_bot path prefers
    # api_key when both are present (direct wins; docstring'd).
    o1 = validate_and_normalize({"llm": {"url": "http://x", "api_key_env": "FOO"}})
    assert o1["llm"]["api_key_env"] == "FOO"
    o2 = validate_and_normalize({"llm": {"url": "http://x", "api_key": "sk-..."}})
    assert o2["llm"]["api_key"] == "sk-..."


def test_validate_drops_complex_param_types(
    caplog: pytest.LogCaptureFixture,
):
    with caplog.at_level("WARNING"):
        out = validate_and_normalize({
            "orb": {
                "variant": "fractal",
                "palette": "Aurora",
                "params": {"density": 2.0, "matrix": [[1, 2], [3, 4]]},
            },
        })
    assert "density" in out["orb"]["params"]
    assert "matrix" not in out["orb"]["params"]


# --- state/mood override round-trip (DECISIONS 2026-04-23) ------------------


def test_validate_accepts_state_and_mood_overrides():
    out = validate_and_normalize({
        "orb": {
            "variant": "fractal", "palette": "Aurora",
            "params": {"density": 2.0},
            "state_overrides": {
                "idle":     {"speed": -0.1},
                "speaking": {"density": 0.4, "speed": 0.3},
            },
            "mood_overrides": {
                "valence":     {"atmosphereGlow": 0.2},
                "arousal":     {"speed": 0.3},
                "guardedness": {"density": -0.2},
            },
        },
    })
    assert out["orb"]["state_overrides"]["idle"]["speed"] == -0.1
    assert out["orb"]["state_overrides"]["speaking"]["density"] == 0.4
    assert out["orb"]["mood_overrides"]["valence"]["atmosphereGlow"] == 0.2
    assert out["orb"]["mood_overrides"]["arousal"]["speed"] == 0.3


def test_state_overrides_drop_unknown_state_key(
    caplog: pytest.LogCaptureFixture,
):
    with caplog.at_level("WARNING"):
        out = validate_and_normalize({
            "orb": {
                "state_overrides": {
                    "idle":   {"speed": -0.1},
                    "hyper":  {"density": 9.9},   # not a real voice state
                },
            },
        })
    assert "idle" in out["orb"]["state_overrides"]
    assert "hyper" not in out["orb"]["state_overrides"]


def test_mood_overrides_drop_unknown_dim(
    caplog: pytest.LogCaptureFixture,
):
    with caplog.at_level("WARNING"):
        out = validate_and_normalize({
            "orb": {
                "mood_overrides": {
                    "valence":   {"atmosphereGlow": 0.2},
                    "curiosity": {"speed": 0.5},   # not a real mood dim
                },
            },
        })
    assert "valence" in out["orb"]["mood_overrides"]
    assert "curiosity" not in out["orb"]["mood_overrides"]


def test_state_overrides_reject_non_mapping():
    with pytest.raises(ValueError, match="state_overrides must be a mapping"):
        validate_and_normalize({"orb": {"state_overrides": "not-a-dict"}})


def test_mood_overrides_reject_non_mapping():
    with pytest.raises(ValueError, match="mood_overrides must be a mapping"):
        validate_and_normalize({"orb": {"mood_overrides": "not-a-dict"}})


def test_override_param_deltas_drop_complex_types(
    caplog: pytest.LogCaptureFixture,
):
    with caplog.at_level("WARNING"):
        out = validate_and_normalize({
            "orb": {
                "state_overrides": {
                    "idle": {"speed": -0.1, "matrix": [[1, 2]]},
                },
            },
        })
    assert out["orb"]["state_overrides"]["idle"]["speed"] == -0.1
    assert "matrix" not in out["orb"]["state_overrides"]["idle"]


def test_state_override_normalizes_case():
    out = validate_and_normalize({
        "orb": {"state_overrides": {"IDLE": {"speed": -0.1}}},
    })
    assert "idle" in out["orb"]["state_overrides"]


def test_mood_overrides_reject_non_numeric_deltas(
    caplog: pytest.LogCaptureFixture,
):
    """Mood deltas are multiplied by the live mood scalar — a string
    or bool can't be scaled, so the validator drops them. State
    deltas still accept strings (replacement semantics, e.g. color
    hex)."""
    with caplog.at_level("WARNING"):
        out = validate_and_normalize({
            "orb": {
                "mood_overrides": {
                    "valence": {
                        "atmosphereGlow": 0.2,  # kept
                        "primaryColor": "#ff00ff",  # dropped — can't scale a string
                        "enabled": True,  # dropped — bool is nonsensical here
                    },
                },
            },
        })
    mood = out["orb"]["mood_overrides"]["valence"]
    assert mood == {"atmosphereGlow": 0.2}


def test_state_overrides_still_accept_string_replacements():
    """State deltas keep string/bool support (replacement, not scaling)
    so authors can swap color hex per voice state."""
    out = validate_and_normalize({
        "orb": {
            "state_overrides": {
                "speaking": {"primaryColor": "#ff00ff", "density": 0.4},
            },
        },
    })
    state = out["orb"]["state_overrides"]["speaking"]
    assert state["primaryColor"] == "#ff00ff"
    assert state["density"] == 0.4


# --- write_config -----------------------------------------------------------


def test_write_round_trip(tmp_path: Path):
    p = tmp_path / "orbis.yaml"
    data = {"persona": {"slug": "round", "name": "Round", "temperature": 0.3}}
    normalized = write_config(data, p)
    assert p.exists()
    reread = yaml.safe_load(p.read_text())
    assert reread["persona"]["name"] == "Round"
    assert normalized["persona"]["temperature"] == 0.3


def test_write_rejects_invalid_payload(tmp_path: Path):
    p = tmp_path / "orbis.yaml"
    with pytest.raises(ValueError):
        write_config({"voice": {"tts_backend": "nope"}}, p)
    assert not p.exists()  # failed write leaves no artifact


def test_write_is_atomic(tmp_path: Path):
    """Write creates the file fresh or replaces it; no .tmp lingers."""
    p = tmp_path / "orbis.yaml"
    write_config({"persona": {"name": "First"}}, p)
    write_config({"persona": {"name": "Second"}}, p)
    reread = yaml.safe_load(p.read_text())
    assert reread["persona"]["name"] == "Second"
    # No .tmp* left behind.
    tmps = list(tmp_path.glob(".tmp*")) + list(tmp_path.glob("*.tmp"))
    assert tmps == []


# --- merge_patch ------------------------------------------------------------


def test_merge_patch_preserves_untouched_blocks(tmp_path: Path):
    p = _write(tmp_path / "orbis.yaml", """
        persona:
          slug: x
          name: X
          temperature: 0.7
        voice:
          tts_backend: kokoro
          voice: af_heart
        orb:
          variant: fractal
          palette: Aurora
    """)
    # Patch only orb; persona + voice should survive intact.
    merge_patch({"orb": {"palette": "Ember"}}, p)
    final = yaml.safe_load(p.read_text())
    assert final["persona"]["name"] == "X"
    assert final["voice"]["tts_backend"] == "kokoro"
    assert final["orb"]["palette"] == "Ember"
    assert final["orb"]["variant"] == "fractal"  # preserved


def test_merge_patch_shallow_merges_within_block(tmp_path: Path):
    p = _write(tmp_path / "orbis.yaml", """
        persona:
          slug: x
          name: X
          temperature: 0.7
    """)
    merge_patch({"persona": {"name": "Y"}}, p)
    final = yaml.safe_load(p.read_text())
    assert final["persona"]["name"] == "Y"
    assert final["persona"]["slug"] == "x"
    assert final["persona"]["temperature"] == 0.7


def test_merge_patch_creates_block_if_missing(tmp_path: Path):
    p = _write(tmp_path / "orbis.yaml", """
        persona:
          slug: x
    """)
    merge_patch({"orb": {"variant": "crystal", "palette": "Prism"}}, p)
    final = yaml.safe_load(p.read_text())
    assert final["orb"]["variant"] == "crystal"


# --- wakeword ---------------------------------------------------------------


def test_validate_wakeword_normalizes_types():
    out = validate_and_normalize(
        {"wakeword": {"enabled": 1, "model": "  hey_orbis  ", "threshold": "0.6"}}
    )
    assert out["wakeword"] == {
        "enabled": True,
        "model": "hey_orbis",
        "threshold": 0.6,
    }


def test_validate_wakeword_clamps_threshold():
    assert validate_and_normalize(
        {"wakeword": {"threshold": 5}}
    )["wakeword"]["threshold"] == 1.0
    assert validate_and_normalize(
        {"wakeword": {"threshold": -2}}
    )["wakeword"]["threshold"] == 0.0


def test_validate_wakeword_bad_threshold_raises():
    with pytest.raises(ValueError):
        validate_and_normalize({"wakeword": {"threshold": "loud"}})


def test_validate_wakeword_drops_unknown_key(
    caplog: pytest.LogCaptureFixture,
):
    out = validate_and_normalize(
        {"wakeword": {"enabled": True, "bogus": "nope"}}
    )
    assert out["wakeword"] == {"enabled": True}
    assert "bogus" in caplog.text


def test_merge_patch_wakeword_preserves_other_blocks(tmp_path: Path):
    p = _write(tmp_path / "orbis.yaml", """
        persona:
          slug: x
          name: X
        stt:
          backend: parakeet
    """)
    merge_patch({"wakeword": {"enabled": True, "model": "hey_orbis"}}, p)
    final = yaml.safe_load(p.read_text())
    assert final["wakeword"] == {"enabled": True, "model": "hey_orbis"}
    # Other blocks untouched.
    assert final["persona"]["name"] == "X"
    assert final["stt"]["backend"] == "parakeet"
    # A follow-up patch shallow-merges onto the existing wakeword block.
    merge_patch({"wakeword": {"enabled": False}}, p)
    final2 = yaml.safe_load(p.read_text())
    assert final2["wakeword"] == {"enabled": False, "model": "hey_orbis"}


# --- setup (durable first-run completion flag) ------------------------------


def test_validate_accepts_setup_complete():
    out = validate_and_normalize({"setup": {"complete": True}})
    assert out["setup"] == {"complete": True}


def test_validate_setup_coerces_to_bool():
    out = validate_and_normalize({"setup": {"complete": 1}})
    assert out["setup"] == {"complete": True}


def test_validate_drops_unknown_setup_key(caplog: pytest.LogCaptureFixture):
    out = validate_and_normalize({"setup": {"complete": True, "bogus": 1}})
    assert out["setup"] == {"complete": True}
    assert "bogus" in caplog.text


def test_merge_patch_setup_preserves_other_blocks(tmp_path: Path):
    p = _write(tmp_path / "orbis.yaml", """
        persona:
          name: X
        voice:
          local_models: on_device
    """)
    merge_patch({"setup": {"complete": True}}, p)
    final = yaml.safe_load(p.read_text())
    assert final["setup"] == {"complete": True}
    # The wizard-written blocks survive — the whole point of the durable flag.
    assert final["persona"]["name"] == "X"
    assert final["voice"]["local_models"] == "on_device"
    # Re-run flips it back without disturbing the rest.
    merge_patch({"setup": {"complete": False}}, p)
    final2 = yaml.safe_load(p.read_text())
    assert final2["setup"] == {"complete": False}
    assert final2["voice"]["local_models"] == "on_device"
