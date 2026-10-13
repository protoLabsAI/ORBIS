"""Startup pings must agree with the selected persona and report real status."""
import logging
from types import SimpleNamespace

import httpx
import pytest

import app


def test_prewarm_uses_saved_persona_not_stale_gateway(monkeypatch, caplog):
    monkeypatch.setattr(app, 'LLM_URL', 'https://stale-gateway/v1')
    monkeypatch.setattr(app, 'LLM_API_KEY', 'stale-secret')
    monkeypatch.setattr(app, '_active_skill', lambda: SimpleNamespace(llm={
        'url': 'http://localhost:11434/v1/', 'model': 'chosen-model',
        'api_key': 'chosen-secret', 'extra_body': {'think': False},
    }))
    requests = []
    def post(url, **kwargs):
        requests.append((url, kwargs))
        return httpx.Response(200, request=httpx.Request('POST', url))
    monkeypatch.setattr(app.httpx, 'post', post)
    with caplog.at_level(logging.INFO):
        app.prewarm_llm()
    url, kwargs = requests[0]
    assert url == 'http://localhost:11434/v1/chat/completions'
    assert kwargs['json']['model'] == 'chosen-model'
    assert kwargs['json']['think'] is False
    assert kwargs['headers']['Authorization'] == 'Bearer chosen-secret'
    assert 'LLM warm' in caplog.text
    assert 'chosen-secret' not in caplog.text


@pytest.mark.parametrize('provider,url', [
    ('anthropic-oauth', 'https://api.anthropic.com'),
    ('openai-codex', 'https://chatgpt.com/backend-api/codex'),
    ('mlx', 'mlx://local-model'),
    (None, 'mlx://local-model'),
])
def test_protocol_specific_provider_does_not_ping_gateway(monkeypatch, provider, url):
    monkeypatch.setattr(app, '_active_skill', lambda: SimpleNamespace(llm={'provider': provider, 'url': url}))
    monkeypatch.setattr(app.httpx, 'post', lambda *a, **kw: pytest.fail('unexpected chat-completions request'))
    app.prewarm_llm()


def test_rejected_warmup_never_logs_success(monkeypatch, caplog):
    monkeypatch.setattr(app, '_active_skill', lambda: SimpleNamespace(llm={'url': 'http://local/v1', 'model': 'm'}))
    monkeypatch.setattr(app.httpx, 'post', lambda *a, **kw: httpx.Response(401, request=httpx.Request('POST', 'http://local/v1/chat/completions')))
    with caplog.at_level(logging.INFO):
        app.prewarm_llm()
    assert 'LLM warm' not in caplog.text
    assert 'LLM prewarm skipped (HTTPStatusError)' in caplog.text
