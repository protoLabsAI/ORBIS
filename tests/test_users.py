"""Unit tests for the single-owner auth primitive.

ORBIS has one owner per install. These tests cover the owner roster
parsing and key resolution; the old multi-tenant / roles /
allowed_skills tests are gone with that machinery.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from auth.users import (
    DEFAULT_USER,
    User,
    UserRegistry,
)


def _write_yaml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "users.yaml"
    p.write_text(textwrap.dedent(body).lstrip())
    return p


# --- Fallback / empty-registry behavior --------------------------------------

def test_empty_registry_is_single_user_mode(tmp_path: Path):
    reg = UserRegistry(tmp_path / "missing.yaml", auto_load=True)
    assert reg.single_user_mode()
    assert reg.source == "empty"
    assert reg.owner() is None


def test_default_user_is_synthetic_owner():
    assert DEFAULT_USER.id == "default"
    assert DEFAULT_USER.api_key_hash == ""


# --- Owner loading -----------------------------------------------------------

def test_registry_loads_single_owner(tmp_path: Path):
    yaml_path = _write_yaml(tmp_path, """
        users:
          - id: alice
            api_key: pv_ak_alice_secret
            display_name: Alice
    """)
    reg = UserRegistry(yaml_path, auto_load=True)
    assert not reg.single_user_mode()
    assert reg.source == "file"
    owner = reg.owner()
    assert owner is not None
    assert owner.id == "alice"
    assert owner.display_name == "Alice"


def test_owner_display_name_defaults_to_id(tmp_path: Path):
    yaml_path = _write_yaml(tmp_path, """
        users:
          - id: bob
            api_key: pv_ak_bob
    """)
    owner = UserRegistry(yaml_path, auto_load=True).owner()
    assert owner is not None
    assert owner.display_name == "bob"


# --- Key resolution ----------------------------------------------------------

def test_correct_key_resolves_to_owner(tmp_path: Path):
    yaml_path = _write_yaml(tmp_path, """
        users:
          - id: alice
            api_key: pv_ak_alice_secret
    """)
    reg = UserRegistry(yaml_path, auto_load=True)
    user = reg.resolve("pv_ak_alice_secret")
    assert user is not None
    assert user.id == "alice"


def test_wrong_key_resolves_to_none(tmp_path: Path):
    yaml_path = _write_yaml(tmp_path, """
        users:
          - id: alice
            api_key: pv_ak_alice
    """)
    reg = UserRegistry(yaml_path, auto_load=True)
    assert reg.resolve("wrong") is None
    assert reg.resolve(None) is None
    assert reg.resolve("") is None


def test_resolve_in_fallback_mode_returns_none(tmp_path: Path):
    reg = UserRegistry(tmp_path / "missing.yaml", auto_load=True)
    # In fallback, resolve always returns None — the app layer uses
    # single_user_mode() to skip auth entirely, not resolve().
    assert reg.resolve("any-key") is None


# --- Multiple-entry guardrail ------------------------------------------------

def test_multiple_entries_logs_warning_and_keeps_first(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
):
    yaml_path = _write_yaml(tmp_path, """
        users:
          - id: alice
            api_key: pv_ak_alice
          - id: bob
            api_key: pv_ak_bob
    """)
    with caplog.at_level("WARNING"):
        reg = UserRegistry(yaml_path, auto_load=True)
    owner = reg.owner()
    assert owner is not None
    assert owner.id == "alice"  # first entry wins
    assert any("single-owner" in r.message for r in caplog.records)


# --- Malformed input ---------------------------------------------------------

def test_missing_id_or_key_logs_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
):
    yaml_path = _write_yaml(tmp_path, """
        users:
          - id: ""
            api_key: pv_ak_oops
    """)
    with caplog.at_level("WARNING"):
        reg = UserRegistry(yaml_path, auto_load=True)
    assert reg.owner() is None
    assert any("malformed" in r.message for r in caplog.records)


# --- Reload ------------------------------------------------------------------

def test_reload_reflects_roster_changes(tmp_path: Path):
    yaml_path = _write_yaml(tmp_path, """
        users:
          - id: alice
            api_key: pv_ak_alice
    """)
    reg = UserRegistry(yaml_path, auto_load=True)
    assert reg.owner().id == "alice"

    yaml_path.write_text(textwrap.dedent("""
        users:
          - id: bob
            api_key: pv_ak_bob
    """).lstrip())
    ids = reg.reload()
    assert ids == ["bob"]
    assert reg.owner().id == "bob"


# --- User.hash_key sanity ----------------------------------------------------

def test_hash_key_is_deterministic():
    a = User.hash_key("pv_ak_test")
    b = User.hash_key("pv_ak_test")
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_hash_key_differs_per_key():
    assert User.hash_key("pv_ak_a") != User.hash_key("pv_ak_b")
