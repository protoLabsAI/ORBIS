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
    }

    assert forbidden.isdisjoint(deps)
    assert "@tauri-apps/plugin-http" in deps


def test_lockfiles_do_not_contain_pwa_runtime_packages():
    lock_text = "\n".join(
        p.read_text(encoding="utf-8")
        for p in (WEB / "bun.lock", WEB / "package-lock.json")
        if p.exists()
    )

    for needle in (
        "vite-plugin-pwa",
        "workbox-window",
        "workbox-core",
        "@pipecat-ai/client-js",
        "@pipecat-ai/client-react",
        "@pipecat-ai/small-webrtc-transport",
    ):
        assert needle not in lock_text


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
