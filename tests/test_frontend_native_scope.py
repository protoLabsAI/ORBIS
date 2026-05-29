"""Frontend package guardrails for the native fork."""

from __future__ import annotations

import json
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
