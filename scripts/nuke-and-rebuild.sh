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
#
# Stops on first error.

set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

LAUNCH=0
TAIL=0
for arg in "$@"; do
  case "$arg" in
    --launch) LAUNCH=1 ;;
    --tail)   TAIL=1 ;;
    -h|--help)
      sed -n '1,40p' "$0" | grep -E '^#' | sed 's/^# *//'
      exit 0 ;;
    *) echo "unknown arg: $arg" >&2; exit 2 ;;
  esac
done

ts() { date "+%H:%M:%S"; }
log() { printf '\033[1;36m[%s]\033[0m %s\n' "$(ts)" "$*"; }
ok()  { printf '\033[1;32m[%s] ✓\033[0m %s\n' "$(ts)" "$*"; }
warn() { printf '\033[1;33m[%s] ⚠\033[0m %s\n' "$(ts)" "$*"; }

START_TS=$(date +%s)
trap 'echo; warn "aborted after $(( $(date +%s) - START_TS ))s"' INT TERM

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
PYAPP_PYTHON_VERSION="3.11" \
PYAPP_EXEC_SPEC="app:main" \
PYAPP_FULL_ISOLATION="1" \
  cargo install pyapp --root /tmp/pyapp-build-fix --locked --force >/dev/null
[ -x /tmp/pyapp-build-fix/bin/pyapp ] || { echo "pyapp build produced no bin" >&2; exit 3; }
mkdir -p "${ROOT}/src-tauri/binaries"
cp /tmp/pyapp-build-fix/bin/pyapp "${ROOT}/src-tauri/binaries/orbis-${TARGET}"
ok "sidecar staged: src-tauri/binaries/orbis-${TARGET} ($(du -h "${ROOT}/src-tauri/binaries/orbis-${TARGET}" | cut -f1))"

# ---------------------------------------------------------------------------
# 6. Tauri bundle
# ---------------------------------------------------------------------------
log "building Tauri app bundle (--features native-audio)…"
cargo tauri build --features native-audio --bundles app
APP="${ROOT}/src-tauri/target/release/bundle/macos/ORBIS.app"
[ -d "${APP}" ] || { echo "expected ${APP}" >&2; exit 3; }
ok "bundle: ${APP}"

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
  RUST_LOG=info "${APP}/Contents/MacOS/orbis-tauri" \
    >/tmp/orbis-tauri.stdout \
    2>/tmp/orbis-tauri.stderr &
  TAURI_PID=$!
  ok "launched orbis-tauri (PID ${TAURI_PID})"
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
  echo "  ${APP}/Contents/MacOS/orbis-tauri 2>&1 | tee /tmp/orbis-tauri.log"
  echo
  echo "  # OR re-run this script with --launch --tail to do both."
fi
