#!/usr/bin/env bash
# Regenerate the typed-frontend artefacts from the FastAPI schema.
#
# Two steps in order:
#   1. dump_openapi.py imports app.py and writes web/openapi.json
#      (the canonical schema for this commit)
#   2. openapi-typescript reads that file and generates
#      web/src/lib/api-types.ts (the static TS types every api.ts
#      helper consumes)
#
# Run after touching any FastAPI route. CI's drift gate re-runs this
# and fails on a non-empty git diff so a forgotten regeneration never
# slips into main.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Pin to the project venv — running with the system python misses
# pipecat / fastapi / langfuse and import fails. The release flow does
# the same dance. CI sets PYTHON=python (the workflow's setup-python
# already has app.py importable from the system pip install -e), so
# accept either an absolute path *or* a command on PATH.
PYTHON="${PYTHON:-${ROOT}/.venv/bin/python}"
case "$PYTHON" in
  */*) [ -x "$PYTHON" ] || { echo "error: $PYTHON not found. Activate the venv or set PYTHON=…" >&2; exit 1; } ;;
  *)   command -v "$PYTHON" >/dev/null || { echo "error: $PYTHON not on PATH. Activate the venv or set PYTHON=…" >&2; exit 1; } ;;
esac

echo "→ dumping openapi.json from app.py"
"$PYTHON" scripts/dump_openapi.py

echo "→ regenerating web/src/lib/api-types.ts"
cd web
bun x openapi-typescript openapi.json -o src/lib/api-types.ts

echo "✓ codegen complete"
