"""Frontend package guardrails for the native fork."""

from __future__ import annotations

import json
import re
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


def test_vite_config_does_not_register_pwa_service_worker():
    """WKWebView service workers broke native /api calls; keep Vite PWA out."""
    vite_config = (WEB / "vite.config.ts").read_text(encoding="utf-8")

    forbidden = (
        "vite-plugin-pwa",
        "VitePWA",
        "registerType",
        "workbox",
        "navigateFallbackDenylist",
        "pwa-192.png",
        "pwa-maskable-512.png",
    )
    for needle in forbidden:
        assert needle not in vite_config

    required = (
        "PWA support removed",
        "transition SW *itself* intercepts /api/* fetches",
        "if a future ORBIS-on-the-web revival happens",
        "ORBIS_BACKEND_URL",
        "'/api'",
        "'/.well-known'",
    )
    for needle in required:
        assert needle in vite_config


def test_web_public_icons_are_not_named_as_pwa_assets():
    """The native shell may use web icons; do not present them as PWA assets."""
    web_index = (WEB / "index.html").read_text(encoding="utf-8")
    gen_icons = (ROOT / "scripts/gen-icons.mjs").read_text(encoding="utf-8")

    required_paths = (
        "public/favicon.svg",
        "public/app-icon-192.png",
        "public/app-icon-512.png",
        "public/app-icon-maskable-512.png",
    )
    for path in required_paths:
        assert (WEB / path).exists()

    assert 'href="/app-icon-192.png"' in web_index
    assert "web/public/app-icon-192.png" in gen_icons
    assert "web/public/app-icon-maskable-512.png" in gen_icons

    forbidden_paths = (
        "public/pwa-192.png",
        "public/pwa-512.png",
        "public/pwa-maskable-512.png",
    )
    for path in forbidden_paths:
        assert not (WEB / path).exists()

    for source in (web_index, gen_icons):
        assert "pwa-192.png" not in source
        assert "pwa-512.png" not in source
        assert "pwa-maskable-512.png" not in source


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
    """Mac native builds route API traffic through Tauri IPC (Rust reqwest).

    macOS Tahoe's WKWebView drops/hangs HTTP bodies even via plugin-http,
    so api.ts now invokes the `api_request` command rather than fetch.
    """
    api_source = (WEB / "src/lib/api.ts").read_text(encoding="utf-8")

    assert "@tauri-apps/api/core" in api_source
    assert "invoke" in api_source
    assert "api_request" in api_source

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
    # The old single SettingsPanel was split into Voice/Agent/System tabs;
    # ApiKeyField + Diagnostics now live in the System tab.
    settings_panel = (WEB / "src/plugins/settings-panel/SystemPanel.tsx").read_text(
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


def test_native_voice_bridge_stays_sse_eventsource_based():
    """Native voice UI state comes from the sidecar SSE stream, bridged to
    the frontend as Tauri `orbis-sse` events (WKWebView won't stream a
    cross-origin EventSource on Tahoe; Rust consumes /api/events instead).
    """
    bridge_hook = (WEB / "src/voice/useVoiceBridge.ts").read_text(encoding="utf-8")
    bridge_mount = (WEB / "src/voice/VoiceStateBridge.tsx").read_text(encoding="utf-8")
    logs_collector = (WEB / "src/plugins/logs-panel/LogsCollector.tsx").read_text(
        encoding="utf-8",
    )

    assert "@tauri-apps/api/event" in bridge_hook
    assert "listen<" in bridge_hook or "listen(" in bridge_hook
    assert "orbis-sse" in bridge_hook
    for event in ("bot-state", "transcript", "session", "tool-call", "delegation-progress"):
        assert f"'{event}'" in bridge_hook
    assert "useVoiceBridge();" in bridge_mount
    assert "source: 'sse'" in logs_collector
    assert "source: 'voice'" in logs_collector
    assert "voiceStore.subscribe(emit)" in logs_collector

    forbidden = (
        "@pipecat-ai/client-react",
        "useRTVIClientEvent",
        "usePipecatClientTransportState",
        "RTVIEvent",
        "PipecatClientProvider",
    )
    for source in (bridge_hook, bridge_mount, logs_collector):
        for needle in forbidden:
            assert needle not in source


def test_browser_voice_and_connect_ui_stays_absent():
    """Hosted/WebRTC voice UI is not part of the native Tauri runtime."""
    forbidden_files = (
        WEB / "src/auth/ConnectScreen.tsx",
        WEB / "src/shared/audio/MicTest.tsx",
        WEB / "src/shared/audio/recordWav.ts",
        WEB / "src/voice/ConnectionBanner.tsx",
        WEB / "src/voice/DelegateHealthBanner.tsx",
        WEB / "src/voice/MicPermissionRationale.tsx",
        WEB / "src/voice/PiPHost.tsx",
        WEB / "src/voice/PiPOverlay.tsx",
        WEB / "src/voice/PiPTrigger.tsx",
        WEB / "src/voice/client.ts",
        WEB / "src/voice/useMicPermission.ts",
        WEB / "src/voice/usePipSupport.ts",
    )
    assert [p.relative_to(ROOT).as_posix() for p in forbidden_files if p.exists()] == []


def test_generated_openapi_browser_client_pipeline_stays_absent():
    """Generated OpenAPI client drift belongs behind a native API redesign."""
    forbidden_files = (
        ROOT / ".github/workflows/codegen-drift.yml",
        ROOT / "scripts/codegen-api.sh",
        ROOT / "scripts/dump_openapi.py",
        WEB / "openapi.json",
    )

    assert [p.relative_to(ROOT).as_posix() for p in forbidden_files if p.exists()] == []


# Design-system guard: the chrome is fully tokenized (see the DS true-up,
# web/src/index.css @theme). Raw zinc-* role colors and literal text-[Npx]
# sizes drift apart and made every visual tweak a scavenger hunt — they must
# go through the semantic tokens (text-fg-*/bg-base/border-edge/text-brand,
# text-micro/helper/label) instead. The orb's own color world (palette data,
# shaders) is exempt.
_SPRAWL_PATTERNS = (
    re.compile(r"text-zinc-\d"),          # role-drift text colors
    re.compile(r"(?:bg|border)-zinc-\d"),  # role-drift surfaces/edges
    re.compile(r"text-\[\d+px\]"),        # literal font sizes
    re.compile(r"-(?:amber|emerald)-\d"),  # un-tokenized accent / success
)
# Exempt: the orb's intrinsic color system (presets/shaders/variant chrome).
_SPRAWL_EXEMPT = ("src/plugins/orb/variants/",)


def test_chrome_uses_design_tokens_not_raw_tailwind_palette():
    """No raw zinc-*/amber-*/emerald-* or literal text-[Npx] in the chrome."""
    src = WEB / "src"
    violations: list[str] = []
    for path in sorted([*src.rglob("*.tsx"), *src.rglob("*.ts")]):
        rel = path.relative_to(WEB).as_posix()
        if any(ex in rel for ex in _SPRAWL_EXEMPT):
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if any(p.search(line) for p in _SPRAWL_PATTERNS):
                violations.append(f"{rel}:{lineno}: {line.strip()}")

    assert not violations, (
        "Raw Tailwind palette / literal px sizes found in chrome — route these "
        "through the design tokens in web/src/index.css:\n  "
        + "\n  ".join(violations)
    )


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
