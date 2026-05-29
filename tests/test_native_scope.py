"""Native-fork guardrails that protect the Tauri desktop baseline."""

from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_native_desktop_scaffold_stays_present():
    """Upstream ORBIS may delete these; orbis-native must keep them."""
    required_paths = (
        ".github/workflows/desktop-build.yml",
        ".github/workflows/docker-publish.yml",
        ".github/workflows/native-audio-preflight.yml",
        ".github/workflows/prepare-release.yml",
        ".github/workflows/release.yml",
        "docs/build-desktop-binary.md",
        "docs/desktop-dev.md",
        "docs/desktop-signing.md",
        "docs/native-audio-direction.md",
        "docs/native-audio-transport.md",
        "scripts/build-desktop-binary.sh",
        "src-tauri/Cargo.lock",
        "src-tauri/Cargo.toml",
        "src-tauri/Info.plist",
        "src-tauri/build.rs",
        "src-tauri/capabilities/default.json",
        "src-tauri/entitlements.plist",
        "src-tauri/icons/icon.png",
        "src-tauri/src/audio/aec.rs",
        "src-tauri/src/audio/engine.rs",
        "src-tauri/src/audio/mod.rs",
        "src-tauri/src/audio/socket.rs",
        "src-tauri/src/audio/voice_processing_input.rs",
        "src-tauri/src/lib.rs",
        "src-tauri/src/main.rs",
        "src-tauri/src/mic_permission.m",
        "src-tauri/tauri.conf.json",
        "scripts/check-macos-release-config.py",
        "scripts/nuke-and-rebuild.sh",
        "scripts/preflight-native-audio-host.sh",
        "scripts/validate-macos-native-audio.sh",
        "tests/test_healthz_native_audio.py",
        "tests/test_local_transport.py",
        "tests/test_native_bargein.py",
        "tests/test_prosody.py",
        "tests/test_sse_bus.py",
        "voice/local_transport.py",
        "voice/native_bargein.py",
        "voice/sse_bus.py",
    )

    missing = [path for path in required_paths if not (ROOT / path).exists()]

    assert missing == []


def test_release_workflows_remain_ready_for_upstream_overwrite():
    """This fork stages the future protoLabsAI/ORBIS overwrite, not a separate product."""
    gated_workflows = (
        ".github/workflows/desktop-build.yml",
        ".github/workflows/docker-publish.yml",
        ".github/workflows/prepare-release.yml",
        ".github/workflows/release.yml",
    )

    for path in gated_workflows:
        workflow = (ROOT / path).read_text(encoding="utf-8")
        assert "github.repository == 'protoLabsAI/ORBIS'" in workflow
        assert "protoLabsAI/orbis-native" not in workflow


def test_native_workflow_guardrails_stay_present():
    check_script = (ROOT / "scripts/check-macos-release-config.py").read_text(encoding="utf-8")
    required = (
        ".github",
        "desktop-build.yml",
        "native-audio-preflight.yml",
        "scripts/validate-macos-native-audio.sh --release",
        "cargo tauri build --features native-audio,voice-processing",
        "cargo test --manifest-path src-tauri/Cargo.toml --features native-audio,voice-processing",
        "runs-on: macos-14",
        "orbis-aarch64-apple-darwin",
    )

    for needle in required:
        assert needle in check_script


def test_default_ci_workflows_stay_native_fork_scoped():
    """Default PR checks should validate native guardrails, not hosted-app plumbing."""
    pytest_workflow = (ROOT / ".github/workflows/pytest.yml").read_text(encoding="utf-8")
    web_workflow = (ROOT / ".github/workflows/web-build.yml").read_text(encoding="utf-8")

    for workflow in (pytest_workflow, web_workflow):
        assert "pull_request:" in workflow
        assert "branches: [main]" in workflow
        assert "protoLabsAI/orbis-native" not in workflow
        assert "codegen" not in workflow
        assert "openapi" not in workflow

    assert 'pip install -e ".[test]"' in pytest_workflow
    assert "pytest -q" in pytest_workflow
    assert "PWA/WebRTC" in pytest_workflow

    assert "working-directory: web" in web_workflow
    assert "bun install --frozen-lockfile" in web_workflow
    assert "bun run build" in web_workflow
    assert "Tauri UI build" in web_workflow
    assert "hosted PWA/browser runtime gate" in web_workflow


def test_backend_dependencies_stay_native_first():
    pyproject = _pyproject()
    dependencies = pyproject["project"]["dependencies"]
    joined_dependencies = "\n".join(dependencies)

    assert "transformers>=4.46" in dependencies
    assert "accelerate" in dependencies
    assert "kokoro>=0.9" in dependencies
    assert "mlx-lm>=0.20; sys_platform == 'darwin' and platform_machine == 'arm64'" in dependencies

    forbidden = (
        "pipecat-ai[webrtc",
        "small-webrtc",
    )
    for needle in forbidden:
        assert needle not in joined_dependencies


def test_split_deployment_pairing_backend_stays_absent():
    """Hosted-SPA pairing belongs to upstream web mode, not native ORBIS."""
    assert not (ROOT / "auth/pairing.py").exists()

    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    forbidden = (
        "CORSMiddleware",
        "auth.pairing",
        "get_pairing_token",
        "is_pairing_enforced",
        "ORBIS_ALLOWED_ORIGINS",
        "ORBIS_PAIR_TOKEN",
        "X-Orbis-Pair",
        "x-orbis-pair",
        "split-deployment pairing token",
    )

    for needle in forbidden:
        assert needle not in app_source


def test_env_example_documents_native_runtime_scope():
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

    required = (
        "# ORBIS local secrets + config.",
        "#AGENT_NAME=orbis",
        "#AUDIO_TRANSPORT=native",
        "#ORBIS_AUDIO_SOCK=/tmp/orbis-audio-{pid}.sock",
        "#ORBIS_AUDIO_INPUT_MODE=voice_processing",
        "#ORBIS_GATE=open",
        "#INBOX_INGEST_TOKEN=",
        "Session = native voice session",
    )
    for needle in required:
        assert needle in env_example

    forbidden = (
        "protoVoice local secrets",
        "#AGENT_NAME=protovoice",
        "ORBIS_ALLOWED_ORIGINS",
        "ORBIS_PAIR_TOKEN",
        "Session = WebRTC session",
    )
    for needle in forbidden:
        assert needle not in env_example


def test_readme_documents_native_runtime_scope():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    required = (
        "single-owner native desktop app",
        "macOS Tauri shell",
        "Not a PWA or browser voice app",
        "native PCM socket",
        "scripts/preflight-native-audio-host.sh",
        "scripts/nuke-and-rebuild.sh --launch --tail",
    )
    for needle in required:
        assert needle in readme

    forbidden = (
        "single-owner WebRTC PWA",
        "via WebRTC + Pipecat",
        "Split deployment",
        "ORBIS_ALLOWED_ORIGINS",
        "X-Orbis-Pair",
        "Document Picture-in-Picture",
    )
    for needle in forbidden:
        assert needle not in readme
