"""Config changes must tell the truth: coherence checks, per-block "applies"
verdicts, honest live-swap results, and no secrets in the POST response."""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pipecat.services.openai.llm import OpenAILLMService

import agent.config_store as store
import app as app_module
from agent.config_store import (
    REDACTED_SECRET,
    _check_llm_coherence,
    applies_map,
    merge_patch_with_warnings,
    write_config,
)
from agent.user_state import user_state_for
from voice.llm import adapter_kind, live_adapter_kind, make_llm

GATEWAY = "https://gateway.example/v1"


def _svc(url=GATEWAY, model="m", provider=None, **kw):
    return make_llm(
        base_url=url,
        model=model,
        api_key="k",
        settings=OpenAILLMService.Settings(model=model),
        provider=provider,
        **kw,
    )


def _cfg(url=GATEWAY, model="m2", **kw):
    return {"url": url, "model": model, "api_key": "k", "provider": None,
            "extra_body": None, "router_model": None, "content_model": None, **kw}


@pytest.fixture
def state():
    st = user_state_for("honest-test")
    st.active_llm = None
    st.active_llm_build = None
    st.filler_generator = object()
    yield st
    st.active_llm = None
    st.active_llm_build = None


def _reconf(cfg, **kw):
    return app_module._reconfigure_live_llm(cfg, user_id="honest-test", **kw)


# --- _reconfigure_live_llm ---------------------------------------------------


def test_no_live_session_is_reported_and_logged(state, caplog):
    with caplog.at_level(logging.INFO):
        res = _reconf(_cfg())
    assert res["applied"] == "next_session" and res["reason"]
    assert "reconfigure deferred" in caplog.text
    assert state.filler_generator is None  # reset even with no session


def test_same_adapter_model_swap_is_live_and_resets_filler(state):
    svc = _svc()
    state.active_llm = svc
    state.active_llm_build = app_module._llm_build_snapshot(_cfg(), None)
    res = _reconf(_cfg(model="new-model"), fallback_cfg=None)
    assert res["applied"] == "live" and res["ok"] is True
    assert svc._settings.model == "new-model"
    assert state.filler_generator is None


def test_adapter_class_change_needs_restart(state, caplog):
    state.active_llm = _svc()
    with caplog.at_level(logging.INFO):
        res = _reconf(_cfg(url="http://127.0.0.1:11434/v1"), fallback_cfg=None)
    assert res["applied"] == "restart" and res["needs_restart"] is True
    assert "openai to ollama" in res["reason"]
    assert "refused" in caplog.text


def test_oauth_boundary_crossing_is_logged_restart(state, caplog):
    svc = _svc(GATEWAY)
    svc.__class__ = type("AnthropicOAuthLLMService", (type(svc),), {})
    state.active_llm = svc
    assert live_adapter_kind(svc) == "anthropic-oauth"
    with caplog.at_level(logging.INFO):
        res = _reconf(_cfg(), fallback_cfg=None)  # provider nulled by the UI
    assert res["applied"] == "restart"
    assert "anthropic-oauth to openai" in res["reason"]
    assert "refused" in caplog.text


def test_ollama_url_change_is_refused_not_pretended(state):
    svc = _svc("http://127.0.0.1:11434/v1", provider="ollama")
    state.active_llm = svc
    res = _reconf(_cfg(url="http://other-host:11434/v1", provider="ollama"),
                  fallback_cfg=None)
    assert res["applied"] == "restart"
    assert "Ollama endpoint" in res["reason"]


def test_ollama_model_change_same_root_is_live(state):
    svc = _svc("http://127.0.0.1:11434/v1", provider="ollama")
    state.active_llm = svc
    res = _reconf(_cfg(url="http://127.0.0.1:11434/v1", provider="ollama",
                       model="llama-x"), fallback_cfg=None)
    assert res["applied"] == "live"
    assert svc._settings.model == "llama-x"


@pytest.mark.parametrize("change,needle", [
    ({"extra_body": {"think": False}}, "extra_body"),
    ({"provider": "openai"}, "provider"),
])
def test_build_time_fields_changed_need_restart(state, change, needle):
    state.active_llm = _svc()
    state.active_llm_build = app_module._llm_build_snapshot(_cfg(), None)
    res = _reconf(_cfg(**change), fallback_cfg=None)
    assert res["applied"] == "restart" and needle in res["reason"]


def test_fallback_change_needs_restart(state):
    state.active_llm = _svc()
    state.active_llm_build = app_module._llm_build_snapshot(_cfg(), None)
    fb = {"url": "http://b/v1", "model": "b", "api_key": "x", "provider": None,
          "extra_body": None}
    res = _reconf(_cfg(), fallback_cfg=fb)
    assert res["applied"] == "restart" and "failover" in res["reason"]


def test_two_model_split_needs_restart_on_plain_service(state):
    state.active_llm = _svc()
    res = _reconf(_cfg(router_model="a", content_model="b"), fallback_cfg=None)
    assert res["applied"] == "restart"


def test_adapter_kind_matches_make_llm():
    for kw, kind in [
        (dict(base_url=GATEWAY), "openai"),
        (dict(base_url="http://127.0.0.1:11434/v1"), "ollama"),
        (dict(base_url=GATEWAY, provider="anthropic-oauth"), "anthropic-oauth"),
        (dict(base_url="mlx://x"), "mlx"),
        (dict(base_url=GATEWAY, model="a", router_model="a", content_model="b"),
         "two-model"),
    ]:
        assert adapter_kind(**kw) == kind
    assert live_adapter_kind(_svc()) == "openai"
    assert live_adapter_kind(_svc("http://127.0.0.1:11434/v1")) == "ollama"


# --- coherence ----------------------------------------------------------------


def test_coherence_hard_rejects():
    errs, _ = _check_llm_coherence(
        {"llm": {"provider": "anthropic-oauth", "model": "protolabs/smart"}})
    assert errs
    errs, _ = _check_llm_coherence(
        {"llm": {"url": "https://api.anthropic.com/v1", "model": "protolabs/fast"}})
    assert errs
    errs, _ = _check_llm_coherence(
        {"llm": {"url": "https://api.openai.com/v1", "model": "protolabs/fast"}})
    assert errs


def test_coherence_warnings():
    def w(llm):
        return _check_llm_coherence({"llm": llm})[1]

    assert w({"provider": "openai", "url": "https://x/v1", "model": "claude-3"})
    assert w({"provider": "ollama", "url": "https://api.openai.com/v1", "model": "m"})
    assert w({"model": "m"})  # empty url
    assert w({"url": "mlx://m", "provider": "openai", "model": "m"})
    assert not w({"url": "http://127.0.0.1:11434/v1", "provider": "ollama", "model": "m"})
    assert not w({"provider": "anthropic-oauth", "model": "claude-sonnet-4-5"})


def test_merge_rejects_incoherent_and_leaves_file_untouched(tmp_path: Path):
    p = tmp_path / "orbis.yaml"
    write_config({"llm": {"url": GATEWAY, "model": "ok"}}, p)
    before = p.read_text()
    with pytest.raises(ValueError, match="gateway alias"):
        merge_patch_with_warnings(
            {"llm": {"provider": "anthropic-oauth", "model": "protolabs/smart"}}, p)
    assert p.read_text() == before


def test_endpoint_change_drops_dependent_keys_unless_in_patch(tmp_path: Path):
    p = tmp_path / "orbis.yaml"
    write_config({"llm": {
        "url": GATEWAY, "model": "m", "api_key_env": "OLD", "micro_model": "mm",
        "router_model": "r", "content_model": "c",
        "extra_body": {"a": 1}, "fallback": {"url": "http://fb"},
    }}, p)
    out, warnings = merge_patch_with_warnings(
        {"llm": {"url": "https://other/v1", "micro_model": "kept"}}, p)
    llm = out["llm"]
    assert llm["micro_model"] == "kept"
    for gone in ("api_key_env", "router_model", "content_model", "extra_body", "fallback"):
        assert gone not in llm
    assert any("api_key_env" in x for x in warnings)


def test_same_endpoint_keeps_dependent_keys(tmp_path: Path):
    p = tmp_path / "orbis.yaml"
    write_config({"llm": {"url": GATEWAY, "model": "m", "micro_model": "mm"}}, p)
    out, warnings = merge_patch_with_warnings(
        {"llm": {"url": GATEWAY + "/", "model": "m2", "provider": None}}, p)
    assert out["llm"]["micro_model"] == "mm"
    assert warnings == []


# --- applies table -----------------------------------------------------------


def test_applies_map_classifies_changed_keys_only():
    old = {"llm": {"url": "u", "model": "a", "provider": None},
           "persona": {"name": "A", "temperature": 0.5}}
    new = {"llm": {"url": "u", "model": "b"},
           "persona": {"name": "B", "temperature": 0.5}}
    assert applies_map(old, new) == {"llm": "live", "persona": "next_session"}
    new["llm"]["extra_body"] = {"x": 1}
    new["persona"]["temperature"] = 0.9
    new["voice"] = {"tts_backend": "kokoro"}
    assert applies_map(old, new) == {
        "llm": "restart", "persona": "restart", "voice": "restart"}


# --- POST /api/config --------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    p = tmp_path / "orbis.yaml"
    monkeypatch.setattr(store, "DEFAULT_PATH", str(p))
    monkeypatch.setenv("ORBIS_CONFIG", str(p))
    return TestClient(app_module.app)


def test_post_config_redacts_secrets_and_reports_apply(client, monkeypatch):
    import server.routers.config as cfg_router

    monkeypatch.setattr(
        cfg_router, "_reconfigure_live_llm",
        lambda *a, **k: {"applied": "restart", "reason": "because"})
    r = client.post("/api/config", json={"llm": {
        "url": GATEWAY, "model": "m", "api_key": "sk-secret",
        "micro_api_key": "sk-micro",
        "fallback": {"url": "http://fb/v1", "api_key": "sk-fb"},
    }})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "sk-secret" not in r.text and "sk-micro" not in r.text and "sk-fb" not in r.text
    assert body["config"]["llm"]["api_key"] == REDACTED_SECRET
    assert body["config"]["llm"]["fallback"]["api_key"] == REDACTED_SECRET
    assert body["llm_apply"] == {"applied": "restart", "reason": "because"}
    assert body["llm_applied_live"] is False
    assert body["applies"]["llm"] == "restart"
    assert isinstance(body["warnings"], list)


def test_post_config_incoherent_is_400(client):
    r = client.post("/api/config", json={"llm": {
        "provider": "anthropic-oauth", "model": "protolabs/smart"}})
    assert r.status_code == 400


# --- prewarm -----------------------------------------------------------------


def test_prewarm_logs_host_and_status(monkeypatch, caplog):
    import httpx

    monkeypatch.setattr(app_module, "_active_skill", lambda: SimpleNamespace(
        llm={"url": "https://gw.example/v1", "model": "m", "api_key": "sekret"}))
    monkeypatch.setattr(app_module.httpx, "post", lambda *a, **kw: httpx.Response(
        401, request=httpx.Request("POST", "https://gw.example/v1/chat/completions")))
    with caplog.at_level(logging.INFO):
        app_module.prewarm_llm()
    assert "host=gw.example" in caplog.text and "status=401" in caplog.text
    assert "sekret" not in caplog.text
