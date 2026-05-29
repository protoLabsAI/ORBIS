"""Infisical owner-roster source."""

from __future__ import annotations

from types import SimpleNamespace

import httpx

from auth import infisical


def test_fetch_users_yaml_defaults_to_orbis_secret_path(monkeypatch):
    monkeypatch.setenv("INFISICAL_CLIENT_ID", "client")
    monkeypatch.setenv("INFISICAL_CLIENT_SECRET", "secret")
    monkeypatch.setenv("INFISICAL_PROJECT_ID", "project")
    monkeypatch.delenv("INFISICAL_SECRET_PATH", raising=False)

    captured: dict[str, object] = {}

    def fake_post(*_args, **_kwargs):
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"accessToken": "token"},
        )

    def fake_get(_url, *, params, **_kwargs):
        captured["params"] = params
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "secret": {
                    "secretValue": "users:\n  - id: alice\n    api_key: pv_ak_test\n",
                },
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", fake_get)

    value = infisical.fetch_users_yaml()

    assert value and "pv_ak_test" in value
    assert captured["params"]["secretPath"] == "/orbis"
