"""Tests for /api/delegates CRUD + /api/delegates/test reachability probe.

Spans the full lifecycle a UI client would exercise:
  GET → list (empty + populated)
  POST → 201 happy path, 409 duplicate, 400 schema rejection
  PUT → update, 404 missing, 400 path/body name mismatch
  DELETE → 204 happy, 404 missing
  POST /test → a2a happy, a2a auth-reject, openai happy, schema reject

Atomic-write semantics covered separately at the config-store layer
(tests/test_delegate_config_store.py — sister suite).
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

import agent.delegate_config_store as store
import app as app_module
from app import app


@pytest.fixture
def isolated_delegates_yaml(tmp_path, monkeypatch):
    """Point the config store + the runtime registry at a per-test
    delegates.yaml so we don't stomp the dev config."""
    p = tmp_path / "delegates.yaml"
    monkeypatch.setattr(store, "DEFAULT_PATH", str(p))
    # The API endpoints reload the runtime registry against the path
    # configured at app boot — swap it for the test's path so reload
    # actually reads our fixture.
    from agent.delegates import DelegateRegistry
    test_registry = DelegateRegistry(p)
    monkeypatch.setattr(app_module, "_DELEGATES", test_registry)
    return p


@pytest.fixture
def client(isolated_delegates_yaml):
    return TestClient(app)


def _task_not_found(request):
    body = json.loads(request.content)
    assert body["method"] == "tasks/get"
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32001, "message": "Task not found"}})


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------


def test_get_empty_returns_empty_list(client, isolated_delegates_yaml):
    r = client.get("/api/delegates")
    assert r.status_code == 200
    assert r.json() == {"delegates": []}


def test_get_returns_existing_entries_with_configured_flag(
    client, isolated_delegates_yaml,
):
    isolated_delegates_yaml.write_text(
        "delegates:\n"
        "  - name: ava\n"
        "    type: a2a\n"
        "    description: Chief of staff.\n"
        "    url: http://ava:3008/a2a\n"
    )
    # Reload the runtime registry so `configured` reflects the new file.
    app_module._DELEGATES.reload()
    r = client.get("/api/delegates")
    assert r.status_code == 200
    body = r.json()
    assert len(body["delegates"]) == 1
    entry = body["delegates"][0]
    assert entry["name"] == "ava"
    assert entry["type"] == "a2a"
    assert entry["configured"] is True


def test_get_flags_unconfigured_when_runtime_parse_fails(
    client, isolated_delegates_yaml,
):
    """Entry on disk that the runtime registry rejects (e.g. missing
    model on an openai delegate) shows up as configured=False so the
    UI can flag it inline."""
    isolated_delegates_yaml.write_text(
        "delegates:\n"
        "  - name: broken\n"
        "    type: openai\n"
        "    description: needs a model.\n"
        "    url: https://example.com/v1\n"
        # model: ← missing on purpose
    )
    app_module._DELEGATES.reload()
    body = client.get("/api/delegates").json()
    assert body["delegates"][0]["configured"] is False


# ---------------------------------------------------------------------------
# POST (create)
# ---------------------------------------------------------------------------


def test_post_creates_and_persists(client, isolated_delegates_yaml):
    payload = {
        "name": "ava",
        "type": "a2a",
        "description": "Chief of staff.",
        "url": "http://ava:3008/a2a",
    }
    r = client.post("/api/delegates", json=payload)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    # Persisted to disk
    assert "name: ava" in isolated_delegates_yaml.read_text()
    # Runtime registry reloaded — name now visible
    assert "ava" in app_module._DELEGATES.names()


def test_post_duplicate_name_returns_409(client, isolated_delegates_yaml):
    isolated_delegates_yaml.write_text(
        "delegates:\n"
        "  - name: ava\n"
        "    type: a2a\n"
        "    description: hi.\n"
        "    url: http://ava/a2a\n"
    )
    payload = {
        "name": "ava",
        "type": "a2a",
        "description": "second one.",
        "url": "http://ava2/a2a",
    }
    r = client.post("/api/delegates", json=payload)
    assert r.status_code == 409
    assert "already exists" in r.json()["error"]


def test_post_schema_rejection_returns_400(client, isolated_delegates_yaml):
    payload = {
        "name": "incomplete",
        "type": "openai",
        "description": "missing model.",
        "url": "https://example.com/v1",
        # model: ← missing
    }
    r = client.post("/api/delegates", json=payload)
    assert r.status_code == 400
    assert "model" in r.json()["error"]


# ---------------------------------------------------------------------------
# PUT (update)
# ---------------------------------------------------------------------------


def test_put_updates_existing(client, isolated_delegates_yaml):
    isolated_delegates_yaml.write_text(
        "delegates:\n"
        "  - name: ava\n"
        "    type: a2a\n"
        "    description: old.\n"
        "    url: http://ava/a2a\n"
    )
    r = client.put(
        "/api/delegates/ava",
        json={
            "name": "ava",
            "type": "a2a",
            "description": "new description.",
            "url": "http://ava-2/a2a",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["delegates"][0]["url"] == "http://ava-2/a2a"
    assert body["delegates"][0]["description"] == "new description."


def test_put_missing_returns_404(client, isolated_delegates_yaml):
    r = client.put(
        "/api/delegates/ghost",
        json={
            "name": "ghost",
            "type": "a2a",
            "description": "spooky.",
            "url": "http://nowhere/a2a",
        },
    )
    assert r.status_code == 404


def test_put_path_body_name_mismatch_returns_400(client, isolated_delegates_yaml):
    """Renames must use DELETE+POST so the registry sees both lifecycle
    events. PUT mismatching name → 400 with a hint."""
    isolated_delegates_yaml.write_text(
        "delegates:\n"
        "  - name: ava\n"
        "    type: a2a\n"
        "    description: hi.\n"
        "    url: http://ava/a2a\n"
    )
    r = client.put(
        "/api/delegates/ava",
        json={
            "name": "renamed",
            "type": "a2a",
            "description": "hi.",
            "url": "http://ava/a2a",
        },
    )
    assert r.status_code == 400
    assert "rename" in r.json()["error"]


def test_put_omits_name_in_body_inherits_from_path(client, isolated_delegates_yaml):
    """Body without a `name` field is filled in from the path — saves
    the UI from having to duplicate it."""
    isolated_delegates_yaml.write_text(
        "delegates:\n"
        "  - name: ava\n"
        "    type: a2a\n"
        "    description: old.\n"
        "    url: http://ava/a2a\n"
    )
    r = client.put(
        "/api/delegates/ava",
        json={
            "type": "a2a",
            "description": "new from path.",
            "url": "http://ava/a2a",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["delegates"][0]["description"] == "new from path."


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------


def test_delete_removes_entry(client, isolated_delegates_yaml):
    isolated_delegates_yaml.write_text(
        "delegates:\n"
        "  - name: ava\n"
        "    type: a2a\n"
        "    description: hi.\n"
        "    url: http://ava/a2a\n"
    )
    r = client.delete("/api/delegates/ava")
    assert r.status_code == 200
    assert r.json()["delegates"] == []
    assert "ava" not in app_module._DELEGATES.names()


def test_delete_missing_returns_404(client, isolated_delegates_yaml):
    r = client.delete("/api/delegates/ghost")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /test (reachability probe)
# ---------------------------------------------------------------------------


def test_test_a2a_happy_path(client, isolated_delegates_yaml, respx_mock):
    respx_mock.post("http://ava:3008/a2a").mock(side_effect=_task_not_found)
    r = client.post(
        "/api/delegates/test",
        json={
            "name": "ava",
            "type": "a2a",
            "description": "ping.",
            "url": "http://ava:3008/a2a",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "latency_ms" in body


def test_test_a2a_auth_rejected(client, isolated_delegates_yaml, respx_mock):
    respx_mock.post("http://ava:3008/a2a").respond(
        status_code=401,
    )
    r = client.post(
        "/api/delegates/test",
        json={
            "name": "ava",
            "type": "a2a",
            "description": "ping.",
            "url": "http://ava:3008/a2a",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "Authentication" in body["error"]


def test_test_a2a_unreachable(client, isolated_delegates_yaml, respx_mock):
    respx_mock.post("http://ava:3008/a2a").mock(
        side_effect=httpx.ConnectError("connection refused"),
    )
    r = client.post(
        "/api/delegates/test",
        json={
            "name": "ava",
            "type": "a2a",
            "description": "ping.",
            "url": "http://ava:3008/a2a",
        },
    )
    body = r.json()
    assert body["ok"] is False
    assert "Cannot reach" in body["error"]


def test_test_openai_routes_to_llm_probe(client, isolated_delegates_yaml, respx_mock):
    """The openai test path piggybacks on /api/llm/test's prober so
    error shapes stay consistent across the UI's two test buttons."""
    respx_mock.post("https://gateway/v1/chat/completions").respond(
        status_code=200,
        json={"choices": [{"message": {"content": "pong"}}]},
    )
    r = client.post(
        "/api/delegates/test",
        json={
            "name": "opus",
            "type": "openai",
            "description": "deep think.",
            "url": "https://gateway/v1",
            "model": "claude-opus-4-6",
        },
    )
    body = r.json()
    assert body["ok"] is True


def test_test_schema_rejection_returns_ok_false(client, isolated_delegates_yaml):
    """Schema errors come back as ``{ok: false, error: ...}`` rather
    than 400 — matches /api/llm/test's contract so the UI's test
    button can render the message inline regardless of cause."""
    r = client.post(
        "/api/delegates/test",
        json={"type": "openai", "url": "https://x/v1"},  # missing name + description
    )
    body = r.json()
    assert body["ok"] is False


def test_access_token_lifecycle_is_private_and_live(client, isolated_delegates_yaml, respx_mock):
    payload = {'name': 'hub', 'type': 'a2a', 'description': 'Local brain', 'url': 'http://hub/a2a',
               'auth': {'credentials': 'Bearer test-private-token'}}
    created = client.post('/api/delegates', json=payload)
    assert created.status_code == 200
    assert 'test-private-token' not in created.text
    assert isolated_delegates_yaml.stat().st_mode & 0o777 == 0o600
    assert app_module._DELEGATES.get('hub').auth_headers()['Authorization'] == 'Bearer test-private-token'
    saved = client.get('/api/delegates').json()['delegates'][0]
    assert saved['auth']['credentials'] == '__redacted__'
    route = respx_mock.post('http://hub/a2a').mock(side_effect=_task_not_found)
    assert client.post('/api/delegates/test', json=saved).json()['ok']
    assert route.calls[0].request.headers['Authorization'] == 'Bearer test-private-token'
    saved['description'] = 'Updated description'
    assert client.put('/api/delegates/hub', json=saved).status_code == 200
    assert app_module._DELEGATES.get('hub').a2a_credential == 'test-private-token'
    saved['url'] = 'http://other-agent/a2a'
    rejected = client.post('/api/delegates/test', json=saved).json()
    assert not rejected['ok']
    assert 'Paste the access token again' in rejected['error']
    assert client.put('/api/delegates/hub', json=saved).status_code == 400
    saved['url'] = 'http://hub/a2a'
    saved['auth']['credentials'] = ''
    saved['auth']['scheme'] = None
    assert client.put('/api/delegates/hub', json=saved).status_code == 200
    assert not app_module._DELEGATES.get('hub').a2a_credential


def test_pasted_secret_in_env_field_rejected_without_echo(client, isolated_delegates_yaml, caplog):
    secret = '126e5a54-test-private-token'
    entry = {'name': 'hub', 'type': 'a2a', 'description': 'Brain', 'url': 'http://hub/a2a',
             'auth': {'scheme': 'bearer', 'credentialsEnv': secret}}
    response = client.post('/api/delegates', json=entry)
    assert response.status_code == 400
    assert 'Access token' in response.json()['error']
    assert secret not in response.text
    assert not isolated_delegates_yaml.exists()
    from agent.delegates import _parse_entry
    assert _parse_entry(entry) is None
    assert secret not in caplog.text


def test_missing_environment_credential_never_reports_connected(client, monkeypatch):
    monkeypatch.delenv('ORBIS_TEST_MISSING_KEY', raising=False)
    result = client.post('/api/delegates/test', json={
        'name': 'hub', 'type': 'a2a', 'description': 'Brain', 'url': 'http://hub/a2a',
        'auth': {'scheme': 'bearer', 'credentialsEnv': 'ORBIS_TEST_MISSING_KEY'},
    }).json()
    assert not result['ok']
    assert 'Access token missing' in result['error']


def test_public_card_cannot_mask_rejected_rpc(client, respx_mock):
    card = respx_mock.get('http://hub/.well-known/agent-card.json').respond(200, json={'name': 'hub'})
    respx_mock.post('http://hub/a2a').respond(401)
    result = client.post('/api/delegates/test', json={
        'name': 'hub', 'type': 'a2a', 'description': 'Brain', 'url': 'http://hub/a2a',
        'auth': {'credentials': 'wrong-token'},
    }).json()
    assert not result['ok']
    assert result['status'] == 401
    assert not card.called


@pytest.mark.parametrize('url', ['http://127.0.0.1:7870/a2a', 'http://192.168.1.20/a2a'])
def test_owner_can_connect_local_agents_without_redirecting_secrets(client, respx_mock, url):
    target = respx_mock.post(url).respond(307, headers={'Location': 'http://other/a2a'}, text='private peer response')
    redirected = respx_mock.post('http://other/a2a').respond(200)
    response = client.post('/api/delegates/test', json={
        'name': 'local', 'type': 'a2a', 'description': 'Local brain', 'url': url,
        'auth': {'credentials': 'private-token'},
    })
    assert target.called
    assert not redirected.called
    assert not response.json()['ok']
    assert 'private peer response' not in response.text
    assert 'private-token' not in response.text


def test_probe_unexpected_error_is_actionable_and_private(client, monkeypatch):
    import agent.delegates
    async def broken(*args, **kwargs):
        raise RuntimeError('private-token')
    monkeypatch.setattr(agent.delegates, 'probe', broken)
    response = client.post('/api/delegates/test', json={
        'name': 'local', 'type': 'a2a', 'description': 'Brain', 'url': 'http://localhost/a2a',
    })
    assert response.status_code == 200
    assert not response.json()['ok']
    assert 'try again' in response.json()['error']
    assert 'private-token' not in response.text


def test_discovery_excludes_default_ports(client, monkeypatch):
    from agent import discovery
    from agent.delegates import Delegate
    monkeypatch.setattr(app_module._DELEGATES, 'all', lambda: [
        Delegate(name='http', type='a2a', description='Brain', url='http://peer/a2a'),
        Delegate(name='https', type='a2a', description='Brain', url='https://secure/a2a'),
    ])
    async def discover(*, known):
        assert ('peer', 80) in known
        assert ('secure', 443) in known
        return []
    monkeypatch.setattr(discovery, 'discover', discover)
    assert client.get('/api/delegates/discover').json() == {'discovered': []}


@pytest.mark.parametrize('method,path', [('get', '/api/delegates'), ('put', '/api/delegates/hub'), ('delete', '/api/delegates/hub')])
def test_corrupt_settings_return_structured_error(client, isolated_delegates_yaml, method, path):
    isolated_delegates_yaml.write_text('delegates: [')
    kwargs = {'json': {}} if method == 'put' else {}
    response = getattr(client, method)(path, **kwargs)
    assert response.status_code >= 400
    assert 'invalid YAML' in response.json()['error']
