#!/usr/bin/env python3
"""Bump the version in pyproject.toml.

Usage:
    python scripts/version.py patch        # 0.1.0 → 0.1.1
    python scripts/version.py minor        # 0.1.0 → 0.2.0
    python scripts/version.py major        # 0.1.0 → 1.0.0
    python scripts/version.py 0.2.0        # explicit version

Single source of truth is ``[project].version`` in pyproject.toml. The
release pipeline (.github/workflows/prepare-release.yml) calls this
with ``patch`` on every non-release PR merge, or with a chosen bump
level via manual workflow_dispatch.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PYPROJECT = Path(__file__).parent.parent / "pyproject.toml"


def read_version() -> str:
    text = PYPROJECT.read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not m:
        raise ValueError("version field not found in pyproject.toml")
    return m.group(1)


def write_version(new_version: str) -> None:
    text = PYPROJECT.read_text()
    updated = re.sub(
        r'^(version\s*=\s*)"[^"]+"',
        rf'\g<1>"{new_version}"',
        text,
        count=1,
        flags=re.MULTILINE,
    )
    PYPROJECT.write_text(updated)


def bump(current: str, kind: str) -> str:
    if re.fullmatch(r"\d+\.\d+\.\d+.*", kind):
        return kind

    major, minor, patch = (int(x) for x in current.split(".")[:3])
    if kind == "major":
        return f"{major + 1}.0.0"
    if kind == "minor":
        return f"{major}.{minor + 1}.0"
    if kind == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"unknown bump type: {kind!r}  (use patch | minor | major | X.Y.Z)")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    kind = sys.argv[1]
    old = read_version()
    new = bump(old, kind)
    write_version(new)
    print(f"version: {old} → {new}")


if __name__ == "__main__":
    main()
