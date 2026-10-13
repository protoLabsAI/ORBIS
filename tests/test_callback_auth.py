"""Real sender/receiver coverage for callback-only credentials."""
import importlib

import httpx
import pytest
from fastapi import FastAPI


@pytest.fixture
def callback_env(tmp_path, monkeypatch):
    monkeypatch.setenv('ORBIS_DB_PATH', str(tmp_path / 'orbis.sqlite'))
    monkeypatch.delenv('A2A_PUSH_TOKEN', raising=False)
    return tmp_path


def test_callback_token_is_private_persistent_and_overridable(callback_env, monkeypatch):
    from agent.callback_auth import callback_token
    first = callback_token()
    assert len(first) >= 40
    assert callback_token() == first
    assert (callback_env / 'a2a-callback-token').stat().st_mode & 0o777 == 0o600
    monkeypatch.setenv('A2A_PUSH_TOKEN', 'explicit-token')
    assert callback_token() == 'explicit-token'


@pytest.mark.asyncio
async def test_real_sdk_sender_authenticates_only_callback(callback_env, monkeypatch):
    from a2a.server.tasks import BasePushNotificationSender, InMemoryPushNotificationConfigStore
    from a2a.types import Task, TaskPushNotificationConfig
    from agent.callback_auth import callback_token
    import a2a_auth
    auth = importlib.reload(a2a_auth)
    monkeypatch.delenv('A2A_AUTH_TOKEN', raising=False)
    monkeypatch.delenv('A2A_ALLOW_UNAUTH', raising=False)
    app = FastAPI()
    received = []

    @app.post('/a2a/callback')
    async def callback():
        received.append(True)
        return {'ok': True}

    @app.post('/a2a')
    async def rpc():
        return {'ok': True}

    auth.install(app, bearer_token='', api_key='', allowed_origins_raw='')
    token = callback_token()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://orbis') as client:
        sender = BasePushNotificationSender(client, InMemoryPushNotificationConfigStore())
        task = Task(id='task-1')
        config = TaskPushNotificationConfig(url='http://orbis/a2a/callback', token=token)
        assert await sender._dispatch_notification(task, config, task.id)
        assert received == [True]
        assert (await client.post('/a2a/callback')).status_code == 401
        assert (await client.post('/a2a/callback', headers={'X-A2A-Notification-Token':'wrong'})).status_code == 401
        assert (await client.post('/a2a', headers={'X-A2A-Notification-Token':token})).status_code == 401
        assert (await client.get('/a2a/callback', headers={'X-A2A-Notification-Token':token})).status_code == 401
        assert (await client.post('/a2a/callback/other', headers={'X-A2A-Notification-Token':token})).status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize('callback_url,local', [(None, True), ('https://callbacks.example/result', False)])
async def test_adapter_only_sends_scoped_token_to_own_callback(callback_env, monkeypatch, callback_url, local):
    from a2a_outbound import A2AResult
    from agent.callback_auth import callback_token
    from agent.delegate_adapters import A2AAdapter
    from agent.delegates import Delegate
    monkeypatch.setenv('ORBIS_BOUND_PORT', '7866')
    monkeypatch.setenv('A2A_AUTH_TOKEN', 'operator-secret-never-share')
    calls = []
    class Client:
        async def send(self, query, **kwargs):
            calls.append(kwargs)
            return A2AResult(text='done', state='completed')
    adapter = A2AAdapter()
    monkeypatch.setattr(adapter, 'client_for', lambda delegate: Client())
    delegate = Delegate(name='hub', type='a2a', description='Brain', url='http://127.0.0.1:7870/a2a')
    assert await adapter.dispatch(delegate, 'ping', push_notification_url=callback_url) == 'done'
    assert calls[0]['push_notification_token'] == (callback_token() if local else None)
    assert calls[0]['push_notification_token'] != 'operator-secret-never-share'


@pytest.mark.asyncio
async def test_started_stream_failure_is_not_redispatched(callback_env, monkeypatch):
    from a2a_outbound import A2ADispatchError
    from agent.delegate_adapters import A2AAdapter
    from agent.delegates import Delegate
    monkeypatch.setenv('A2A_STREAM', '1')
    calls = []
    class Client:
        async def send(self, query, **kwargs):
            calls.append(kwargs)
            raise A2ADispatchError('stream disconnected', task_started=True)
    adapter = A2AAdapter()
    monkeypatch.setattr(adapter, 'client_for', lambda delegate: Client())
    delegate = Delegate(name='hub', type='a2a', description='Brain', url='http://hub/a2a')
    with pytest.raises(A2ADispatchError, match='disconnected'):
        await adapter.dispatch(delegate, 'ping')
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_real_sdk_guard_rejection_runs_agent_once_via_stream(callback_env, monkeypatch):
    from a2a_outbound import A2AClient
    import a2a_server
    monkeypatch.setenv('A2A_ALLOW_UNAUTH', '1')
    monkeypatch.delenv('PUSH_NOTIFICATION_ALLOWED_HOSTS', raising=False)
    monkeypatch.delenv('PUSH_NOTIFICATION_ALLOWED_CIDRS', raising=False)
    monkeypatch.setattr(a2a_server, 'A2A_AUTH_TOKEN', '')
    executions = []
    async def stream(text, context_id, **kwargs):
        executions.append(text)
        yield ('done', 'callback-free success')
    app = FastAPI()
    a2a_server.register_a2a_routes(app, text_stream_factory=stream, version='test')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://peer') as client:
        outbound = A2AClient('http://peer/a2a', httpx_client=client, card_origin='http://peer')
        result = await outbound.send('ping', push_notification_url='http://127.0.0.1:7866/a2a/callback')
    assert result.text == 'callback-free success'
    assert executions == ['ping']
