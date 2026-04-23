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
