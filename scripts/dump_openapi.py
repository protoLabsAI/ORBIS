"""Dump ``app.openapi()`` to ``web/openapi.json``.

Used by ``web/codegen-api`` to feed the OpenAPI schema into
``openapi-typescript`` so the frontend gets compile-time-checked
types for every backend route. Committing the schema means a fresh
``bun run build`` doesn't need a running sidecar — types are static
artefacts that travel with the codebase.

The drift gate in CI re-runs this script and fails if the output
differs from what's committed; that catches the "I added an
endpoint and forgot to regenerate" case.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# scripts/ is a sibling of app.py; ensure repo root is on sys.path so
# `from app import app` resolves regardless of cwd at invocation time
# (matters for CI which calls scripts/dump_openapi.py from anywhere).
sys.path.insert(0, str(ROOT))

# Importing app builds the FastAPI instance with all routes registered.
# `app.openapi()` is a pure schema-build call — no server start, no
# lifespan side effects. Safe in a sync script.
from app import app  # noqa: E402

OUTPUT = ROOT / "web" / "openapi.json"


def main() -> int:
    schema = app.openapi()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    print(f"wrote {OUTPUT.relative_to(Path.cwd())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
