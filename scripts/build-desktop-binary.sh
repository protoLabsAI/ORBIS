#!/usr/bin/env bash
# Build an ORBIS desktop sidecar binary via PyApp for the current host.
#
# Matches the CI workflow at .github/workflows/desktop-build.yml so a
# local smoke-test build mirrors what ships. Writes to ./dist/.
#
# Usage:
#   scripts/build-desktop-binary.sh            # auto-detect version from pyproject
#   scripts/build-desktop-binary.sh v0.1.8     # explicit tag
#
# Requirements:
#   - Rust + cargo on PATH (`curl https://sh.rustup.rs | sh`)
#   - Python 3.11 (only used to read pyproject.toml; the built binary
#     ships its own Python)

set -euo pipefail

cd "$(dirname "$0")/.."
source scripts/pyapp-installer-env.sh

VERSION="${1:-}"
if [ -z "${VERSION}" ]; then
  VERSION="v$(grep '^version' pyproject.toml | head -1 | sed 's/.*= *"\(.*\)"/\1/')"
fi
VERSION_NO_V="${VERSION#v}"

HOST_OS="$(uname -s)"
HOST_ARCH="$(uname -m)"

case "${HOST_OS}" in
  Darwin)
    # ORBIS desktop targets Apple Silicon (M1+) only; Intel Macs are
    # out of scope per the hardware-requirements discussion. Fail
    # fast with a clear message rather than building an arm64 binary
    # that won't run on an x86_64 host.
    if [ "${HOST_ARCH}" != "arm64" ]; then
      echo "Unsupported macOS host arch: ${HOST_ARCH}. ORBIS desktop" >&2
      echo "requires an Apple Silicon (arm64) Mac. See DECISIONS.md." >&2
      exit 2
    fi
    TARGET="aarch64-apple-darwin"
    SUFFIX=""
    TORCH_INDEX=""
    ;;
  Linux)
    if [ "${HOST_ARCH}" != "x86_64" ]; then
      echo "Unsupported Linux host arch: ${HOST_ARCH}. ORBIS desktop" >&2
      echo "builds target x86_64-unknown-linux-gnu." >&2
      exit 2
    fi
    TARGET="x86_64-unknown-linux-gnu"
    SUFFIX=""
    TORCH_INDEX="https://download.pytorch.org/whl/cu128"
    ;;
  MINGW*|MSYS*|CYGWIN*)
    TARGET="x86_64-pc-windows-msvc"
    SUFFIX=".exe"
    TORCH_INDEX="https://download.pytorch.org/whl/cu128"
    ;;
  *)
    echo "Unsupported host: ${HOST_OS}/${HOST_ARCH}" >&2
    exit 2
    ;;
esac

PROJECT_PATH="$(pwd)"   # split so a failing `pwd` surfaces via set -e

# PyApp takes an sdist/wheel file, not a directory. Build an sdist
# now so the downstream PYAPP_PROJECT_PATH points at a real file.
echo "[build-desktop] Building sdist..."
python3 -m pip install --quiet --upgrade build
python3 -m build --sdist --outdir "${PROJECT_PATH}/dist-sdist"
SDIST="${PROJECT_PATH}/dist-sdist/orbis-${VERSION_NO_V}.tar.gz"
if [ ! -f "${SDIST}" ]; then
  echo "Expected sdist at ${SDIST} but it doesn't exist." >&2
  echo "Check pyproject.toml's [project].version matches ${VERSION_NO_V}." >&2
  exit 2
fi

export PYAPP_PROJECT_NAME="orbis"
export PYAPP_PROJECT_VERSION="${VERSION_NO_V}"
export PYAPP_PROJECT_PATH="${SDIST}"
export PYAPP_PYTHON_VERSION="3.11"
export PYAPP_EXEC_SPEC="app:main"
export PYAPP_FULL_ISOLATION="1"
if [ -n "${TORCH_INDEX}" ]; then
  export PYAPP_PIP_EXTRA_ARGS="--extra-index-url ${TORCH_INDEX}"
fi

OUT="dist/orbis-${TARGET}${SUFFIX}"
mkdir -p dist

echo "[build-desktop] Building ${OUT} (ORBIS ${VERSION_NO_V}, PyApp ${ORBIS_PYAPP_VERSION}, UV ${PYAPP_UV_VERSION}, target ${TARGET})..."
cargo install pyapp --version "${ORBIS_PYAPP_VERSION}" --root dist --locked --force

if [ "${HOST_OS}" = "Darwin" ] || [ "${HOST_OS}" = "Linux" ]; then
  mv dist/bin/pyapp "${OUT}"
else
  mv dist/bin/pyapp.exe "${OUT}"
fi

echo "[build-desktop] Wrote ${OUT} ($(du -h "${OUT}" | cut -f1))"

# Copy into src-tauri/binaries so Tauri picks it up without a manual step.
TAURI_BIN="src-tauri/binaries/orbis-${TARGET}${SUFFIX}"
cp "${OUT}" "${TAURI_BIN}"
echo "[build-desktop] Copied to ${TAURI_BIN}"

# Bust the pyapp cache so the next app launch always runs the new version.
PYAPP_CACHE="${HOME}/Library/Application Support/pyapp/orbis"
if [ -d "${PYAPP_CACHE}" ]; then
  rm -rf "${PYAPP_CACHE}"
  echo "[build-desktop] Cleared pyapp cache at ${PYAPP_CACHE}"
fi

echo "[build-desktop] Smoke-test: ORBIS_ALLOW_CPU=1 ${OUT} --port 0"
