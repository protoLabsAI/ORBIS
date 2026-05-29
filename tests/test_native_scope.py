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
        "src-tauri/Cargo.toml",
        "src-tauri/tauri.conf.json",
        "scripts/check-macos-release-config.py",
        "scripts/validate-macos-native-audio.sh",
        "voice/local_transport.py",
        "voice/native_bargein.py",
        "voice/sse_bus.py",
    )

    missing = [path for path in required_paths if not (ROOT / path).exists()]

    assert missing == []


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
