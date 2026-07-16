"""Tests for _build_speaker_gate (PR 1.2 — pipeline wiring).

Covers config-block resolution: enabled/disabled, voiceprint path
resolution (config → env → default), threshold / stranger_action,
the corrupt-voiceprint path (logs error, runs in owner-trust this
session), and the speechbrain-missing path (lazy import failure
falls back to owner-trust without crashing the session).

Doesn't exercise the live pipeline — that's an integration concern.
This file tests the helper in isolation.
"""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest


@pytest.fixture
def helper(monkeypatch):
    """Reload app with a clean env so module-level constants don't
    leak across tests. Returns (_build_speaker_gate, app_module)."""
    monkeypatch.delenv("SPEAKER_GATE_VOICEPRINT_PATH", raising=False)
    import app as _app
    return _app._build_speaker_gate, _app


# --- enabled / disabled ---------------------------------------------------


def test_disabled_block_returns_disabled_gate(helper) -> None:
    build, _ = helper
    gate = build({"enabled": False})
    assert gate._enabled is False


def test_enabled_block_with_no_voiceprint_runs_in_owner_trust(helper, tmp_path) -> None:
    """Default state: enabled, no voiceprint file. Gate is enabled (so
    pipeline order matters) but operates in owner-trust mode because
    voiceprint=None. Preserves the no-auth single-user deployment story."""
    build, _ = helper
    gate = build({
        "enabled": True,
        "voiceprint_path": str(tmp_path / "nope.npy"),
    })
    assert gate._enabled is True
    assert gate._voiceprint is None
    assert gate._embedder is None


# --- threshold + stranger_action ----------------------------------------


def test_threshold_default_is_062(helper, tmp_path) -> None:
    build, _ = helper
    gate = build({"voiceprint_path": str(tmp_path / "nope.npy")})
    assert gate._threshold == pytest.approx(0.62)


def test_threshold_override(helper, tmp_path) -> None:
    build, _ = helper
    gate = build({
        "voiceprint_path": str(tmp_path / "nope.npy"),
        "threshold": 0.55,
    })
    assert gate._threshold == pytest.approx(0.55)


def test_stranger_action_warn_is_default(helper, tmp_path) -> None:
    build, app = helper
    gate = build({"voiceprint_path": str(tmp_path / "nope.npy")})
    assert gate._action is app.StrangerAction.WARN


def test_stranger_action_refuse_resolves(helper, tmp_path) -> None:
    build, app = helper
    gate = build({
        "voiceprint_path": str(tmp_path / "nope.npy"),
        "stranger_action": "refuse",
    })
    assert gate._action is app.StrangerAction.REFUSE


def test_stranger_action_delegate_guest_resolves(helper, tmp_path) -> None:
    build, app = helper
    gate = build({
        "voiceprint_path": str(tmp_path / "nope.npy"),
        "stranger_action": "delegate_guest",
    })
    assert gate._action is app.StrangerAction.DELEGATE_GUEST


def test_unknown_stranger_action_falls_back_to_warn(helper, tmp_path, caplog) -> None:
    build, app = helper
    import logging
    with caplog.at_level(logging.WARNING):
        gate = build({
            "voiceprint_path": str(tmp_path / "nope.npy"),
            "stranger_action": "ignite_self_destruct",
        })
    assert gate._action is app.StrangerAction.WARN
    # Side-effect must be observed too — a silent fallback would mask
    # config typos from the operator.
    assert any(
        "unknown stranger_action" in rec.message for rec in caplog.records
    )


# --- threshold parsing --------------------------------------------------


def test_threshold_null_uses_default_quietly(helper, tmp_path, caplog) -> None:
    """null in YAML / explicit None in config is a legitimate 'use the
    default' signal — no warning expected, just silent fallback to
    0.62. CR Major: pre-fix this raised TypeError on float(None) and
    aborted run_bot."""
    build, _ = helper
    import logging
    with caplog.at_level(logging.WARNING):
        gate = build({
            "voiceprint_path": str(tmp_path / "nope.npy"),
            "threshold": None,
        })
    assert gate._threshold == pytest.approx(0.62)
    # No warning — None is intentional, not a typo.
    assert not any("invalid threshold" in rec.message for rec in caplog.records)


def test_threshold_string_typo_falls_back(helper, tmp_path, caplog) -> None:
    """Non-numeric typo (e.g. operator wrote 'high' instead of 0.7) →
    warn + default, don't take down the session."""
    build, _ = helper
    import logging
    with caplog.at_level(logging.WARNING):
        gate = build({
            "voiceprint_path": str(tmp_path / "nope.npy"),
            "threshold": "high",
        })
    assert gate._threshold == pytest.approx(0.62)
    assert any("invalid threshold" in rec.message for rec in caplog.records)


def test_threshold_string_numeric_coerces(helper, tmp_path) -> None:
    """A string that float() can parse (YAML sometimes serializes 0.5
    as the string '0.5') still resolves cleanly."""
    build, _ = helper
    gate = build({
        "voiceprint_path": str(tmp_path / "nope.npy"),
        "threshold": "0.55",
    })
    assert gate._threshold == pytest.approx(0.55)


# --- voiceprint resolution ----------------------------------------------


def test_voiceprint_path_from_config(helper, tmp_path) -> None:
    build, _ = helper
    # Save a real voiceprint so we exercise the load+embedder branch.
    vp = np.zeros(192, dtype=np.float32)
    p = tmp_path / "vp.npy"
    np.save(p, vp)

    # Force ECAPAEmbedder to fail (test environment doesn't have
    # speechbrain by default; this ensures the test is deterministic).
    with patch("agent.ecapa_embedder.ECAPAEmbedder", side_effect=ImportError("no speechbrain")):
        gate = build({"voiceprint_path": str(p)})

    # Voiceprint loaded, but embedder import failed → owner-trust this
    # session.
    assert gate._voiceprint is not None
    assert gate._embedder is None


def test_voiceprint_path_from_env(helper, tmp_path, monkeypatch) -> None:
    """Env var takes effect when config has no voiceprint_path."""
    vp = np.zeros(192, dtype=np.float32)
    p = tmp_path / "env_vp.npy"
    np.save(p, vp)

    monkeypatch.setenv("SPEAKER_GATE_VOICEPRINT_PATH", str(p))

    with patch("agent.ecapa_embedder.ECAPAEmbedder", side_effect=ImportError):
        gate = build_with_reload(monkeypatch)({})

    assert gate._voiceprint is not None


def build_with_reload(monkeypatch):
    """Re-import app after env mutation so module-level constants pick
    up the new env (this is needed for env-var-driven defaults)."""
    import importlib
    import app
    importlib.reload(app)
    return app._build_speaker_gate


def test_voiceprint_corrupt_logs_and_owner_trusts(helper, tmp_path, caplog) -> None:
    """The R-flagged behavior: corrupt voiceprint must NOT silently
    drop into owner-trust. It logs an actionable error AND runs in
    owner-trust this session so the user can still talk."""
    build, _ = helper
    bad = tmp_path / "bad.npy"
    bad.write_bytes(b"not a real npy file")

    import logging
    with caplog.at_level(logging.ERROR):
        gate = build({"voiceprint_path": str(bad)})

    assert gate._voiceprint is None
    # Error logged so the operator notices.
    assert any("corrupt" in rec.message.lower() for rec in caplog.records)


def test_voiceprint_present_but_speechbrain_missing(helper, tmp_path, caplog) -> None:
    """Voiceprint exists but [speaker-id] extra not installed: log a
    warning telling the operator how to install, run owner-trust this
    session."""
    build, _ = helper
    vp = np.zeros(192, dtype=np.float32)
    p = tmp_path / "vp.npy"
    np.save(p, vp)

    import logging
    with caplog.at_level(logging.WARNING):
        with patch("agent.ecapa_embedder.ECAPAEmbedder", side_effect=ImportError("no speechbrain")):
            gate = build({"voiceprint_path": str(p)})

    assert gate._voiceprint is not None
    assert gate._embedder is None
    # Actionable hint pointing at the extra.
    assert any("speaker-id" in rec.message for rec in caplog.records)


# --- pipeline placement (smoke) -----------------------------------------


def test_speaker_gate_is_in_run_bot_pipeline_construction() -> None:
    """Smoke check: the gate is referenced in run_bot's pipeline list.
    Cheaper than wiring the full pipeline, catches accidental removal."""
    import app
    src = open(app.run_bot.__code__.co_filename).read()
    # The gate construction line + its placement in the pipeline list
    # both need to appear in run_bot.
    assert "_build_speaker_gate(sg_cfg)" in src
    # Speaker_gate should appear between EchoGuardSuppressor and rtvi
    # in the pipeline construction.
    eg_idx = src.index("EchoGuardSuppressor(_ECHO_STATE)")
    sg_idx = src.index("speaker_gate,", eg_idx)
    rtvi_idx = src.index("rtvi,", sg_idx)
    assert eg_idx < sg_idx < rtvi_idx, \
        "speaker_gate must sit between EchoGuardSuppressor and rtvi"
