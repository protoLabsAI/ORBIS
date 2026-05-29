"""Tests for the native-audio fields exposed by /healthz.

The macOS live-validation harness depends on these keys to prove that
the sidecar is running the native socket transport, has connected to the
Rust audio engine, and has exchanged mic/speaker frames.
"""

from __future__ import annotations

import asyncio

import pytest

import app as app_module


class _DummyTransport:
    connected = True
    mic_frames_received = 7
    speaker_frames_sent = 3


@pytest.mark.asyncio
async def test_healthz_reports_native_audio_runtime(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ORBIS_AUDIO_SOCK", "/tmp/orbis-audio-test.sock")
    monkeypatch.setattr(
        app_module,
        "audio_runtime_info",
        lambda: {"input_mode": "voice_processing", "mic_gain": 1.0},
    )
    monkeypatch.setattr(app_module, "_native_transport", _DummyTransport())

    async def _pipeline():
        await asyncio.sleep(60)

    task = asyncio.create_task(_pipeline())
    monkeypatch.setattr(app_module, "_native_pipeline_task", task)
    try:
        payload = await app_module.health()
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

    payload = await app_module.health()

    audio = payload["audio"]
    assert audio["transport"] == "native"
    assert audio["input_mode"] == "cpal"
    assert audio["mic_gain"] == 16.0
    assert audio["socket_configured"] is False
    assert audio["socket_connected"] is False
    assert audio["mic_frames_received"] == 0
    assert audio["speaker_frames_sent"] == 0
    assert audio["pipeline_running"] is False
