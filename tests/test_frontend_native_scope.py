"""Frontend package guardrails for the native fork."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


def _package_json() -> dict:
    return json.loads((WEB / "package.json").read_text(encoding="utf-8"))


def test_web_package_does_not_reintroduce_pwa_runtime_dependencies():
    """Native ORBIS is not a PWA; service-worker deps should stay out."""
    package = _package_json()
    deps = {
        **package.get("dependencies", {}),
        **package.get("devDependencies", {}),
    }

    forbidden = {
        "vite-plugin-pwa",
        "workbox-window",
        "workbox-core",
        "@pipecat-ai/client-js",
        "@pipecat-ai/client-react",
        "@pipecat-ai/small-webrtc-transport",
        "openapi-typescript",
    }

    assert forbidden.isdisjoint(deps)
    assert "@tauri-apps/plugin-http" in deps


def test_bun_lockfile_does_not_contain_pwa_runtime_packages():
    lock_text = (WEB / "bun.lock").read_text(encoding="utf-8")

    for needle in (
        "vite-plugin-pwa",
        "workbox-window",
        "workbox-core",
        "@pipecat-ai/client-js",
        "@pipecat-ai/client-react",
        "@pipecat-ai/small-webrtc-transport",
        "openapi-typescript",
    ):
        assert needle not in lock_text


def test_web_npm_lockfile_stays_absent():
    """Bun is the frontend package-manager source of truth."""
    assert not (WEB / "package-lock.json").exists()


def test_frontend_dist_scaffold_stays_packageable_from_fresh_clone():
    """PyApp packaging force-includes web/dist, so the path must exist."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    wheel_force_include = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"][
        "force-include"
    ]
    sdist_force_include = pyproject["tool"]["hatch"]["build"]["targets"]["sdist"][
        "force-include"
    ]
    web_gitignore = (WEB / ".gitignore").read_text(encoding="utf-8")

    assert (WEB / "dist/.gitkeep").exists()
    assert wheel_force_include["web/dist"] == "web/dist"
    assert sdist_force_include["web/dist"] == "web/dist"
    assert "dist/*" in web_gitignore
    assert "!dist/.gitkeep" in web_gitignore


def test_api_client_stays_tauri_native_not_split_deployment_browser_fetch():
    """Mac native builds route API traffic through Tauri's Rust HTTP plugin."""
    api_source = (WEB / "src/lib/api.ts").read_text(encoding="utf-8")

    assert "@tauri-apps/plugin-http" in api_source
    assert "tauriFetch(" in api_source

    forbidden_files = (
        WEB / "src/lib/backend.ts",
        WEB / "src/lib/api-types.ts",
        WEB / "src/auth/pairing.ts",
    )
    assert [p.relative_to(ROOT).as_posix() for p in forbidden_files if p.exists()] == []

    forbidden_needles = (
        "X-Orbis-Pair",
        "pairingStore",
        "apiUrl(",
        "type { paths }",
    )
    for needle in forbidden_needles:
        assert needle not in api_source


def test_native_frontend_audio_and_settings_artifacts_stay_present():
    """Native mic permission, diagnostics, and SSE bridge replace browser voice UI."""
    required_files = (
        WEB / "src/shared/audio/NativeLevelMeter.tsx",
        WEB / "src/shared/audio/microphonePermission.ts",
        WEB / "src/shared/audio/nativeAudio.ts",
        WEB / "src/plugins/settings-panel/ApiKeyField.tsx",
        WEB / "src/plugins/settings-panel/Diagnostics.tsx",
        WEB / "src/voice/useVoiceBridge.ts",
    )
    assert [p.relative_to(ROOT).as_posix() for p in required_files if not p.exists()] == []

    setup_wizard = (WEB / "src/plugins/setup-wizard/SetupWizard.tsx").read_text(
        encoding="utf-8",
    )
    mic_settings = (WEB / "src/plugins/settings-panel/MicSettings.tsx").read_text(
        encoding="utf-8",
    )
    settings_panel = (WEB / "src/plugins/settings-panel/SettingsPanel.tsx").read_text(
        encoding="utf-8",
    )
    voice_bridge = (WEB / "src/voice/VoiceStateBridge.tsx").read_text(encoding="utf-8")
    diagnostics = (WEB / "src/plugins/settings-panel/Diagnostics.tsx").read_text(
        encoding="utf-8",
    )

    assert "@/shared/audio/NativeLevelMeter" in setup_wizard
    assert "@/shared/audio/nativeAudio" in setup_wizard
    assert "@/shared/audio/microphonePermission" in setup_wizard
    assert "@/shared/audio/NativeLevelMeter" in mic_settings
    assert "@/shared/audio/nativeAudio" in mic_settings
    assert "@/shared/audio/microphonePermission" in mic_settings
    assert "ApiKeyField" in settings_panel
    assert "Diagnostics" in settings_panel
    assert "useVoiceBridge" in voice_bridge
    assert "clear_browsing_data" in diagnostics


def test_generated_openapi_browser_client_pipeline_stays_absent():
    """Generated OpenAPI client drift belongs behind a native API redesign."""
    forbidden_files = (
        ROOT / ".github/workflows/codegen-drift.yml",
        ROOT / "scripts/codegen-api.sh",
        ROOT / "scripts/dump_openapi.py",
        WEB / "openapi.json",
    )

    assert [p.relative_to(ROOT).as_posix() for p in forbidden_files if p.exists()] == []


def test_upstream_orb_variants_stay_ported_to_native_frontend():
    """Native ORBIS keeps upstream visual product work that fits Tauri."""
    variants = WEB / "src/plugins/orb/variants"
    imports = (variants / "index.ts").read_text(encoding="utf-8")

    for variant in ("tetra", "lattice", "spectrum", "galaxy"):
        assert f"import './{variant}';" in imports
        assert (variants / variant / "index.tsx").exists()
        assert (variants / variant / "schema.ts").exists()
        assert (variants / variant / "presets.ts").exists()

    assert "import './liquid';" not in imports
    assert not (variants / "liquid").exists()


def test_upstream_orb_spectrum_shader_hardening_stays_ported():
    """The Rainbow spectrum palette must not regress to a blank render."""
    spectrum = WEB / "src/plugins/orb/variants/spectrum"
    presets = (spectrum / "presets.ts").read_text(encoding="utf-8")
    shader = (spectrum / "shaders/spectrum.frag.glsl").read_text(encoding="utf-8")

    assert "fadeOuter: 2.60, fadeInner: 2.45" in presets
    assert "float lo = min(uFadeInner, uFadeOuter);" in shader
    assert "float hi = max(uFadeInner, uFadeOuter);" in shader
    assert "smoothstep(lo, max(hi, lo + 1e-4), distFromCenter)" in shader
    assert "smoothstep(uFadeInner, uFadeOuter, distFromCenter)" not in shader


def test_upstream_orb_randomize_keeps_user_resolution():
    """Randomize should not mutate the user's manually tuned DPR setting."""
    panel = (WEB / "src/plugins/orb-settings/OrbSettingsPanel.tsx").read_text(
        encoding="utf-8",
    )

    assert "if (spec.key === 'dpr') continue;" in panel


def test_upstream_orb_custom_presets_stay_per_variant():
    """Saved orb presets must remain scoped to the active variant schema."""
    storage = (WEB / "src/plugins/orb/storage.ts").read_text(encoding="utf-8")
    store = (WEB / "src/plugins/orb/store.ts").read_text(encoding="utf-8")
    panel = (WEB / "src/plugins/orb-settings/OrbSettingsPanel.tsx").read_text(
        encoding="utf-8",
    )

    assert "STORAGE_CUSTOM_V2 = 'orbis.customPresets.v2'" in storage
    assert "loadCustomByVariant(variantId: string)" in storage
    assert "saveCustomByVariant(variantId: string" in storage
    assert "orbis.customPresets'" not in storage

    assert "loadCustomByVariant(this.snap.variantId)" in store
    assert "loadCustom()" not in store

    assert "setCustomMap(loadCustomByVariant(variant.id));" in panel
    assert "saveCustomByVariant(variant.id, next);" in panel
    assert "}, [variant?.id]);" in panel
    assert "setCustomName('');" in panel
    assert "saveCustom(next)" not in panel
