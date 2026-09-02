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

import agent.delegates as delegates_module
from agent.delegate_config_store import read_delegates, write_delegates
from agent.delegates import _DEFAULT_HUB_DESCRIPTION, migrate_default_hub_endpoint

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
    assert hub["description"] == _DEFAULT_HUB_DESCRIPTION
    assert hub["url"] == "http://127.0.0.1:7870/a2a"


def test_migrates_only_legacy_default_hub_and_preserves_file_shape(
    tmp_path, monkeypatch,
) -> None:
    path = tmp_path / "delegates.yaml"
    path.write_text(
        "# keep this unrelated URL: http://127.0.0.1:7871/a2a\n"
        "delegates:\n"
        "  - name: other\n"
        "    type: a2a\n"
        "    description: other agent\n"
        "    url: http://127.0.0.1:7871/a2a\n"
        "    headers: {X-Custom: yes}\n"
        "  - name: hub\n"
        "    type: a2a\n"
        "    description: >\n"
        "      Multi-step reasoning, tool use, fleet delegation, background work,\n"
        "      and long-horizon tasks. Use for: complex goals requiring multiple\n"
        "      tools, tasks that should run in the background, research, anything\n"
        "      that spans multiple turns.\n"
        "    url: http://127.0.0.1:7871/a2a\n",
        encoding="utf-8",
    )
    replacements = []
    real_replace = delegates_module.os.replace

    def _recording_replace(source, destination):
        replacements.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr(delegates_module.os, "replace", _recording_replace)

    assert migrate_default_hub_endpoint(path) is True
    migrated = path.read_text(encoding="utf-8")
    assert migrated.count("http://127.0.0.1:7871/a2a") == 2  # comment + other
    assert migrated.count("http://127.0.0.1:7870/a2a") == 1
    assert "# keep this unrelated URL" in migrated
    assert "headers: {X-Custom: yes}" in migrated
    assert len(replacements) == 1
    assert replacements[0][0].parent == path.parent
    assert replacements[0][1] == path
    assert migrate_default_hub_endpoint(path) is False  # one-time / idempotent


def test_does_not_migrate_description_customized_hub(tmp_path) -> None:
    path = tmp_path / "delegates.yaml"
    original = (
        "delegates:\n"
        "  - name: hub\n"
        "    type: a2a\n"
        "    description: secured local brain\n"
        "    url: http://127.0.0.1:7871/a2a\n"
    )
    path.write_text(original, encoding="utf-8")

    assert migrate_default_hub_endpoint(path) is False
    assert path.read_text(encoding="utf-8") == original


def test_does_not_migrate_aliased_default_hub(tmp_path) -> None:
    path = tmp_path / "delegates.yaml"
    original = (
        "defaults: &legacy_hub\n"
        "  name: hub\n"
        "  type: a2a\n"
        "  description: >\n"
        "    Multi-step reasoning, tool use, fleet delegation, background work,\n"
        "    and long-horizon tasks. Use for: complex goals requiring multiple\n"
        "    tools, tasks that should run in the background, research, anything\n"
        "    that spans multiple turns.\n"
        "  url: http://127.0.0.1:7871/a2a\n"
        "delegates:\n"
        "  - *legacy_hub\n"
    )
    path.write_text(original, encoding="utf-8")

    assert migrate_default_hub_endpoint(path) is False
    assert path.read_text(encoding="utf-8") == original


def test_migrates_untouched_hub_after_settings_round_trip(tmp_path) -> None:
    """Settings strips description edges; that is not a user customization."""
    path = tmp_path / "delegates.yaml"
    path.write_text(
        "delegates:\n"
        "  - name: hub\n"
        "    type: a2a\n"
        "    description: >\n"
        "      Multi-step reasoning, tool use, fleet delegation, background work,\n"
        "      and long-horizon tasks. Use for: complex goals requiring multiple\n"
        "      tools, tasks that should run in the background, research, anything\n"
        "      that spans multiple turns.\n"
        "    url: http://127.0.0.1:7871/a2a\n",
        encoding="utf-8",
    )

    # Exercise the same read/normalize/write path as the Delegates Settings API.
    write_delegates(read_delegates(path), path)
    round_tripped = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert round_tripped["delegates"][0]["description"] == (
        _DEFAULT_HUB_DESCRIPTION.strip()
    )

    assert migrate_default_hub_endpoint(path) is True
    migrated = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert migrated["delegates"][0]["url"] == "http://127.0.0.1:7870/a2a"


def test_does_not_replace_symlinked_delegates_file(tmp_path, monkeypatch) -> None:
    real_path = tmp_path / "real-delegates.yaml"
    real_path.write_text(
        "delegates:\n"
        "  - name: hub\n"
        "    type: a2a\n"
        f"    description: {_DEFAULT_HUB_DESCRIPTION.strip()!r}\n"
        "    url: http://127.0.0.1:7871/a2a\n",
        encoding="utf-8",
    )
    link_path = tmp_path / "delegates.yaml"
    link_path.symlink_to(real_path)

    def _unexpected_replace(*_args) -> None:
        raise AssertionError("symlinked config must never reach os.replace")

    monkeypatch.setattr(delegates_module.os, "replace", _unexpected_replace)

    assert migrate_default_hub_endpoint(link_path) is False
    assert link_path.is_symlink()
    assert "http://127.0.0.1:7871/a2a" in real_path.read_text(encoding="utf-8")
