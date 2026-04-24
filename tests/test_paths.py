"""Tests for agent.paths — per-OS default resolution + env overrides."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def clear_env(monkeypatch: pytest.MonkeyPatch):
    """Strip any env vars the resolver reads so each test starts clean."""
    for k in (
        "ORBIS_DB_PATH", "ORBIS_CACHE_DIR", "HF_HOME", "TRANSFORMERS_CACHE",
        "MODEL_DIR", "XDG_DATA_HOME", "XDG_CACHE_HOME", "APPDATA", "LOCALAPPDATA",
    ):
        monkeypatch.delenv(k, raising=False)


def _reload():
    from agent import paths
    importlib.reload(paths)
    return paths


# --- platform detection ------------------------------------------------------


def test_platform_detects_darwin(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    p = _reload()
    assert p._platform() == "darwin"


def test_platform_detects_windows(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sys, "platform", "win32")
    p = _reload()
    assert p._platform() == "windows"


def test_platform_detects_linux(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sys, "platform", "linux")
    p = _reload()
    assert p._platform() == "linux"


# --- DB path -----------------------------------------------------------------


def test_db_path_env_override_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    explicit = tmp_path / "custom" / "my.sqlite"
    monkeypatch.setenv("ORBIS_DB_PATH", str(explicit))
    p = _reload()
    got = p.get_db_path()
    assert got == explicit
    assert got.parent.is_dir()  # mkdir on demand


def test_db_path_darwin_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("HOME", str(tmp_path))
    p = _reload()
    got = p.get_db_path()
    assert got == tmp_path / "Library" / "Application Support" / "orbis" / "orbis.sqlite"


def test_db_path_windows_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    p = _reload()
    got = p.get_db_path()
    assert got == tmp_path / "Roaming" / "orbis" / "orbis.sqlite"


def test_db_path_linux_xdg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    p = _reload()
    got = p.get_db_path()
    assert got == tmp_path / "data" / "orbis" / "orbis.sqlite"


def test_db_path_linux_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    """No XDG_DATA_HOME → falls back to ~/.local/share."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("HOME", str(tmp_path))
    p = _reload()
    got = p.get_db_path()
    assert got == tmp_path / ".local" / "share" / "orbis" / "orbis.sqlite"


# --- cache dir ---------------------------------------------------------------


def test_cache_dir_env_override_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    explicit = tmp_path / "orbis-cache"
    monkeypatch.setenv("ORBIS_CACHE_DIR", str(explicit))
    p = _reload()
    got = p.get_cache_dir()
    assert got == explicit
    assert got.is_dir()


def test_cache_dir_hf_home_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    """Docker compat: HF_HOME pre-set → use it rather than the per-OS default."""
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    p = _reload()
    got = p.get_cache_dir()
    assert got == tmp_path / "hf"


def test_cache_dir_model_dir_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    """Legacy MODEL_DIR alias still honored — some older docs used it."""
    monkeypatch.setenv("MODEL_DIR", str(tmp_path / "models"))
    p = _reload()
    got = p.get_cache_dir()
    assert got == tmp_path / "models"


def test_cache_dir_darwin_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("HOME", str(tmp_path))
    p = _reload()
    got = p.get_cache_dir()
    assert got == tmp_path / "Library" / "Caches" / "orbis"


# --- configure_hf_home -------------------------------------------------------


def test_configure_hf_home_sets_envs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    monkeypatch.setenv("ORBIS_CACHE_DIR", str(tmp_path))
    p = _reload()
    resolved = p.configure_hf_home()
    assert resolved == tmp_path
    import os
    assert os.environ["HF_HOME"] == str(tmp_path)
    assert os.environ["TRANSFORMERS_CACHE"] == str(tmp_path)


def test_configure_hf_home_does_not_clobber_existing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    """``setdefault`` semantics — operator-provided HF_HOME wins."""
    monkeypatch.setenv("HF_HOME", "/mnt/operator-override")
    monkeypatch.setenv("ORBIS_CACHE_DIR", str(tmp_path))
    p = _reload()
    p.configure_hf_home()
    import os
    assert os.environ["HF_HOME"] == "/mnt/operator-override"
