from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import app
from agent import config_store
from server.routers.voicemodels import voice_retry
from server.routers import system as system_router
from server.routers import voicemodels as voicemodels_router
from voice import lifecycle as lifecycle_module
from voice import runtime_config


def _persona(**overrides):
    values = {
        "slug": "test-persona",
        "stt": {"backend": "parakeet"},
        "tts_backend": "kokoro",
        "voice": "af_heart",
        "tts_url": None,
        "tts_model": None,
        "tts_api_key": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_native_strict_prewarm_propagates_required_local_engine_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    monkeypatch.setattr(
        config_store, "read_config", lambda: {"voice": {"local_models": "on_device"}}
    )
    monkeypatch.setattr(app, "_emit_boot", lambda stage, _detail: calls.append(stage))
    monkeypatch.setattr(
        app,
        "prewarm_stt",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("model failed")),
    )
    monkeypatch.setattr(app, "prewarm_tts", lambda **_kwargs: calls.append("tts-called"))

    with pytest.raises(RuntimeError, match="model failed"):
        app.prewarm_all(strict_local=True, persona=_persona(), include_llm=False)

    assert calls == ["stt"]


def test_direct_prewarm_remains_best_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(
        config_store, "read_config", lambda: {"voice": {"local_models": "on_device"}}
    )
    monkeypatch.setattr(app, "_emit_boot", lambda stage, _detail: calls.append(stage))
    monkeypatch.setattr(
        app,
        "prewarm_stt",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("model failed")),
    )
    monkeypatch.setattr(app, "prewarm_tts", lambda **_kwargs: calls.append("tts-called"))
    monkeypatch.setattr(app, "prewarm_llm", lambda: calls.append("llm-called"))

    app.prewarm_all(persona=_persona())

    assert calls == ["stt", "tts", "tts-called", "llm", "llm-called", "ready"]


def test_prewarm_uses_exact_persona_backends_and_skips_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warmed: list[tuple[str, dict]] = []
    persona = _persona(
        stt={"backend": "sensevoice", "model": "persona-stt"},
        tts_backend="openai",
        voice="persona-voice",
        tts_url="https://tts.example/v1",
        tts_model="persona-tts",
        tts_api_key="persona-key",
    )
    monkeypatch.setattr(runtime_config, "STT_BACKEND", "parakeet")
    monkeypatch.setattr(runtime_config, "TTS_BACKEND", "kokoro")
    monkeypatch.setattr(
        config_store, "read_config", lambda: {"voice": {"local_models": "on_device"}}
    )
    monkeypatch.setattr(app, "_emit_boot", lambda *_args: None)
    monkeypatch.setattr(
        app, "prewarm_stt", lambda **kwargs: warmed.append(("stt", kwargs))
    )
    monkeypatch.setattr(
        app, "prewarm_tts", lambda **kwargs: warmed.append(("tts", kwargs))
    )
    monkeypatch.setattr(
        app,
        "prewarm_llm",
        lambda: (_ for _ in ()).throw(AssertionError("LLM warm delayed voice")),
    )

    app.prewarm_all(persona=persona, strict_local=True, include_llm=False)

    assert warmed == [
        ("stt", {"backend": "sensevoice", "model": "persona-stt"}),
        (
            "tts",
            {
                "backend": "openai",
                "voice": "persona-voice",
                "url": "https://tts.example/v1",
                "model": "persona-tts",
                "api_key": "persona-key",
            },
        ),
    ]


@pytest.mark.asyncio
async def test_explicit_retry_is_idempotent_while_owner_is_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = asyncio.Event()
    calls = 0

    async def fake_owner(**_kwargs) -> None:
        nonlocal calls
        calls += 1
        await gate.wait()

    monkeypatch.setattr(lifecycle_module, "run_native_voice_lifecycle", fake_owner)
    monkeypatch.setattr(app, "_native_lifecycle_task", None)

    assert await app.start_native_voice_lifecycle("/tmp/orbis.sock") is True
    assert await app.start_native_voice_lifecycle("/tmp/orbis.sock") is False
    assert calls == 1

    gate.set()
    await app._native_lifecycle_task
    assert await app.start_native_voice_lifecycle("/tmp/orbis.sock") is True
    await app._native_lifecycle_task
    assert calls == 2


@pytest.mark.asyncio
async def test_retry_endpoint_rejects_direct_no_socket_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ORBIS_AUDIO_SOCK", raising=False)

    response = await voice_retry(user=None)

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_retry_requires_relaunch_after_socket_was_consumed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORBIS_AUDIO_SOCK", "/tmp/orbis.sock")
    await app._voice_lifecycle.transition(
        "failed",
        "Voice pipeline failed to start",
        code="pipeline_start_failed",
        action="relaunch_required",
    )
    try:
        response = await voice_retry(user=None)
        assert response.status_code == 409
        assert b'"code":"relaunch_required"' in response.body
    finally:
        app._voice_lifecycle.reset()


@pytest.mark.asyncio
async def test_health_backend_names_follow_effective_persona(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persona = _persona(stt={"backend": "openai"}, tts_backend="elevenlabs")
    monkeypatch.setattr(system_router, "get_active_persona", lambda: persona)

    payload = await system_router.health()

    assert payload["stt_backend"] == "openai"
    assert payload["tts_backend"] == "elevenlabs"


@pytest.mark.asyncio
async def test_model_download_selection_follows_effective_persona(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Process defaults are local + Kokoro, but this persona needs no downloads.
    persona = _persona(stt={"backend": "openai"}, tts_backend="elevenlabs")
    monkeypatch.setattr(voicemodels_router, "get_active_persona", lambda: persona)

    response = await voicemodels_router.voice_download_models()
    chunks = [chunk async for chunk in response.body_iterator]

    assert "no on-device models needed" in "".join(chunks)
