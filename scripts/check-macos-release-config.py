#!/usr/bin/env python3
"""Static guardrails for ORBIS's production macOS native-audio build.

This script is intentionally host-portable: it checks source files and
workflow config without requiring macOS signing tools. The live app,
Gatekeeper, notarization, and microphone callback checks stay in
scripts/validate-macos-native-audio.sh.
"""

from __future__ import annotations

import json
import os
import plistlib
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TAURI = ROOT / "src-tauri"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def require_contains(path: Path, needle: str, description: str) -> None:
    text = read(path)
    require(needle in text, f"{description}: missing {needle!r} in {path}")


def require_absent(path: Path, needle: str, description: str) -> None:
    text = read(path)
    require(needle not in text, f"{description}: unexpected {needle!r} in {path}")


def check_tauri_config() -> None:
    conf = json.loads(read(TAURI / "tauri.conf.json"))
    bundle = conf["bundle"]
    macos = bundle["macOS"]

    require(conf["identifier"] == "studio.protolabs.orbis", "stable bundle id changed")
    require(bundle["targets"] == ["dmg"], "production bundle target must be DMG only")
    require(bundle["externalBin"] == ["binaries/orbis"], "sidecar externalBin changed")
    require(
        bundle["resources"] == {
            "../config/orbis.example.yaml": "config/orbis.example.yaml",
            "../config/persona.md": "config/persona.md",
            "../config/starter_orbs.yaml": "config/starter_orbs.yaml",
        },
        "first-run config resources must stay bundled",
    )
    require(macos["hardenedRuntime"] is True, "hardened runtime must be enabled")
    require(macos["infoPlist"] == "./Info.plist", "explicit Info.plist must be used")
    require(macos["entitlements"] == "./entitlements.plist", "explicit entitlements must be used")
    require(macos["minimumSystemVersion"] == "13.0", "minimum macOS version must stay 13.0")


def check_tauri_capabilities() -> None:
    capability = json.loads(read(TAURI / "capabilities" / "default.json"))
    generated = json.loads(read(TAURI / "gen" / "schemas" / "capabilities.json"))
    permissions = capability["permissions"]

    require(capability["windows"] == ["main"], "default capability must target only the main window")
    require(capability["local"] is True, "default capability must allow local splash content")
    require(
        capability.get("remote", {}).get("urls") == [
            "http://127.0.0.1:*/*",
            "http://localhost:*/*",
        ],
        "default capability must allow only localhost sidecar origins",
    )
    for required in ["core:default", "dialog:allow-message", "log:default", "http:default"]:
        require(required in permissions, f"default capability missing {required}")

    http_scopes = [
        p for p in permissions if isinstance(p, dict) and p.get("identifier") == "http:default"
    ]
    require(len(http_scopes) == 1, "default capability must have one scoped http permission")
    require(
        http_scopes[0].get("allow") == [
            {"url": "http://127.0.0.1:*/*"},
            {"url": "http://localhost:*/*"},
        ],
        "http permission must stay scoped to localhost sidecar origins",
    )

    shell_scopes = [
        p
        for p in permissions
        if isinstance(p, dict) and p.get("identifier") == "shell:allow-execute"
    ]
    require(len(shell_scopes) == 1, "default capability must have one scoped sidecar execute permission")
    require(
        shell_scopes[0].get("allow") == [
            {
                "name": "binaries/orbis",
                "sidecar": True,
                "args": ["--host", "127.0.0.1", "--port", "0"],
            }
        ],
        "sidecar execute permission must stay pinned to the bundled orbis sidecar args",
    )
    require("dialog:default" not in permissions, "default capability must not allow dialog:default")
    require("dialog:allow-open" not in permissions, "default capability must not allow file-open dialogs")
    require("dialog:allow-save" not in permissions, "default capability must not allow file-save dialogs")
    require("shell:default" not in permissions, "default capability must not allow shell:default")
    require("shell:allow-open" not in permissions, "default capability must not allow broad shell open")

    normalized_capability = {k: v for k, v in capability.items() if k != "$schema"}
    require(
        generated.get("default") == normalized_capability,
        "generated Tauri capabilities must match src-tauri/capabilities/default.json",
    )


def check_plists() -> None:
    info = plistlib.loads((TAURI / "Info.plist").read_bytes())
    entitlements = plistlib.loads((TAURI / "entitlements.plist").read_bytes())

    require("NSMicrophoneUsageDescription" in info, "Info.plist missing microphone usage text")
    require("NSCameraUsageDescription" not in info, "Info.plist must not request camera")

    require(
        entitlements.get("com.apple.security.device.audio-input") is True,
        "entitlements missing microphone input",
    )
    require(
        "com.apple.security.device.camera" not in entitlements,
        "entitlements must not include camera",
    )
    require(
        entitlements.get("com.apple.security.network.client") is True,
        "entitlements missing network client",
    )
    require(
        entitlements.get("com.apple.security.network.server") is True,
        "entitlements missing network server",
    )


def check_native_audio_sources() -> None:
    lib_rs = TAURI / "src" / "lib.rs"
    engine_rs = TAURI / "src" / "audio" / "engine.rs"
    vp_rs = TAURI / "src" / "audio" / "voice_processing_input.rs"
    mic_shim = TAURI / "src" / "mic_permission.m"
    transport = ROOT / "voice" / "local_transport.py"

    require_contains(
        lib_rs,
        'command = command.env("ORBIS_AUDIO_INPUT_MODE", "voice_processing");',
        "Rust sidecar handoff must identify the production input mode",
    )
    require_contains(
        lib_rs,
        "ensure_microphone_permission()",
        "Rust startup must gate native audio on microphone permission",
    )
    require_contains(
        lib_rs,
        'resolve("config/persona.md", BaseDirectory::Resource)',
        "Rust first-run seed must copy the bundled persona prompt next to orbis.yaml",
    )
    require_contains(
        lib_rs,
        "get_microphone_permission_status",
        "frontend needs microphone permission status IPC",
    )
    require_contains(
        engine_rs,
        "VoiceProcessingInput::new",
        "AudioEngine must start AVAudioEngine voice-processing input",
    )
    require_contains(
        TAURI / "src" / "audio" / "socket.rs",
        "[audio/socket] first playback frame received",
        "Rust audio socket must log first playback frame receipt",
    )
    require_contains(
        vp_rs,
        "setVoiceProcessingEnabled_error(true)",
        "voice-processing input must enable Apple's voice-processing IO",
    )
    require_contains(
        vp_rs,
        "[voice-processing] input became audible",
        "voice-processing input must log non-silent audio for live validation",
    )
    require_contains(
        transport,
        'return 1.0 if audio_input_mode == "voice_processing" else 16.0',
        "Python sidecar must use unity mic gain for voice-processing mode",
    )
    require_contains(
        transport,
        "def audio_runtime_info()",
        "Python sidecar must expose audio runtime config for health checks",
    )
    require_contains(
        transport,
        "def connected(self) -> bool:",
        "Python native transport must expose its current socket connection state",
    )
    require_contains(
        transport,
        "def mic_frames_received(self) -> int:",
        "Python native transport must expose received mic frame count",
    )
    require_contains(
        transport,
        "def speaker_frames_sent(self) -> int:",
        "Python native transport must expose sent speaker frame count",
    )
    require_contains(
        transport,
        "[local_transport] first mic frame",
        "Python native transport must log first mic frame receipt",
    )
    require_contains(
        transport,
        "[local_transport] first speaker frame",
        "Python native transport must log first speaker frame send",
    )
    require_contains(
        ROOT / "app.py",
        "**audio_runtime_info()",
        "healthz must expose the sidecar audio input mode and mic gain",
    )
    require_contains(
        ROOT / "app.py",
        '"socket_configured": bool(os.environ.get("ORBIS_AUDIO_SOCK"))',
        "healthz must expose whether the native audio socket was configured",
    )
    require_contains(
        ROOT / "app.py",
        '"socket_connected": bool(_native_transport and _native_transport.connected)',
        "healthz must expose whether Python is connected to the native audio socket",
    )
    require_contains(
        ROOT / "app.py",
        '"pipeline_running": bool(',
        "healthz must expose whether the native voice pipeline task is still running",
    )
    require_contains(
        ROOT / "app.py",
        '"mic_frames_received": (',
        "healthz must expose the Python-side received mic frame count",
    )
    require_contains(
        ROOT / "app.py",
        '"speaker_frames_sent": (',
        "healthz must expose the Python-side sent speaker frame count",
    )
    require_contains(
        ROOT / "tests" / "test_healthz_native_audio.py",
        "test_healthz_reports_native_audio_runtime",
        "healthz native-audio runtime fields must have focused test coverage",
    )
    require_contains(
        ROOT / "tests" / "test_healthz_native_audio.py",
        "test_healthz_reports_native_audio_idle_state",
        "healthz native-audio idle fields must have focused test coverage",
    )
    require_contains(
        ROOT / "app.py",
        "_native_pipeline_task = None\n    _native_transport = None",
        "lifespan startup must reset native audio runtime state",
    )

    require_contains(
        mic_shim,
        "AVMediaTypeAudio",
        "macOS TCC shim must request audio permission",
    )
    require_absent(
        mic_shim,
        "AVMediaTypeVideo",
        "macOS TCC shim must not request camera permission",
    )
    require_absent(
        ROOT / "pyproject.toml",
        "pipecat-ai[webrtc",
        "desktop sidecar dependencies must not reinstall Pipecat WebRTC extras",
    )


def check_frontend_sources() -> None:
    web_src = ROOT / "web" / "src"
    get_user_media = []
    camera_permission = []
    for path in web_src.rglob("*"):
        if not path.is_file() or path.suffix not in {".ts", ".tsx", ".js", ".jsx"}:
            continue
        text = read(path)
        if "getUserMedia" in text:
            get_user_media.append(path.relative_to(ROOT))
        if "camera" in text.lower() and "microphone" in text.lower():
            camera_permission.append(path.relative_to(ROOT))

    require(not get_user_media, f"browser getUserMedia path must stay removed: {get_user_media}")
    require(
        not camera_permission,
        f"frontend must not couple camera and microphone permission flows: {camera_permission}",
    )
    require_contains(
        web_src / "shared" / "audio" / "nativeAudio.ts",
        "voice_processing",
        "frontend must understand production audio input mode",
    )
    require_contains(
        web_src / "plugins" / "setup-wizard" / "SetupWizard.tsx",
        "getMicrophonePermissionStatus",
        "setup wizard must use native microphone permission IPC",
    )
    require_contains(
        web_src / "plugins" / "settings-panel" / "MicSettings.tsx",
        "getAudioInputMode",
        "settings panel must hide CPAL-only controls for voice-processing mode",
    )


def check_workflow() -> None:
    workflow = ROOT / ".github" / "workflows" / "desktop-build.yml"
    preflight = ROOT / ".github" / "workflows" / "native-audio-preflight.yml"
    live_validation = ROOT / "scripts" / "validate-macos-native-audio.sh"
    rebuild = ROOT / "scripts" / "nuke-and-rebuild.sh"
    text = read(workflow)
    preflight_text = read(preflight)

    require_contains(workflow, "macos-14", "desktop workflow must build on Apple Silicon runner")
    require_contains(
        workflow,
        "target: aarch64-apple-darwin",
        "desktop workflow must target macOS arm64",
    )
    require_contains(
        workflow,
        "cargo tauri build --features native-audio,voice-processing",
        "desktop workflow must build production native audio features",
    )
    require_contains(
        workflow,
        "scripts/check-macos-release-config.py",
        "desktop workflow must run this static guardrail script",
    )
    require_contains(
        workflow,
        "scripts/validate-macos-native-audio.sh --release",
        "tag releases must run release validation harness",
    )
    require_contains(
        workflow,
        'scripts/validate-macos-native-audio.sh --dmg "${installer:?installer env missing}"',
        "manual desktop builds must still validate DMG payload contents",
    )
    require_contains(
        workflow,
        "seq 1 180",
        "desktop release upload must wait for the parallel release workflow",
    )
    require_contains(
        workflow,
        "Expected exactly one DMG installer",
        "desktop workflow must fail on ambiguous DMG installer output",
    )
    require_contains(
        workflow,
        "DMG upload would be missing from the GitHub release",
        "desktop release upload must fail instead of silently dropping the DMG",
    )
    require_contains(
        workflow,
        "base64.b64decode",
        "desktop workflow must decode the App Store Connect key portably on macOS",
    )
    require_contains(
        workflow,
        "python3 -c",
        "desktop workflow must avoid heredoc indentation hazards in the signing step",
    )
    require_contains(
        workflow,
        "PYAPP_EXEC_SPEC: 'app:main'",
        "desktop workflow must build a PyApp sidecar that launches app:main",
    )
    require_contains(
        workflow,
        "PYAPP_FULL_ISOLATION: '1'",
        "desktop workflow must keep the sidecar isolated from user Python installs",
    )
    require_contains(
        workflow,
        "ORBIS_ALLOW_CPU: '1'",
        "desktop workflow smoke test must boot on GPU-less CI runners",
    )
    require_contains(
        workflow,
        "ORBIS_READY http://",
        "desktop workflow smoke test must wait for the sidecar readiness marker",
    )
    require_contains(
        workflow,
        "src-tauri/binaries/${{ env.artifact_name }}",
        "desktop workflow must stage the PyApp sidecar where Tauri externalBin expects it",
    )
    require_contains(
        preflight,
        "scripts/check-macos-release-config.py",
        "preflight workflow must run static macOS release guardrails",
    )
    require_contains(
        preflight,
        "bun run build",
        "preflight workflow must build the web app",
    )
    require_contains(
        preflight,
        "cargo fmt --manifest-path src-tauri/Cargo.toml --check",
        "preflight workflow must check Rust formatting",
    )
    require_contains(
        preflight,
        "python -m pytest",
        "preflight workflow must run focused Python tests",
    )
    require_contains(
        preflight,
        "tests/test_local_transport.py",
        "preflight workflow must run native socket transport tests",
    )
    require_contains(
        preflight,
        "tests/test_healthz_native_audio.py",
        "preflight workflow must run native-audio healthz runtime tests",
    )
    require_contains(
        preflight,
        "pipecat-ai>=1.0",
        "preflight workflow must install the minimal Pipecat dependency for transport tests",
    )
    require_contains(
        preflight,
        "cargo test --manifest-path src-tauri/Cargo.toml --features native-audio,voice-processing",
        "preflight workflow must test the native-audio voice-processing feature set",
    )
    require_contains(
        preflight,
        "runs-on: macos-14",
        "preflight workflow must compile the macOS-only native audio path",
    )
    require_contains(
        preflight,
        "orbis-aarch64-apple-darwin",
        "macOS preflight must stage the arm64 sidecar name Tauri expects",
    )
    require_contains(
        live_validation,
        "--release requires --dmg",
        "release validation must require a DMG path",
    )
    require_contains(
        live_validation,
        "DURATION=240",
        "live validation default must match the documented 240-second soak",
    )
    require_contains(
        live_validation,
        'ARCHS="$(lipo -archs "$EXECUTABLE"',
        "live validation must verify the app executable architecture",
    )
    require_contains(
        live_validation,
        'MAIN_EXECUTABLE="$EXECUTABLE"',
        "live validation must remember the executable discovered from Info.plist",
    )
    require_contains(
        live_validation,
        'RUST_LOG=info "$MAIN_EXECUTABLE"',
        "live validation must launch the verified bundle executable",
    )
    require_contains(
        live_validation,
        "launched app exited before live validation completed",
        "live validation must fail fast if the launched app exits early",
    )
    require_contains(
        live_validation,
        "orbis-aarch64-apple-darwin",
        "live validation must verify the bundled arm64 PyApp sidecar",
    )
    require_contains(
        live_validation,
        "did not observe non-silent AVAudioEngine input",
        "live validation must prove the microphone callback produced audible input",
    )
    require_contains(
        live_validation,
        "[local_transport] connected",
        "live validation must prove Python connected to the native audio socket",
    )
    require_contains(
        live_validation,
        "did not observe Python local audio transport socket connection",
        "live validation must fail if Python never connects to the native audio socket",
    )
    require_contains(
        live_validation,
        "did not observe Python local audio transport receiving mic frames",
        "live validation must fail if Python never receives mic frames",
    )
    require_contains(
        live_validation,
        "did not observe Python local audio transport sending speaker frames",
        "live validation must fail if Python never sends speaker frames",
    )
    require_contains(
        live_validation,
        "did not observe Rust audio socket receiving playback frames",
        "live validation must fail if Rust never receives playback frames",
    )
    require_contains(
        live_validation,
        'SIDECAR_ARCHS="$(lipo -archs "$SIDECAR"',
        "live validation must verify bundled sidecar architecture",
    )
    require_contains(
        workflow,
        "sidecar_archs",
        "desktop workflow must verify bundled sidecar architecture",
    )
    require_contains(
        workflow,
        'assert "com.apple.security.cs.allow-unsigned-executable-memory" not in entitlements',
        "desktop workflow must reject unsigned executable-memory entitlement",
    )
    require_contains(
        workflow,
        'assert "com.apple.security.cs.disable-library-validation" not in entitlements',
        "desktop workflow must reject disabled library-validation entitlement",
    )
    require_contains(
        workflow,
        'resources / "config" / "orbis.example.yaml"',
        "desktop workflow must verify bundled first-run example config",
    )
    require_contains(
        workflow,
        'resources / "config" / "persona.md"',
        "desktop workflow must verify bundled first-run persona prompt",
    )
    require_contains(
        workflow,
        'resources / "config" / "starter_orbs.yaml"',
        "desktop workflow must verify bundled starter-orb config",
    )
    require_contains(
        live_validation,
        "/healthz",
        "live validation must verify the ready backend health endpoint",
    )
    require_contains(
        live_validation,
        'payload.get("audio", {}).get("transport") != "native"',
        "live validation must parse healthz JSON and assert audio.transport exactly",
    )
    require_contains(
        live_validation,
        'payload.get("audio", {}).get("input_mode") != "voice_processing"',
        "live validation must assert the sidecar reports voice-processing input mode",
    )
    require_contains(
        live_validation,
        "audio.mic_gain=1.0",
        "live validation must assert the sidecar reports unity mic gain",
    )
    require_contains(
        live_validation,
        'payload.get("audio", {}).get("socket_configured") is not True',
        "live validation must assert the sidecar reports a configured audio socket",
    )
    require_contains(
        live_validation,
        'payload.get("audio", {}).get("socket_connected") is not True',
        "live validation must assert the sidecar reports an active audio socket connection",
    )
    require_contains(
        live_validation,
        'payload.get("audio", {}).get("pipeline_running") is not True',
        "live validation must assert the sidecar reports the native voice pipeline running",
    )
    require_contains(
        live_validation,
        "mic_frames_received > 0",
        "live validation must assert the sidecar reports received mic frames",
    )
    require_contains(
        live_validation,
        "speaker_frames_sent > 0",
        "live validation must assert the sidecar reports sent speaker frames",
    )
    require_contains(
        live_validation,
        "require_cmd python3",
        "live validation must declare python3 because healthz JSON parsing uses it",
    )
    require_contains(
        live_validation,
        "require_cmd hdiutil",
        "release validation must declare hdiutil because DMG contents are mounted",
    )
    require_contains(
        live_validation,
        'hdiutil attach "$DMG"',
        "release validation must mount the DMG to inspect its contents",
    )
    require_contains(
        live_validation,
        "using app mounted from DMG for validation",
        "release validation must support validating downloaded DMGs without a separate built app",
    )
    require_contains(
        live_validation,
        "DMG contains ORBIS.app",
        "release validation must prove the DMG contains the expected app",
    )
    require_contains(
        live_validation,
        "DMG app sidecar is arm64",
        "release validation must prove the app inside the DMG contains the arm64 sidecar",
    )
    require_contains(
        live_validation,
        "bundled resource exists: $resource",
        "live validation must verify first-run config resources in the built app",
    )
    require_contains(
        live_validation,
        "DMG app resource exists: $resource",
        "release validation must verify first-run config resources inside the DMG app",
    )
    require_contains(
        live_validation,
        "entitlement_is_true",
        "release validation must parse signed entitlements structurally",
    )
    require_contains(
        live_validation,
        "entitlement_is_absent",
        "release validation must parse forbidden entitlements structurally",
    )
    require_contains(
        live_validation,
        "signed entitlements include true network client",
        "release validation must verify signed network-client entitlement",
    )
    require_contains(
        live_validation,
        "signed entitlements include true network server",
        "release validation must verify signed network-server entitlement",
    )
    require_contains(
        live_validation,
        "signed entitlements include true audio input",
        "release validation must verify signed audio-input entitlement",
    )
    require_contains(
        live_validation,
        "signed entitlements do not allow unsigned executable memory",
        "release validation must reject broad unsigned executable-memory entitlement",
    )
    require_contains(
        live_validation,
        "signed entitlements keep library validation enabled",
        "release validation must reject disabled library validation",
    )
    require_contains(
        live_validation,
        'validate_release_app_signing "$DMG_APP" "DMG app"',
        "release validation must verify signing/notarization on the app inside the DMG",
    )
    require_contains(
        live_validation,
        'validate_release_app_signing "$REQUESTED_APP" "build-tree app"',
        "release validation must check the original build-tree app when present",
    )
    require_contains(
        live_validation,
        "build-tree app is absent; release validation will use the mounted DMG app",
        "release validation must not mislabel a downloaded DMG app as the build-tree app",
    )
    require_contains(
        live_validation,
        "$label Gatekeeper execute assessment passed",
        "release validation must run Gatekeeper execute assessment for every checked app",
    )
    require_contains(
        live_validation,
        "$label has a valid stapled notarization ticket",
        "release validation must verify stapled notarization for every checked app",
    )
    require_contains(
        live_validation,
        ': > "$SIDECAR_LOG"',
        "live validation must truncate sidecar logs before launch",
    )
    require_contains(
        live_validation,
        ': > "$ORBIS_LOG"',
        "live validation must truncate app logs before launch",
    )
    require_contains(
        live_validation,
        "dump_log_tail",
        "live validation must embed bounded log tails in failing reports",
    )
    require_contains(
        live_validation,
        'tail -n 120 "$file"',
        "live validation log tails must stay bounded",
    )
    require_contains(
        live_validation,
        "healthz reports audio.transport=native",
        "live validation must prove the backend reports native audio transport",
    )
    require_contains(
        live_validation,
        'log "FAIL: macOS native-audio validation failed with $failures issue(s)"',
        "live validation final failure summary must not increment the failure count",
    )
    require_absent(
        live_validation,
        'fail "macOS native-audio validation failed with $failures issue(s)"',
        "live validation final failure summary must not be counted as another validation failure",
    )
    require_contains(
        rebuild,
        "hdiutil create",
        "local --dmg rebuild must create the DMG after stable signing",
    )
    require_contains(
        rebuild,
        'cp -R "${APP}" "${DMG_STAGE}/ORBIS.app"',
        "local --dmg rebuild must stage ORBIS.app at the DMG volume root",
    )
    require_contains(
        rebuild,
        '-srcfolder "${DMG_STAGE}"',
        "local --dmg rebuild must package the staging directory, not the app bundle contents",
    )
    require_contains(
        rebuild,
        "from the already-signed .app",
        "local --dmg rebuild must document signed-app packaging order",
    )
    require_contains(
        rebuild,
        "plutil -extract CFBundleExecutable raw",
        "local rebuild launch path must resolve the executable from the built app Info.plist",
    )
    require_contains(
        rebuild,
        'RUST_LOG=info "${EXECUTABLE}"',
        "local rebuild --launch must start the executable discovered from Info.plist",
    )
    require("x86_64-pc-windows" not in text, "Windows matrix must stay deferred")
    require("x86_64-unknown-linux" not in text, "Linux matrix must stay deferred")
    require(
        "pull_request:" in preflight_text and "push:" in preflight_text,
        "preflight workflow must run before release tags",
    )


def check_scripts_executable() -> None:
    scripts = [
        ROOT / "scripts" / "check-macos-release-config.py",
        ROOT / "scripts" / "preflight-native-audio-host.sh",
        ROOT / "scripts" / "validate-macos-native-audio.sh",
        ROOT / "scripts" / "nuke-and-rebuild.sh",
    ]
    for script in scripts:
        require(script.is_file(), f"required script is missing: {script}")
        require(os.access(script, os.X_OK), f"required script is not executable: {script}")

    host_preflight = ROOT / "scripts" / "preflight-native-audio-host.sh"
    require_contains(
        host_preflight,
        'bash -n "$0" scripts/validate-macos-native-audio.sh scripts/nuke-and-rebuild.sh',
        "host native-audio preflight must syntax-check itself and the Mac helper scripts",
    )
    require_contains(
        host_preflight,
        "require_cmd bun",
        "host native-audio preflight must fail clearly if Bun is missing",
    )
    require_contains(
        host_preflight,
        "bun install --frozen-lockfile",
        "host native-audio preflight must install frontend deps with the lockfile",
    )
    require_contains(
        host_preflight,
        "bun run build",
        "host native-audio preflight must build the web app consumed by Tauri",
    )
    require_contains(
        host_preflight,
        "cargo test --manifest-path src-tauri/Cargo.toml --features native-audio,voice-processing",
        "host native-audio preflight must run the production feature Rust tests",
    )
    require_contains(
        host_preflight,
        "tests/test_healthz_native_audio.py",
        "host native-audio preflight must run native-audio healthz runtime tests",
    )
    require_contains(
        host_preflight,
        "-type d -name __pycache__ -exec rm -rf {} +",
        "host native-audio preflight must clean generated Python caches on exit",
    )


def check_docs() -> None:
    stale_claims = [
        (
            ROOT / "DECISIONS.md",
            "only first-class platform",
            "DECISIONS must describe Mac-first scope, not Apple-Silicon-only scope",
        ),
        (
            ROOT / "DECISIONS.md",
            "No Linux/Windows desktop product",
            "DECISIONS must defer Linux/Windows desktop, not reject it as a product",
        ),
        (
            ROOT / "DECISIONS.md",
            "omitting it produces a WebRTC-only desktop build",
            "DECISIONS must not describe WebRTC-only desktop builds as current",
        ),
        (
            ROOT / "docs" / "native-audio-direction.md",
            "only first-class platform",
            "native audio direction must describe Mac-first scope",
        ),
        (
            ROOT / "docs" / "native-audio-transport.md",
            "## Feature Flags",
            "historical transport docs must not present obsolete flags as current",
        ),
        (
            ROOT / "docs" / "desktop-dev.md",
            "ORBIS_DEV_URL",
            "desktop dev docs must not advertise unimplemented ORBIS_DEV_URL flow",
        ),
        (
            ROOT / "HANDOFF.md",
            "**Mac desktop build (signed + notarized .dmg)** installs",
            "HANDOFF must not claim current signed DMG live validation is complete",
        ),
        (
            ROOT / "HANDOFF.md",
            "ORBIS_0.1.44_aarch64.dmg",
            "HANDOFF must not hard-code versioned local DMG artifact names",
        ),
        (
            ROOT / "scripts" / "validate-macos-native-audio.sh",
            "ORBIS-0.1.44-aarch64-apple-darwin.dmg",
            "validation script usage must not hard-code versioned release artifact names",
        ),
        (
            ROOT / "docs" / "voice-lifecycle.md",
            "install UIDelegate patch",
            "voice lifecycle docs must not point current Mac path at the WebView permission patch",
        ),
        (
            ROOT / "docs" / "voice-lifecycle.md",
            "`audio-input`, `camera`",
            "voice lifecycle docs must not describe camera entitlement in the current Tauri shell",
        ),
        (
            ROOT / "STATUS.md",
            "transport_factory.py         Phase 1",
            "STATUS module map must not list deleted transport_factory.py as current",
        ),
        (
            ROOT / "STATUS.md",
            "multi_input_mixer.py         Phase 1",
            "STATUS module map must not list deleted multi_input_mixer.py as current",
        ),
        (
            ROOT / "STATUS.md",
            "MicTest.tsx                Phase 1",
            "STATUS module map must not list deleted getUserMedia mic test as current",
        ),
        (
            ROOT / "STATUS.md",
            "recordWav.ts               Phase 1",
            "STATUS module map must not list deleted getUserMedia recorder as current",
        ),
        (
            ROOT / "STATUS.md",
            "493 passing",
            "STATUS must not use stale exact full-suite pytest counts as current evidence",
        ),
        (
            ROOT / "HANDOFF.md",
            "131 passing",
            "HANDOFF must not use stale exact full-suite pytest counts as current evidence",
        ),
        (
            ROOT / "HANDOFF.md",
            "No `bun run build` in the release pipeline",
            "HANDOFF must not claim frontend build coverage is missing from release/preflight",
        ),
        (
            ROOT / "HANDOFF.md",
            "Add `.github/workflows/frontend-check.yml` on next pass",
            "HANDOFF must not require a separate frontend build workflow while native-audio preflight already builds the web app",
        ),
        (
            ROOT / ".github" / "workflows" / "native-audio-preflight.yml",
            "live microphone soak\n# still run in desktop-build.yml",
            "preflight workflow comments must not imply CI performs the interactive live microphone soak",
        ),
    ]
    for path, needle, message in stale_claims:
        require_absent(path, needle, message)


def main() -> int:
    checks = [
        check_tauri_config,
        check_tauri_capabilities,
        check_plists,
        check_native_audio_sources,
        check_frontend_sources,
        check_workflow,
        check_scripts_executable,
        check_docs,
    ]
    for check in checks:
        check()
        print(f"PASS {check.__name__}")
    print("PASS macOS release config guardrails")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        raise SystemExit(1)
