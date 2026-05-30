#!/usr/bin/env bash
# Nuke ORBIS dev state and rebuild from scratch.
#
# Wipes every cache that has ever silently broken a dev session:
#   - web/dist (stale frontend bundle — Tauri serves this; it does
#     NOT auto-rebuild when you `cargo tauri build`)
#   - dist-sdist (stale Python source distributions)
#   - src-tauri/target/release/bundle (old .app)
#   - src-tauri/binaries/orbis-<target> (staged sidecar binary)
#   - ~/Library/Application Support/pyapp/orbis (pyapp env cache —
#     content-hashed; new sidecar source CAN collide if the hash matches)
#   - ~/Library/Caches/studio.protolabs.orbis (app cache)
#   - ~/Library/Logs/studio.protolabs.orbis/sidecar.log (start fresh)
#   - /tmp/pyapp-build-fix (cargo install --root)
#   - /tmp/orbis-audio-*.sock (stale unix sockets from prior runs)
#
# Then rebuilds in the right order:
#   frontend (bun run build) → sdist → pyapp sidecar → stage →
#   tauri bundle → wipe pyapp env again (paranoia) → launch.
#
# What it does NOT touch:
#   - ~/Library/Application Support/studio.protolabs.orbis/orbis.yaml
#     (user config — wizard answers, persona, llm config)
#   - web/node_modules
#   - src-tauri/target/release/{deps,build} (Rust incremental cache —
#     keeps subsequent rebuilds fast)
#   - ~/.cargo
#
# Usage:
#   scripts/nuke-and-rebuild.sh                    # build only
#   scripts/nuke-and-rebuild.sh --launch           # build + launch
#   scripts/nuke-and-rebuild.sh --launch --tail    # + tail sidecar log
#   scripts/nuke-and-rebuild.sh --dmg              # build local DMG
#   scripts/nuke-and-rebuild.sh --legacy-cpal      # fallback input path
#
# Stops on first error.

set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

LAUNCH=0
TAIL=0
DMG=0
DMG_STAGE=""
VOICE_PROCESSING=1
for arg in "$@"; do
  case "$arg" in
    --launch) LAUNCH=1 ;;
    --tail)   TAIL=1 ;;
    --dmg)    DMG=1 ;;
    --voice-processing|--vp)
      # Kept for compatibility with older runbooks; voice-processing
      # is now the default Mac dev/release path.
      VOICE_PROCESSING=1 ;;
    --legacy-cpal|--no-voice-processing)
      # Escape hatch while validating device-specific AVAudioEngine
      # behavior. This is not the production Mac path.
      VOICE_PROCESSING=0 ;;
    -h|--help)
      sed -n '1,40p' "$0" | grep -E '^#' | sed 's/^# *//'
      exit 0 ;;
    *) echo "unknown arg: $arg" >&2; exit 2 ;;
  esac
done

CARGO_FEATURES="native-audio"
if [ "${VOICE_PROCESSING}" = "1" ]; then
  CARGO_FEATURES="native-audio,voice-processing"
fi
BUNDLE_TARGETS="app"

ts() { date "+%H:%M:%S"; }
log() { printf '\033[1;36m[%s]\033[0m %s\n' "$(ts)" "$*"; }
ok()  { printf '\033[1;32m[%s] ✓\033[0m %s\n' "$(ts)" "$*"; }
warn() { printf '\033[1;33m[%s] ⚠\033[0m %s\n' "$(ts)" "$*"; }

START_TS=$(date +%s)
cleanup() {
  status=$?
  if [ -n "${DMG_STAGE:-}" ] && [ -d "${DMG_STAGE}" ]; then
    rm -rf "${DMG_STAGE}"
  fi
  if [ "$status" -ne 0 ]; then
    echo
    warn "aborted after $(( $(date +%s) - START_TS ))s"
  fi
  exit "$status"
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# 0. Sanity
# ---------------------------------------------------------------------------
[ -f "${ROOT}/pyproject.toml" ] || { echo "not at repo root: ${ROOT}" >&2; exit 2; }
HOST_ARCH="$(uname -m)"
if [ "$(uname -s)" != "Darwin" ] || [ "${HOST_ARCH}" != "arm64" ]; then
  warn "this script targets Apple Silicon (got $(uname -s)/${HOST_ARCH})"
fi
TARGET="aarch64-apple-darwin"
VERSION="$(grep '^version' pyproject.toml | head -1 | sed 's/.*= *"\(.*\)"/\1/')"
log "repo: ${ROOT}"
log "version: ${VERSION}"
log "target: ${TARGET}"
log "bundle target: ${BUNDLE_TARGETS}"

# ---------------------------------------------------------------------------
# 1. Kill all ORBIS processes
# ---------------------------------------------------------------------------
log "killing running ORBIS processes…"
pkill -9 -f "orbis-tauri"            2>/dev/null || true
pkill -9 -f "ORBIS.app"              2>/dev/null || true
pkill -9 -f "pyapp/orbis"            2>/dev/null || true
pkill -9 -f "Application Support/pyapp/orbis" 2>/dev/null || true
pkill -9 -f "python app.py"          2>/dev/null || true
sleep 1
if pgrep -f "orbis-tauri|pyapp/orbis|ORBIS.app" >/dev/null 2>&1; then
  warn "some ORBIS processes survived pkill -9 — investigate manually"
  ps aux | grep -iE "orbis|pyapp" | grep -v grep || true
  exit 3
fi
ok "no ORBIS processes running"

# ---------------------------------------------------------------------------
# 2. Wipe stale artifacts
# ---------------------------------------------------------------------------
log "wiping stale build artifacts and caches…"

wipe() {
  if [ -e "$1" ] || [ -L "$1" ]; then
    rm -rf "$1"
    echo "  - removed $1"
  fi
}

wipe "${ROOT}/web/dist"
wipe "${ROOT}/dist-sdist"
wipe "${ROOT}/src-tauri/target/release/bundle"
wipe "${ROOT}/src-tauri/binaries/orbis-${TARGET}"
wipe "${HOME}/Library/Application Support/pyapp/orbis"
wipe "${HOME}/Library/Caches/studio.protolabs.orbis"
# WKWebView state — stale ServiceWorker / IndexedDB / cookies / localStorage
# stick around across rebuilds and have caused "Load failed" fetches against
# fresh sidecars (SW intercepts requests against an old bundle's expected URLs).
# Two bundle IDs in play: 'studio.protolabs.orbis' (when opened via
# Finder/`open`) and 'orbis-tauri' (when run from terminal directly).
for bid in studio.protolabs.orbis orbis-tauri; do
  wipe "${HOME}/Library/WebKit/${bid}"
  for d in "${HOME}/Library/HTTPStorages/${bid}"*; do
    [ -e "$d" ] && rm -rf "$d" && echo "  - removed $d"
  done
done
wipe "${HOME}/Library/Logs/studio.protolabs.orbis/sidecar.log"
wipe "/tmp/pyapp-build-fix"
wipe "/tmp/orbis-tauri.stderr"
wipe "/tmp/orbis-tauri.stdout"
# Stale unix sockets from previous runs.
for s in /tmp/orbis-audio-*.sock; do
  [ -e "$s" ] && rm -f "$s" && echo "  - removed $s"
done
# pyapp cache lives under TMPDIR too sometimes.
if [ -n "${TMPDIR:-}" ]; then
  for s in "${TMPDIR%/}"/orbis-audio-*.sock; do
    [ -e "$s" ] && rm -f "$s" && echo "  - removed $s"
  done
fi
ok "wipe complete"

# ---------------------------------------------------------------------------
# 3. Frontend
# ---------------------------------------------------------------------------
log "rebuilding frontend (bun run build)…"
if ! command -v bun >/dev/null 2>&1; then
  echo "bun not on PATH — install via 'curl -fsSL https://bun.sh/install | bash'" >&2
  exit 2
fi
cd "${ROOT}/web"
bun install --silent
bun run build
[ -f "${ROOT}/web/dist/index.html" ] || { echo "bun build produced no dist/index.html" >&2; exit 3; }
ok "frontend built ($(stat -f %m "${ROOT}/web/dist/index.html"))"
cd "${ROOT}"

# ---------------------------------------------------------------------------
# 4. Python sdist
# ---------------------------------------------------------------------------
log "building Python sdist (orbis-${VERSION}.tar.gz)…"
PYTHON="${ROOT}/.venv/bin/python"
[ -x "${PYTHON}" ] || PYTHON="$(command -v python3)"
"${PYTHON}" -m build --sdist --outdir "${ROOT}/dist-sdist" >/dev/null
SDIST="${ROOT}/dist-sdist/orbis-${VERSION}.tar.gz"
[ -f "${SDIST}" ] || { echo "expected sdist at ${SDIST}" >&2; exit 3; }
ok "sdist: ${SDIST} ($(du -h "${SDIST}" | cut -f1))"

# ---------------------------------------------------------------------------
# 5. PyApp sidecar
# ---------------------------------------------------------------------------
log "building pyapp sidecar (cargo install pyapp)…"
PYAPP_PROJECT_NAME="orbis" \
PYAPP_PROJECT_VERSION="${VERSION}" \
PYAPP_PROJECT_PATH="${SDIST}" \
PYAPP_PROJECT_FEATURES="parakeet,smart-turn" \
PYAPP_PYTHON_VERSION="3.11" \
PYAPP_EXEC_SPEC="app:main" \
PYAPP_FULL_ISOLATION="1" \
  cargo install pyapp --root /tmp/pyapp-build-fix --locked --force >/dev/null
# Bundles the Parakeet STT backend (parakeet-mlx) so STT_BACKEND=parakeet
# works. It enlarges the sidecar; drop PYAPP_PROJECT_FEATURES to ship a
# Whisper-only build.
[ -x /tmp/pyapp-build-fix/bin/pyapp ] || { echo "pyapp build produced no bin" >&2; exit 3; }
mkdir -p "${ROOT}/src-tauri/binaries"
cp /tmp/pyapp-build-fix/bin/pyapp "${ROOT}/src-tauri/binaries/orbis-${TARGET}"
ok "sidecar staged: src-tauri/binaries/orbis-${TARGET} ($(du -h "${ROOT}/src-tauri/binaries/orbis-${TARGET}" | cut -f1))"

# ---------------------------------------------------------------------------
# 6. Tauri bundle
# ---------------------------------------------------------------------------
log "building Tauri bundle (--features ${CARGO_FEATURES}, --bundles ${BUNDLE_TARGETS})…"
cargo tauri build --features "${CARGO_FEATURES}" --bundles "${BUNDLE_TARGETS}"
APP="${ROOT}/src-tauri/target/release/bundle/macos/ORBIS.app"
[ -d "${APP}" ] || { echo "expected ${APP}" >&2; exit 3; }
EXECUTABLE_NAME="$(plutil -extract CFBundleExecutable raw "${APP}/Contents/Info.plist" 2>/dev/null || true)"
EXECUTABLE="${APP}/Contents/MacOS/${EXECUTABLE_NAME}"
[ -n "${EXECUTABLE_NAME}" ] && [ -x "${EXECUTABLE}" ] || {
  echo "could not resolve executable from ${APP}/Contents/Info.plist" >&2
  exit 3
}
ok "bundle: ${APP}"

# ---------------------------------------------------------------------------
# 6a. Ad-hoc codesign with a STABLE identifier
# ---------------------------------------------------------------------------
# macOS TCC keys microphone grants on the binary's
# code signature requirement (csreq). When we ad-hoc sign with no
# explicit --identifier, codesign picks one from the bundle's
# Info.plist (CFBundleIdentifier) — usually fine — but `cargo run` and
# direct binary launches via terminal end up with a *different*
# csreq tied to the binary's path/name, producing a second TCC entry
# (orbis-tauri vs studio.protolabs.orbis). The result: every rebuild
# re-prompts for mic permission AND the WKWebView state caches stick
# around in two parallel ~/Library/WebKit/<bid>/ trees.
#
# Forcing the same --identifier on every dev build collapses both
# launch paths to one TCC entry and one WebKit storage tree.
# Production builds via the desktop-build CI workflow use a real
# Developer ID identity — this is purely the dev-loop fix.
log "ad-hoc signing with stable identifier (TCC stability across rebuilds)…"
codesign --force --deep --sign - --identifier studio.protolabs.orbis "${APP}"
ok "signed: $(codesign -dvv "${APP}" 2>&1 | grep -E 'Identifier|Signature' | head -2 | xargs)"

if [ "${DMG}" = "1" ]; then
  # Build a local installer from the already-signed .app. Asking
  # `cargo tauri build --bundles dmg` would create the DMG before this
  # script applies its stable ad-hoc signature, so the installer would
  # not contain the exact app that local launch validation uses.
  if ! command -v hdiutil >/dev/null 2>&1; then
    echo "hdiutil is required for --dmg on macOS" >&2
    exit 2
  fi
  DMG_DIR="${ROOT}/src-tauri/target/release/bundle/dmg"
  DMG_PATH="${DMG_DIR}/ORBIS_${VERSION}_aarch64.dmg"
  DMG_STAGE="${ROOT}/src-tauri/target/release/bundle/dmg-stage"
  mkdir -p "${DMG_DIR}"
  rm -f "${DMG_PATH}"
  rm -rf "${DMG_STAGE}"
  mkdir -p "${DMG_STAGE}"
  cp -R "${APP}" "${DMG_STAGE}/ORBIS.app"
  log "creating local DMG from signed app…"
  hdiutil create -volname "ORBIS" -srcfolder "${DMG_STAGE}" -ov -format UDZO "${DMG_PATH}" >/dev/null
  rm -rf "${DMG_STAGE}"
  ok "dmg: ${DMG_PATH}"
fi

# ---------------------------------------------------------------------------
# 7. Final pyapp cache wipe (paranoia)
# ---------------------------------------------------------------------------
# pyapp's env cache is content-hashed by sdist. Even with same version
# string, a different tarball produces a different env dir, so the new
# sidecar source IS executed. But content-hash collisions have happened
# in the wild — wipe again right before launch to be deterministic.
log "final pyapp env wipe…"
wipe "${HOME}/Library/Application Support/pyapp/orbis"
ok "pyapp env clean"

ELAPSED=$(( $(date +%s) - START_TS ))
echo
ok "rebuild complete in ${ELAPSED}s"
echo

# ---------------------------------------------------------------------------
# 8. Launch (optional)
# ---------------------------------------------------------------------------
if [ "${LAUNCH}" = "1" ]; then
  log "launching ${APP} from terminal (Rust stderr → /tmp/orbis-tauri.stderr)…"
  RUST_LOG=info "${EXECUTABLE}" \
    >/tmp/orbis-tauri.stdout \
    2>/tmp/orbis-tauri.stderr &
  TAURI_PID=$!
  ok "launched ${EXECUTABLE_NAME} (PID ${TAURI_PID})"
  echo
  echo "  Rust  log: /tmp/orbis-tauri.stderr"
  echo "  Sidecar  : ${HOME}/Library/Logs/studio.protolabs.orbis/sidecar.log"
  echo
  if [ "${TAIL}" = "1" ]; then
    log "tailing sidecar.log (Ctrl-C to stop — app keeps running)…"
    # Wait for the log file to appear, then tail.
    SLOG="${HOME}/Library/Logs/studio.protolabs.orbis/sidecar.log"
    for _ in $(seq 1 30); do
      [ -f "${SLOG}" ] && break
      sleep 1
    done
    tail -F "${SLOG}"
  fi
else
  echo "next:"
  echo "  open ${APP}"
  echo "  # OR with stderr visible:"
  echo "  ${EXECUTABLE} 2>&1 | tee /tmp/orbis-tauri.log"
  echo
  echo "  # OR re-run this script with --launch --tail to do both."
fi
