#!/usr/bin/env bash
# Host-side ORBIS native-audio preflight.
#
# This is the repeatable non-live check to run before handing a patch set to
# an Apple Silicon Mac for DMG build + microphone soak. It mirrors the cheap
# parts of .github/workflows/native-audio-preflight.yml and intentionally does
# not attempt signing, notarization, DMG mounting, or live mic validation.

set -euo pipefail

cd "$(dirname "$0")/.."

log() { printf '[preflight] %s\n' "$*"; }
fail() {
  printf '[preflight] ERROR: %s\n' "$*" >&2
  exit 2
}
require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "missing required command: $1"
}

cleanup() {
  if [ -n "${sidecar:-}" ]; then
    rm -f "$sidecar"
    rmdir src-tauri/binaries 2>/dev/null || true
  fi
  find . -path './.git' -prune -o -type d -name __pycache__ -exec rm -rf {} +
  rm -rf .pytest_cache
}
trap cleanup EXIT

require_cmd bash
require_cmd bun
require_cmd cargo
require_cmd git
require_cmd rustc

PYTHON=".venv/bin/python"
PYTEST=".venv/bin/pytest"
if [ ! -x "$PYTHON" ]; then
  PYTHON="$(command -v python3 || true)"
fi
if [ ! -x "$PYTEST" ]; then
  PYTEST="$(command -v pytest || true)"
fi
[ -n "$PYTHON" ] || fail "missing required command: python3"
[ -n "$PYTEST" ] || fail "missing required command: pytest (or .venv/bin/pytest)"

log "static macOS release guardrails"
scripts/check-macos-release-config.py

log "Python and shell syntax"
"$PYTHON" -m py_compile scripts/check-macos-release-config.py app.py
bash -n "$0" scripts/validate-macos-native-audio.sh scripts/nuke-and-rebuild.sh \
    scripts/build-patched-pyapp.sh scripts/pyapp-installer-env.sh \
    tests/test-build-patched-pyapp-cli.sh

log "PyApp builder CLI regression"
tests/test-build-patched-pyapp-cli.sh

log "PyApp UV checksum regression"
scripts/build-patched-pyapp.sh --test

if command -v yamllint >/dev/null 2>&1; then
  log "workflow YAML lint"
  yamllint -d '{extends: relaxed, rules: {line-length: disable}}' \
    .github/workflows/desktop-build.yml \
    .github/workflows/native-audio-preflight.yml
else
  log "yamllint not found; skipping workflow YAML lint"
fi

log "web SPA build"
(
  cd web
  bun install --frozen-lockfile
  bun run build
)

log "Rust formatting"
cargo fmt --manifest-path src-tauri/Cargo.toml --check

log "focused Python native-audio tests"
"$PYTEST" tests/test_local_transport.py tests/test_healthz_native_audio.py

log "Tauri Rust tests with temporary dummy sidecar"
target="$(rustc -vV | sed -n 's/host: //p')"
sidecar="src-tauri/binaries/orbis-${target}"
mkdir -p src-tauri/binaries
printf '#!/bin/sh\necho dummy sidecar\n' > "$sidecar"
chmod +x "$sidecar"

cargo test --manifest-path src-tauri/Cargo.toml
cargo test --manifest-path src-tauri/Cargo.toml --features native-audio,voice-processing

log "diff whitespace"
git diff --check

log "host native-audio preflight passed"
