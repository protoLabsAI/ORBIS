"""Guard: delegates must actually load in the BUILT app (orbis-2az).

The installed .app launches with cwd=/, so the cwd-relative default
`config/delegates.yaml` resolves to nothing — the DelegateRegistry and
/api/delegates come up empty and `delegate_to` is never registered. The
Tauri shell must bundle delegates.yaml and pass its resolved path as the
DELEGATES_YAML env var (mirroring ORBIS_STARTER_ORBS).
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_delegates_yaml_is_bundled_as_resource() -> None:
    conf = json.loads((ROOT / "src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    resources = conf["bundle"]["resources"]
    assert "../config/delegates.yaml" in resources, (
        "config/delegates.yaml must be bundled so the installed app has delegates"
    )
    assert resources["../config/delegates.yaml"] == "config/delegates.yaml"


def test_tauri_shell_wires_delegates_env() -> None:
    lib = (ROOT / "src-tauri/src/lib.rs").read_text(encoding="utf-8")
    # Resolver + env wiring, mirroring the starter_orbs pattern.
    assert "resolve_delegates_path" in lib
    assert 'resolve("config/delegates.yaml", BaseDirectory::Resource)' in lib
    assert 'command.env("DELEGATES_YAML"' in lib


def test_repo_delegates_yaml_present_and_nonempty() -> None:
    p = ROOT / "config/delegates.yaml"
    assert p.exists(), "config/delegates.yaml must exist to be bundled"
    assert "delegates:" in p.read_text(encoding="utf-8")


def test_bundled_hub_uses_production_endpoint() -> None:
    config = yaml.safe_load(
        (ROOT / "config/delegates.yaml").read_text(encoding="utf-8")
    )
    hub = next(d for d in config["delegates"] if d["name"] == "hub")
    assert hub["type"] == "a2a"
    assert hub["url"] == "http://127.0.0.1:7870/a2a"


def test_boot_status_warns_without_blocking_on_confirmed_hub_failure() -> None:
    source = (ROOT / "web/src/components/BootStatus.tsx").read_text(
        encoding="utf-8"
    )
    assert "setReady(true);" in source
    assert "void warnIfHubUnavailable();" in source
    assert "delegate.name === 'hub'" in source
    assert "hub.ok === false" in source
    assert "hub unavailable — delegated work is offline" in source
