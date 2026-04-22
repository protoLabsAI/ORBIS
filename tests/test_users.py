"""Unit tests for auth.users — roster parsing, roles, allowed_skills.

These tests exercise UserRegistry directly, with YAML content written to
a tempfile. No network, no Infisical, no FastAPI.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from auth.users import (
    DEFAULT_USER,
    ROLE_ADMIN,
    ROLE_USER,
    User,
    UserRegistry,
)


def _write_yaml(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "users.yaml"
    p.write_text(textwrap.dedent(body).lstrip())
    return p


# --- User.allows_skill --------------------------------------------------------

def _user(role: str = ROLE_USER, allowed: tuple[str, ...] | None = None) -> User:
    return User(
        id="u",
        display_name="U",
        api_key_hash="h",
        role=role,
        allowed_skills=allowed,
    )


def test_admin_allows_any_skill():
    u = _user(role=ROLE_ADMIN, allowed=("only-this",))
    assert u.allows_skill("anything")
    assert u.allows_skill("only-this")


def test_user_with_no_allowed_list_allows_any_skill():
    u = _user(role=ROLE_USER, allowed=None)
    assert u.allows_skill("chef")
    assert u.allows_skill("josh")


def test_user_with_allowed_list_restricts():
    u = _user(allowed=("chef", "josh"))
    assert u.allows_skill("chef")
    assert u.allows_skill("josh")
    assert not u.allows_skill("default")


def test_user_with_single_allowed_is_effectively_locked():
    u = _user(allowed=("josh",))
    assert u.allows_skill("josh")
    assert not u.allows_skill("chef")


# --- UserRegistry YAML parsing ------------------------------------------------

def test_empty_registry_is_single_user_mode(tmp_path: Path):
    reg = UserRegistry(tmp_path / "missing.yaml", auto_load=True)
    assert reg.single_user_mode()
    assert reg.source == "empty"
    assert reg.by_id("default") is DEFAULT_USER
    assert DEFAULT_USER.is_admin  # fallback runs as admin


def test_registry_loads_users_with_roles_and_allowed_skills(tmp_path: Path):
    yaml_path = _write_yaml(tmp_path, """
        users:
          - id: alice
            api_key: pv_ak_alice
            display_name: Alice
            allowed_skills: [josh, chef]
          - id: bob
            api_key: pv_ak_bob
            display_name: Bob
            role: admin
    """)
    reg = UserRegistry(yaml_path, auto_load=True)
    assert not reg.single_user_mode()
    assert reg.source == "file"

    alice = reg.resolve("pv_ak_alice")
    assert alice is not None
    assert alice.id == "alice"
    assert alice.role == ROLE_USER
    assert alice.allowed_skills == ("josh", "chef")
    assert alice.allows_skill("josh")
    assert alice.allows_skill("chef")
    assert not alice.allows_skill("default")

    bob = reg.resolve("pv_ak_bob")
    assert bob is not None
    assert bob.is_admin
    assert bob.allowed_skills is None  # admin, no constraint
    assert bob.allows_skill("anything")


def test_registry_rejects_unknown_key(tmp_path: Path):
    yaml_path = _write_yaml(tmp_path, """
        users:
          - id: alice
            api_key: pv_ak_alice
    """)
    reg = UserRegistry(yaml_path, auto_load=True)
    assert reg.resolve("wrong") is None
    assert reg.resolve(None) is None
    assert reg.resolve("") is None


def test_allowed_skills_missing_means_unconstrained(tmp_path: Path):
    yaml_path = _write_yaml(tmp_path, """
        users:
          - id: alice
            api_key: pv_ak_alice
    """)
    alice = UserRegistry(yaml_path, auto_load=True).resolve("pv_ak_alice")
    assert alice is not None
    assert alice.allowed_skills is None
    assert alice.allows_skill("any-slug")


def test_empty_allowed_skills_list_treated_as_unconstrained(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
):
    # Empty list is almost always a mistake — we log + treat as None
    # so the user isn't soft-locked out of every skill.
    yaml_path = _write_yaml(tmp_path, """
        users:
          - id: alice
            api_key: pv_ak_alice
            allowed_skills: []
    """)
    with caplog.at_level("WARNING"):
        alice = UserRegistry(yaml_path, auto_load=True).resolve("pv_ak_alice")
    assert alice is not None
    assert alice.allowed_skills is None
    assert any("allowed_skills is empty" in r.message for r in caplog.records)


def test_non_list_allowed_skills_is_ignored(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
):
    yaml_path = _write_yaml(tmp_path, """
        users:
          - id: alice
            api_key: pv_ak_alice
            allowed_skills: josh
    """)
    with caplog.at_level("WARNING"):
        alice = UserRegistry(yaml_path, auto_load=True).resolve("pv_ak_alice")
    assert alice is not None
    assert alice.allowed_skills is None
    assert any("must be a list" in r.message for r in caplog.records)


def test_allowed_skills_strips_and_drops_empty_entries(tmp_path: Path):
    yaml_path = _write_yaml(tmp_path, """
        users:
          - id: alice
            api_key: pv_ak_alice
            allowed_skills: ["  josh  ", "", "chef"]
    """)
    alice = UserRegistry(yaml_path, auto_load=True).resolve("pv_ak_alice")
    assert alice is not None
    assert alice.allowed_skills == ("josh", "chef")


def test_unknown_role_defaults_to_user(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
):
    yaml_path = _write_yaml(tmp_path, """
        users:
          - id: alice
            api_key: pv_ak_alice
            role: wizard
    """)
    with caplog.at_level("WARNING"):
        alice = UserRegistry(yaml_path, auto_load=True).resolve("pv_ak_alice")
    assert alice is not None
    assert alice.role == ROLE_USER
    assert any("unknown role" in r.message for r in caplog.records)


def test_pinned_viz_must_be_mapping(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
):
    yaml_path = _write_yaml(tmp_path, """
        users:
          - id: alice
            api_key: pv_ak_alice
            pinned_viz: "not-a-dict"
    """)
    with caplog.at_level("WARNING"):
        alice = UserRegistry(yaml_path, auto_load=True).resolve("pv_ak_alice")
    assert alice is not None
    assert alice.pinned_viz is None
    assert any("pinned_viz must be a mapping" in r.message for r in caplog.records)


def test_pinned_viz_accepted_as_mapping(tmp_path: Path):
    yaml_path = _write_yaml(tmp_path, """
        users:
          - id: alice
            api_key: pv_ak_alice
            pinned_viz:
              palette: Noir
    """)
    alice = UserRegistry(yaml_path, auto_load=True).resolve("pv_ak_alice")
    assert alice is not None
    assert alice.pinned_viz == {"palette": "Noir"}


def test_by_id_lookup(tmp_path: Path):
    yaml_path = _write_yaml(tmp_path, """
        users:
          - id: alice
            api_key: pv_ak_alice
          - id: bob
            api_key: pv_ak_bob
            role: admin
    """)
    reg = UserRegistry(yaml_path, auto_load=True)
    assert reg.by_id("alice") is not None
    assert reg.by_id("bob") is not None
    assert reg.by_id("charlie") is None


def test_by_id_falls_back_to_default_in_single_user_mode(tmp_path: Path):
    reg = UserRegistry(tmp_path / "missing.yaml", auto_load=True)
    # Single-user mode: 'default' resolves to DEFAULT_USER.
    assert reg.by_id("default") is DEFAULT_USER
    # Unknown id still returns None (DEFAULT_USER is a sentinel, not a
    # catch-all).
    assert reg.by_id("alice") is None


def test_reload_reflects_roster_changes(tmp_path: Path):
    yaml_path = _write_yaml(tmp_path, """
        users:
          - id: alice
            api_key: pv_ak_alice
    """)
    reg = UserRegistry(yaml_path, auto_load=True)
    assert [u.id for u in reg.all()] == ["alice"]

    yaml_path.write_text(textwrap.dedent("""
        users:
          - id: alice
            api_key: pv_ak_alice
            allowed_skills: [josh]
          - id: bob
            api_key: pv_ak_bob
            role: admin
    """).lstrip())
    assert sorted(reg.reload()) == ["alice", "bob"]
    alice = reg.resolve("pv_ak_alice")
    assert alice is not None
    assert alice.allowed_skills == ("josh",)
