"""FastAPI TestClient coverage for /api/whoami, /api/skills,
/api/admin/skills. Uses app.dependency_overrides to inject specific
users into the require_user / require_admin dependencies without
needing real API keys in the registry.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

import app as app_module
from auth.users import ROLE_ADMIN, ROLE_USER, User
from auth import require_admin, require_user


def _make_user(
    user_id: str,
    *,
    role: str = ROLE_USER,
    allowed_skills: tuple[str, ...] | None = None,
    pinned_viz: dict | None = None,
) -> User:
    return User(
        id=user_id,
        display_name=user_id.title(),
        api_key_hash="h:" + user_id,
        role=role,
        allowed_skills=allowed_skills,
        pinned_viz=pinned_viz,
    )


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Fresh TestClient that cleans up dependency_overrides after each test."""
    c = TestClient(app_module.app)
    try:
        yield c
    finally:
        app_module.app.dependency_overrides.clear()


def _as_user(user: User) -> None:
    """Override require_user + require_admin so both resolve to the same
    caller. require_admin still 403s for non-admins because the original
    dependency runs a role check after resolution — we only shortcut the
    header + registry lookup."""
    def override_require_user() -> User:
        return user
    def override_require_admin() -> User:
        if not user.is_admin:
            from fastapi import HTTPException
            raise HTTPException(status_code=403, detail="admin role required")
        return user
    app_module.app.dependency_overrides[require_user] = override_require_user
    app_module.app.dependency_overrides[require_admin] = override_require_admin


# --- /api/whoami --------------------------------------------------------------

def test_whoami_admin(client: TestClient):
    _as_user(_make_user("bob", role=ROLE_ADMIN))
    r = client.get("/api/whoami")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "bob"
    assert body["role"] == "admin"
    assert body["allowed_skills"] is None
    assert body["pinned_viz"] is None


def test_whoami_constrained_user(client: TestClient):
    _as_user(_make_user(
        "alice",
        allowed_skills=("josh", "chef"),
        pinned_viz={"palette": "Noir"},
    ))
    r = client.get("/api/whoami")
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "user"
    assert body["allowed_skills"] == ["josh", "chef"]
    assert body["pinned_viz"] == {"palette": "Noir"}


# --- GET /api/skills ----------------------------------------------------------

def test_get_skills_admin_sees_everything_unlocked(client: TestClient):
    _as_user(_make_user("bob", role=ROLE_ADMIN))
    r = client.get("/api/skills")
    assert r.status_code == 200
    body = r.json()
    assert body["locked"] is False
    # Admin sees the full catalog — at minimum 'default' always exists.
    slugs = [s["slug"] for s in body["skills"]]
    assert "default" in slugs
    assert len(slugs) == len(app_module._SKILLS)


def test_get_skills_user_with_multiple_allowed_filters_list_not_locked(
    client: TestClient,
):
    _as_user(_make_user("alice", allowed_skills=("josh", "chef")))
    r = client.get("/api/skills")
    assert r.status_code == 200
    body = r.json()
    assert body["locked"] is False
    slugs = {s["slug"] for s in body["skills"]}
    assert slugs == {"josh", "chef"}


def test_get_skills_user_with_single_allowed_is_locked(client: TestClient):
    _as_user(_make_user("alice", allowed_skills=("josh",)))
    r = client.get("/api/skills")
    body = r.json()
    assert body["locked"] is True
    assert [s["slug"] for s in body["skills"]] == ["josh"]
    # Active always resolves inside the allowed set.
    assert body["active"] == "josh"


def test_get_skills_user_unconstrained_sees_everything_unlocked(
    client: TestClient,
):
    _as_user(_make_user("alice"))  # allowed_skills=None
    r = client.get("/api/skills")
    body = r.json()
    assert body["locked"] is False
    assert len([s["slug"] for s in body["skills"]]) == len(app_module._SKILLS)


def test_get_skills_active_snaps_into_allowed_when_mutable_drifts(
    client: TestClient,
):
    """If a user's mutable skill_slug predates a roster change that
    restricts their allowed_skills, GET /api/skills should snap active
    to the first allowed slug rather than returning something the user
    can't activate."""
    # Prime the mutable state outside their allowed list.
    app_module.user_state_for("alice").skill_slug = "default"
    _as_user(_make_user("alice", allowed_skills=("josh", "chef")))
    r = client.get("/api/skills")
    body = r.json()
    assert body["active"] == "josh"  # first allowed


# --- POST /api/skills ---------------------------------------------------------

def test_post_skills_admin_can_pick_anything(client: TestClient):
    _as_user(_make_user("bob", role=ROLE_ADMIN))
    r = client.post("/api/skills", json={"slug": "chef"})
    assert r.status_code == 200
    assert r.json() == {"active": "chef"}


def test_post_skills_user_can_pick_within_allowed(client: TestClient):
    _as_user(_make_user("alice", allowed_skills=("josh", "chef")))
    r = client.post("/api/skills", json={"slug": "chef"})
    assert r.status_code == 200
    assert r.json() == {"active": "chef"}


def test_post_skills_user_rejected_for_disallowed_slug(client: TestClient):
    _as_user(_make_user("alice", allowed_skills=("josh",)))
    r = client.post("/api/skills", json={"slug": "chef"})
    assert r.status_code == 403
    assert "not in allowed_skills" in r.json()["detail"]


def test_post_skills_rejects_unknown_slug(client: TestClient):
    _as_user(_make_user("bob", role=ROLE_ADMIN))
    r = client.post("/api/skills", json={"slug": "does-not-exist"})
    # 200 with error body — matches the existing unknown-slug convention.
    assert r.status_code == 200
    body = r.json()
    assert "unknown skill" in body["error"]


def test_post_skills_unconstrained_user_can_pick_anything(client: TestClient):
    _as_user(_make_user("alice"))  # allowed_skills=None
    r = client.post("/api/skills", json={"slug": "chef"})
    assert r.status_code == 200


# --- POST /api/admin/skills ---------------------------------------------------

def test_post_admin_skills_requires_admin(client: TestClient):
    _as_user(_make_user("alice", allowed_skills=("josh",)))
    r = client.post(
        "/api/admin/skills",
        json={"user_id": "default", "slug": "chef"},
    )
    assert r.status_code == 403


def test_post_admin_skills_admin_sets_any_user_skill(client: TestClient):
    _as_user(_make_user("bob", role=ROLE_ADMIN))
    # Target user must exist in the registry. Single-user mode accepts
    # "default", so use that for this test.
    r = client.post(
        "/api/admin/skills",
        json={"user_id": "default", "slug": "chef"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["active"] == "chef"
    assert app_module.user_state_for("default").skill_slug == "chef"


def test_post_admin_skills_rejects_unknown_skill(client: TestClient):
    _as_user(_make_user("bob", role=ROLE_ADMIN))
    r = client.post(
        "/api/admin/skills",
        json={"user_id": "default", "slug": "nonexistent"},
    )
    assert r.status_code == 200
    assert "unknown skill" in r.json()["error"]


def test_post_admin_skills_missing_user_id(client: TestClient):
    _as_user(_make_user("bob", role=ROLE_ADMIN))
    r = client.post("/api/admin/skills", json={"slug": "chef"})
    assert r.status_code == 200
    assert "user_id is required" in r.json()["error"]
