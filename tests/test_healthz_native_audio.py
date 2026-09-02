"""Tests for the native-audio fields exposed by /healthz.

The macOS live-validation harness depends on these keys to prove that
the sidecar is running the native socket transport, has connected to the
Rust audio engine, and has exchanged mic/speaker frames.
"""

from __future__ import annotations

import asyncio

import pytest

import app as app_module  # noqa: F401 — patched via app_module.* (handler reads app.<name>)
from agent.delegates import Delegate, DelegateRegistry
from server.routers.system import health


class _DummyTransport:
    connected = True
    mic_frames_received = 7
    speaker_frames_sent = 3


class _DummyLifecycle:
    def __init__(self, snapshot):
        self._snapshot = snapshot

    def snapshot(self):
        return self._snapshot

    def is_running(self):
        return bool(self._snapshot and self._snapshot["state"] == "running")


@pytest.mark.asyncio
async def test_healthz_exposes_confirmed_hub_failure(monkeypatch: pytest.MonkeyPatch):
    registry = DelegateRegistry(None)
    registry._items["hub"] = Delegate(
        name="hub",
        description="local brain",
        type="a2a",
        url="http://127.0.0.1:7870/a2a",
    )
    registry.record_health("hub", ok=False, error="unreachable")
    monkeypatch.setattr(app_module, "_DELEGATES", registry)

    payload = await health()

    hub = next(delegate for delegate in payload["delegates"] if delegate["name"] == "hub")
    assert hub["ok"] is False
    assert hub["consecutive_failures"] == 1
    assert "last_error" not in hub  # public health never leaks endpoint details


@pytest.mark.asyncio
async def test_healthz_reports_native_audio_runtime(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ORBIS_AUDIO_SOCK", "/tmp/orbis-audio-test.sock")
    monkeypatch.setattr(
        app_module,
        "audio_runtime_info",
        lambda: {"input_mode": "voice_processing", "mic_gain": 1.0},
    )
    monkeypatch.setattr(app_module, "_native_transport", _DummyTransport())
    monkeypatch.setattr(
        app_module,
        "_voice_lifecycle",
        _DummyLifecycle({"state": "running", "detail": "Voice pipeline ready"}),
    )

    async def _pipeline():
        await asyncio.sleep(60)

    task = asyncio.create_task(_pipeline())
    monkeypatch.setattr(app_module, "_native_pipeline_task", task)
    try:
        payload = await health()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    audio = payload["audio"]
    assert audio["transport"] == "native"
    assert audio["input_mode"] == "voice_processing"
    assert audio["mic_gain"] == 1.0
    assert audio["socket_configured"] is True
    assert audio["socket_connected"] is True
    assert audio["mic_frames_received"] == 7
    assert audio["speaker_frames_sent"] == 3
    assert audio["pipeline_running"] is True
    assert payload["voice"]["lifecycle"] == {
        "state": "running",
        "detail": "Voice pipeline ready",
    }


@pytest.mark.asyncio
async def test_healthz_does_not_call_connected_socket_a_running_pipeline(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ORBIS_AUDIO_SOCK", "/tmp/orbis-audio-test.sock")
    monkeypatch.setattr(app_module, "audio_runtime_info", lambda: {})
    monkeypatch.setattr(app_module, "_native_transport", _DummyTransport())
    monkeypatch.setattr(
        app_module,
        "_voice_lifecycle",
        _DummyLifecycle({"state": "starting", "detail": "Starting voice pipeline…"}),
    )

    task = asyncio.create_task(asyncio.sleep(60))
    monkeypatch.setattr(app_module, "_native_pipeline_task", task)
    try:
        payload = await health()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert payload["audio"]["socket_connected"] is True
    assert payload["audio"]["pipeline_running"] is False
    assert payload["voice"]["lifecycle"]["state"] == "starting"


@pytest.mark.asyncio
async def test_healthz_reports_native_audio_idle_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ORBIS_AUDIO_SOCK", raising=False)
    monkeypatch.setattr(
        app_module,
        "audio_runtime_info",
        lambda: {"input_mode": "cpal", "mic_gain": 16.0},
    )
    monkeypatch.setattr(app_module, "_native_transport", None)
    monkeypatch.setattr(app_module, "_native_pipeline_task", None)
    monkeypatch.setattr(app_module, "_voice_lifecycle", _DummyLifecycle(None))

    payload = await health()

    audio = payload["audio"]
    assert audio["transport"] == "native"
    assert audio["input_mode"] == "cpal"
    assert audio["mic_gain"] == 16.0
    assert audio["socket_configured"] is False
    assert audio["socket_connected"] is False
    assert audio["mic_frames_received"] == 0
    assert audio["speaker_frames_sent"] == 0
    assert audio["pipeline_running"] is False
    assert payload["voice"]["lifecycle"] is None
