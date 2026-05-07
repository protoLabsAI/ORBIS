"""Dump ``app.openapi()`` to ``web/openapi.json``.

Used by ``web/codegen-api`` to feed the OpenAPI schema into
``openapi-typescript`` so the frontend gets compile-time-checked
types for every backend route. Committing the schema means a fresh
``bun run build`` doesn't need a running sidecar — types are static
artefacts that travel with the codebase.

The drift gate in CI re-runs this script and fails if the output
differs from what's committed; that catches the "I added an
endpoint and forgot to regenerate" case.

Filters routes the frontend doesn't consume — auto-registered PWA
asset endpoints (/manifest.webmanifest, /pwa-*.png, /sw.js…) leak
absolute paths from ``web/dist`` into the schema and would make the
drift gate environment-dependent (``/Users/kj/...`` vs ``/home/runner/...``).
A2A inbound endpoints similarly aren't called from api.ts. The kept
surface is /api/* + /healthz + a few well-known fixtures.
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

def _keep_path(path: str) -> bool:
    """Frontend cares about /api/* and /healthz. Everything else
    (PWA static assets, A2A inbound, /a2a/*, the SPA fallback) is
    auto-registered and either leaks absolute paths into the schema
    or isn't called from api.ts. Pruning keeps the schema stable
    across environments so the drift gate is reliable."""
    if path == "/healthz":
        return True
    return path.startswith("/api/")


def _filter_schema(schema: dict) -> dict:
    paths = schema.get("paths", {})
    schema["paths"] = {p: v for p, v in paths.items() if _keep_path(p)}
    return schema


def main() -> int:
    schema = _filter_schema(app.openapi())
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    print(f"wrote {OUTPUT.relative_to(Path.cwd())} ({len(schema['paths'])} paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
