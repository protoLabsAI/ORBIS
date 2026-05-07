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
# the same dance.
PYTHON="${PYTHON:-${ROOT}/.venv/bin/python}"
if [ ! -x "$PYTHON" ]; then
  echo "error: ${PYTHON} not found. Activate the venv or set PYTHON=…" >&2
  exit 1
fi

echo "→ dumping openapi.json from app.py"
"$PYTHON" scripts/dump_openapi.py

echo "→ regenerating web/src/lib/api-types.ts"
cd web
bun x openapi-typescript openapi.json -o src/lib/api-types.ts

echo "✓ codegen complete"
