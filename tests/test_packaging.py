"""Guard: every shipped first-party package must be in hatchling's wheel
``only-include`` AND sdist ``include``.

Why this exists: CI runs pytest from the source tree, where every top-level
package imports fine. The *built* sidecar (pyapp installs the wheel/sdist) only
contains what these lists enumerate — so a package omitted from them passes the
whole suite yet crashes the packaged app at boot with ``ModuleNotFoundError``.
That is exactly what happened when ``server/`` (the extracted routers package)
was added without updating pyproject: ``No module named 'server'`` on launch,
invisible to CI. This test closes that gap and auto-covers future packages.
"""

from __future__ import annotations

import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # py<3.11
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]

# Top-level packages that are dev-only and intentionally NOT shipped.
_DEV_ONLY = {"tests", "evals"}


def _shipped_packages() -> set[str]:
    return {
        p.name
        for p in ROOT.iterdir()
        if p.is_dir()
        and (p / "__init__.py").is_file()
        and p.name not in _DEV_ONLY
        and not p.name.startswith(".")
    }


def test_all_shipped_packages_declared_in_pyproject():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    targets = data["tool"]["hatch"]["build"]["targets"]
    wheel = set(targets["wheel"]["only-include"])
    sdist = set(targets["sdist"]["include"])

    pkgs = _shipped_packages()
    assert "server" in pkgs, "sanity: server package should exist on this branch"

    missing_wheel = pkgs - wheel
    missing_sdist = pkgs - sdist
    assert not missing_wheel, (
        "first-party packages missing from [tool.hatch.build.targets.wheel] "
        f"only-include: {sorted(missing_wheel)} — the built sidecar will "
        "ModuleNotFoundError on them even though tests pass from source"
    )
    assert not missing_sdist, (
        "first-party packages missing from [tool.hatch.build.targets.sdist] "
        f"include: {sorted(missing_sdist)}"
    )


def test_all_shipped_packages_copied_into_docker_runtime():
    """Keep the source-copy image path in sync with packaged installs.

    Docker installs dependencies from ``pyproject.toml`` and then copies the
    application source directly, so hatchling's include lists cannot protect
    this build path.  A newly shipped package must have an explicit directory
    copy or imports can pass from the checkout and fail only when the runtime
    image boots.
    """
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    copied_packages = {
        match.group("source")
        for match in re.finditer(
            r"^COPY\s+(?P<source>[A-Za-z0-9_-]+)/\s+\./(?P=source)/\s*$",
            dockerfile,
            flags=re.MULTILINE,
        )
    }

    missing_docker = _shipped_packages() - copied_packages
    assert not missing_docker, (
        "first-party packages missing from the Docker runtime source copies: "
        f"{sorted(missing_docker)} — add `COPY <package>/ ./<package>/` or "
        "the image can fail with ModuleNotFoundError at boot"
    )


def test_docker_workflows_verify_runtime_before_publishing():
    """A failed boot smoke must not leave broken fleet or release tags."""
    workflows = [
        ROOT / ".github/workflows/docker-publish.yml",
        ROOT / ".github/workflows/release.yml",
    ]

    for path in workflows:
        workflow = path.read_text(encoding="utf-8")
        build = workflow.index("- name: Build Docker image for verification")
        verify = workflow.index("- name: Verify — module import in runtime image")
        publish = workflow.index("- name: Push verified Docker image")

        assert build < verify < publish, (
            f"{path.name} must build, smoke, then publish the Docker image"
        )
        assert "load: true" in workflow[build:verify]
        smoke = workflow[verify:publish]
        for module in ("acp", "app", "server", "agent.delegate_adapters"):
            assert module in smoke, f"{path.name} smoke must import {module}"
        assert "push: true" not in workflow[:publish], (
            f"{path.name} publishes an image before its runtime smoke test"
        )
