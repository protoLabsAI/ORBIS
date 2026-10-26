"""Voice observability: turn-id log filter, [turn] summary line, LLM voice
doctor (drift, endpoint auth, no secret leakage), boot banner."""

from __future__ import annotations

import json
import logging

import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    ErrorFrame,
    FunctionCallsStartedFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    MetricsFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.metrics.metrics import TTFAMetricsData, TTFBMetricsData
from pipecat.observers.base_observer import FramePushed
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import LLMService

import agent.turn_observer as turn_mod
from agent.boot_banner import env_fallbacks_used
from agent.latency import LatencyObserver
from agent.llm_status import (
    LLMStatus,
    configured_from_llm_block,
    describe_service,
    scrub,
    url_host,
)
from agent.turn_observer import TurnLogFilter, TurnObserver

_LLM = LLMService.__new__(LLMService)


def _pushed(frame) -> FramePushed:
    return FramePushed(
        source=None,
        destination=None,
        frame=frame,
        direction=FrameDirection.DOWNSTREAM,
        timestamp=0,
    )


class _Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


# --- turn-id log filter ----------------------------------------------------


def test_filter_renders_dash_without_turn(monkeypatch):
    monkeypatch.setattr(turn_mod, "_current_turn", None)
    monkeypatch.setattr(turn_mod, "_current_session", None)
    rec = logging.LogRecord("x", logging.INFO, "f", 1, "m", None, None)
    assert TurnLogFilter().filter(rec)
    assert rec.turn == "-" and rec.session == "-"
    assert "[t=-]" in logging.Formatter("[t=%(turn)s]").format(rec)


@pytest.mark.asyncio
async def test_filter_injects_minted_turn(monkeypatch):
    monkeypatch.setattr(turn_mod, "_current_turn", None)
    obs = TurnObserver(session_id="abcdef123456", clock=_Clock())
    await obs.on_push_frame(_pushed(UserStartedSpeakingFrame()))
    rec = logging.LogRecord("x", logging.INFO, "f", 1, "m", None, None)
    TurnLogFilter().filter(rec)
    assert rec.turn == 1
    assert rec.session == "abcdef12"


# --- [turn] summary line ---------------------------------------------------


async def _run(obs, clock, steps):
    for dt, frame in steps:
        clock.t += dt
        await obs.on_push_frame(_pushed(frame))


def _turn_lines(caplog):
    return [r for r in caplog.records if r.getMessage().startswith("[turn]")]


@pytest.mark.asyncio
async def test_turn_summary_line_assembled(caplog):
    clock = _Clock()
    obs = TurnObserver(
        clock=clock,
        brief=lambda: {
            "model": "gpt-x",
            "member": "primary",
            "host": "api.example.com",
        },
    )
    metrics = MetricsFrame(
        data=[
            TTFBMetricsData(processor="OpenAILLMService#0", value=0.8),
            TTFAMetricsData(
                processor="KokoroTTSService#0", ttfa=0.4, ttfb=0.3, leading_silence=0.1
            ),
        ]
    )
    with caplog.at_level(logging.INFO, logger="agent.turn_observer"):
        await _run(
            obs,
            clock,
            [
                (0, UserStartedSpeakingFrame()),
                (1.0, VADUserStoppedSpeakingFrame()),
                (0, UserStoppedSpeakingFrame()),
                (1.0, TranscriptionFrame("hello there how are", "u", "t")),
                (0.1, LLMFullResponseStartFrame()),
                (0.9, LLMTextFrame("Hi! ")),
                (0.1, metrics),
                (0.2, BotStartedSpeakingFrame()),
                (1.0, LLMTextFrame("Doing well.")),
                (0.1, LLMFullResponseEndFrame()),
                (0.5, BotStoppedSpeakingFrame()),
            ],
        )
    (rec,) = _turn_lines(caplog)
    msg = rec.getMessage()
    assert rec.levelno == logging.INFO
    assert "n=1 " in msg
    assert "stt=1.00s(19ch)" in msg
    assert "llm_ttfb=0.80s" in msg
    assert "llm_total=2.30s" in msg
    assert "tts_ttfa=0.40s" in msg
    assert "first_audio=2.30s" in msg
    assert "stop=end_turn" in msg
    assert "out_chars=15" in msg
    assert "tools=[]" in msg
    assert "model=gpt-x member=primary host=api.example.com" in msg


@pytest.mark.asyncio
async def test_slow_first_audio_warns_and_unknowns_render_dash(caplog):
    clock = _Clock()
    obs = TurnObserver(clock=clock, warn_first_audio_secs=3.0)
    with caplog.at_level(logging.INFO, logger="agent.turn_observer"):
        await _run(
            obs,
            clock,
            [
                (0, UserStartedSpeakingFrame()),
                (0.5, UserStoppedSpeakingFrame()),
                (4.0, BotStartedSpeakingFrame()),
                (1.0, BotStoppedSpeakingFrame()),
            ],
        )
    (rec,) = _turn_lines(caplog)
    assert rec.levelno == logging.WARNING
    assert "first_audio=4.00s" in rec.getMessage()
    assert "stt=-(0ch)" in rec.getMessage()
    assert "llm_ttfb=- " in rec.getMessage() and "model=-" in rec.getMessage()


@pytest.mark.asyncio
async def test_interrupted_error_and_tool_use(caplog):
    clock = _Clock()
    obs = TurnObserver(clock=clock)
    call = type("C", (), {"function_name": "get_weather"})()
    with caplog.at_level(logging.INFO, logger="agent.turn_observer"):
        # Turn 1: tool call, no text -> tool_use; ended by the user barging in
        # after the bot never spoke (new speech finalizes it).
        await _run(
            obs,
            clock,
            [
                (0, UserStartedSpeakingFrame()),
                (1, UserStoppedSpeakingFrame()),
                (0.1, LLMFullResponseStartFrame()),
                (0.1, FunctionCallsStartedFrame(function_calls=[call])),
                (0.1, LLMFullResponseEndFrame()),
                # Turn 2: LLM errors with no recovery.
                (1, UserStartedSpeakingFrame()),
                (1, UserStoppedSpeakingFrame()),
                (0.1, LLMFullResponseStartFrame()),
                (0.1, ErrorFrame(error="boom", processor=_LLM)),
                (0.1, LLMFullResponseEndFrame()),
                # Turn 3: user barges in while the bot is mid-speech.
                (1, UserStartedSpeakingFrame()),
                (1, UserStoppedSpeakingFrame()),
                (0.1, LLMFullResponseStartFrame()),
                (0.1, LLMTextFrame("long answer")),
                (0.1, BotStartedSpeakingFrame()),
                (1, UserStartedSpeakingFrame()),
            ],
        )
    msgs = [r.getMessage() for r in _turn_lines(caplog)]
    assert (
        "n=1 " in msgs[0]
        and "stop=tool_use" in msgs[0]
        and "tools=['get_weather']" in msgs[0]
    )
    assert "n=2 " in msgs[1] and "stop=error" in msgs[1]
    assert "n=3 " in msgs[2] and "stop=interrupted" in msgs[2]


@pytest.mark.asyncio
async def test_duplicate_hops_and_vad_flicker_do_not_double_count(caplog):
    clock = _Clock()
    obs = TurnObserver(clock=clock)
    started = UserStartedSpeakingFrame()
    text = LLMTextFrame("abc")
    with caplog.at_level(logging.INFO, logger="agent.turn_observer"):
        await obs.on_push_frame(_pushed(started))
        await obs.on_push_frame(_pushed(started))  # same frame, next hop
        await obs.on_push_frame(_pushed(UserStartedSpeakingFrame()))  # flicker
        await obs.on_push_frame(_pushed(text))
        await obs.on_push_frame(_pushed(text))
        await obs.on_push_frame(_pushed(BotStartedSpeakingFrame()))
        await obs.on_push_frame(_pushed(BotStoppedSpeakingFrame()))
    (rec,) = _turn_lines(caplog)
    assert "n=1 " in rec.getMessage() and "out_chars=3" in rec.getMessage()


# --- LatencyObserver: errored request has no ttfb --------------------------


@pytest.mark.asyncio
async def test_llm_error_cancels_open_ttfb():
    cancelled = []

    class _Svc(LLMService):
        async def cancel_ttfb_metrics(self):
            cancelled.append(True)

    svc = _Svc.__new__(_Svc)
    await LatencyObserver().on_push_frame(
        _pushed(ErrorFrame(error="400 bad request", processor=svc))
    )
    assert cancelled == [True]


@pytest.mark.asyncio
async def test_latency_observer_feeds_status():
    from agent.llm_status import llm_status

    llm_status.reset()
    frame = MetricsFrame(
        data=[TTFBMetricsData(processor="OpenAILLMService#0", value=0.5)]
    )
    await LatencyObserver().on_push_frame(_pushed(frame))
    assert llm_status.last_ttfb_ms == 500
    assert llm_status.last_ok_at is not None
    llm_status.reset()


# --- doctor: drift ---------------------------------------------------------


class _FakeSvc:
    def __init__(self, base_url: str, model: str):
        self._client = type("C", (), {"base_url": base_url})()
        self._settings = type("S", (), {"model": model})()


def _status(cfg_model="m1", cfg_url="https://api.example.com/v1", live=None):
    st = LLMStatus()
    st.bind(
        [live or _FakeSvc("https://api.example.com/v1/", "m1")],
        lambda: configured_from_llm_block(
            {"url": cfg_url, "model": cfg_model},
            default_url="http://localhost:8100/v1",
            default_model="local",
            config_mtime=1.0,
        ),
    )
    return st


def test_no_drift_when_signatures_match():
    snap = _status().snapshot()
    assert snap["drift"] is False
    assert snap["running"]["member"] == "primary"


def test_drift_on_model_mismatch():
    assert _status(cfg_model="m2").snapshot()["drift"] is True


def test_drift_on_host_mismatch():
    st = _status(cfg_url="https://other.example.org/v1")
    assert st.snapshot()["drift"] is True


def test_no_drift_when_unbound_or_failed_over():
    assert LLMStatus().snapshot()["drift"] is False
    st = LLMStatus()
    st.bind(
        [_FakeSvc("https://a.test/v1", "m1"), _FakeSvc("https://b.test/v1", "m9")],
        lambda: {"host": "a.test", "model": "m1", "extra_models": []},
    )
    st.record_failover(1)
    snap = st.snapshot()
    assert snap["running"]["member"] == "backup"
    assert snap["failovers"] == 1
    assert snap["drift"] is False


def test_error_recording_scrubs_and_truncates():
    st = _status()
    st.record_error("auth", "401 bad key sk-abcdef1234567890 " + "x" * 500, status=401)
    err = st.snapshot()["last_error"]
    assert err["status"] == 401 and err["class"] == "auth"
    assert "sk-abcdef" not in err["msg"] and len(err["msg"]) <= 200
    assert "msg" not in st.snapshot(public=True)["last_error"]


def test_helpers():
    assert (
        url_host("https://user:pw@api.example.com:8443/v1?key=abc")
        == "api.example.com:8443"
    )
    assert "pw" not in json.dumps(
        describe_service(_FakeSvc("https://u:pw@h.test/v1?k=1", "m"))
    )
    assert scrub("Bearer abcdefghijkl") == "[redacted]"


def test_env_fallbacks_used():
    env = {"LLM_URL": "http://x", "LLM_SERVED_NAME": "m", "LLM_API_KEY": "k"}
    cfg = {"llm": {"model": "from-yaml", "api_key_env": "FOO"}}
    assert env_fallbacks_used(cfg, env) == ["llm.url<-LLM_URL"]
    assert env_fallbacks_used({}, {}) == []


# --- doctor endpoint: auth + no secret leakage -----------------------------


@pytest.fixture()
def client(monkeypatch):
    from fastapi.testclient import TestClient

    import app as app_module
    from agent.llm_status import llm_status

    llm_status.reset()
    llm_status.bind(
        [_FakeSvc("https://api.example.com/v1", "m1")],
        lambda: configured_from_llm_block(
            {
                "url": "https://api.example.com/v1?token=SECRETQUERY",
                "model": "m1",
                "api_key": "sk-supersecretkey000",
            },
            default_url="http://localhost:8100/v1",
            default_model="local",
            config_mtime=1.0,
        ),
    )
    llm_status.record_error("auth", "401 with sk-supersecretkey000", status=401)
    yield TestClient(app_module.app), app_module
    llm_status.reset()


def test_doctor_requires_auth_when_multi_user(client, monkeypatch):
    c, app_module = client
    import auth.users as auth_users

    monkeypatch.setattr(auth_users.user_registry, "single_user_mode", lambda: False)
    monkeypatch.setattr(auth_users.user_registry, "resolve", lambda key: None)
    assert c.get("/api/voice/doctor").status_code == 401


def test_doctor_returns_detail_without_secrets(client):
    c, _ = client
    resp = c.get("/api/voice/doctor")
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"]["url"] == "https://api.example.com/v1"
    assert body["running"]["url"] == "https://api.example.com/v1"
    assert body["last_error"]["status"] == 401
    dumped = json.dumps(body)
    assert "supersecret" not in dumped and "SECRETQUERY" not in dumped


def test_healthz_exposes_host_only_copy(client):
    c, _ = client
    body = c.get("/healthz").json()
    llm = body["voice"]["llm"]
    assert llm["configured"]["host"] == "api.example.com"
    assert "url" not in llm["configured"] and "url" not in llm["running"]
    assert "msg" not in llm["last_error"]
    assert "supersecret" not in json.dumps(body)


def test_diagnostics_includes_config_banner(client):
    c, _ = client
    banner = c.get("/api/diagnostics").json()["config_banner"]
    assert {"path", "mtime", "env_fallbacks_used", "version"} <= set(banner)


@pytest.mark.asyncio
async def test_stt_time_is_measured_from_vad_stop_not_turn_stop(caplog) -> None:
    """Batch STT: the transcript lands *before* UserStoppedSpeakingFrame, so
    anchoring on the latter reported ``stt=-0.00s``."""
    clock = _Clock()
    obs = TurnObserver(clock=clock)
    with caplog.at_level(logging.INFO, logger="agent.turn_observer"):
        await _run(
            obs,
            clock,
            [
                (0, UserStartedSpeakingFrame()),
                (1.0, VADUserStoppedSpeakingFrame()),
                (0.4, TranscriptionFrame("hello", "u", "t")),
                (0, UserStoppedSpeakingFrame()),
                (0.1, LLMFullResponseStartFrame()),
                (0.1, LLMTextFrame("Hi.")),
                (0.1, LLMFullResponseEndFrame()),
                (0.1, BotStartedSpeakingFrame()),
                (0.1, BotStoppedSpeakingFrame()),
            ],
        )
    (rec,) = _turn_lines(caplog)
    assert "stt=0.40s(5ch)" in rec.getMessage()
