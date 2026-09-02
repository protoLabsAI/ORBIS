#!/usr/bin/env bash
# Build ORBIS's checksum-verifying PyApp launcher from the pinned upstream
# source release. The upstream source archive and the UV archive digest are
# both pinned in pyapp-installer-env.sh; the downstream patch verifies UV
# before PyApp extracts or executes it.

set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
# Release builders source this file before adding platform-specific installer
# arguments. Reload the security pins here for standalone use, but preserve the
# caller's composed pip arguments across that reload.
caller_pip_extra_args="${PYAPP_PIP_EXTRA_ARGS:-}"
# shellcheck source=scripts/pyapp-installer-env.sh
source "${ROOT}/scripts/pyapp-installer-env.sh"
if [ -n "$caller_pip_extra_args" ]; then
  export PYAPP_PIP_EXTRA_ARGS="$caller_pip_extra_args"
fi
unset caller_pip_extra_args

usage() {
  echo "usage: $0 --root <cargo-install-root> [--force] [--quiet]" >&2
  echo "       $0 --test" >&2
  exit 2
}

mode="install"
install_root=""
cargo_args=()
case "${1:-}" in
  --root)
    [ -n "${2:-}" ] || usage
    install_root="$2"
    shift 2
    for arg in "$@"; do
      case "$arg" in
        --force|--quiet) cargo_args+=("$arg") ;;
        *) usage ;;
      esac
    done
    ;;
  --test)
    [ "$#" -eq 1 ] || usage
    mode="test"
    ;;
  *) usage ;;
esac

for command_name in cargo curl patch rustc tar; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "[pyapp-build] missing required command: ${command_name}" >&2
    exit 2
  }
done

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    echo "[pyapp-build] sha256sum or shasum is required" >&2
    exit 2
  fi
}

require_sha256() {
  local name="$1"
  local value="$2"
  if [[ ! "$value" =~ ^[0-9a-f]{64}$ ]]; then
    echo "[pyapp-build] ${name} must be a lowercase SHA-256 digest" >&2
    exit 2
  fi
}

build_target="$(rustc -vV | sed -n 's/^host: //p')"
case "$build_target" in
  aarch64-apple-darwin)
    uv_sha256="$PYAPP_UV_SHA256_AARCH64_APPLE_DARWIN"
    ;;
  x86_64-unknown-linux-gnu)
    uv_sha256="$PYAPP_UV_SHA256_X86_64_UNKNOWN_LINUX_GNU"
    ;;
  x86_64-pc-windows-msvc)
    uv_sha256="$PYAPP_UV_SHA256_X86_64_PC_WINDOWS_MSVC"
    ;;
  *)
    echo "[pyapp-build] no pinned UV checksum for Rust host ${build_target}" >&2
    exit 2
    ;;
esac

require_sha256 "ORBIS_PYAPP_SOURCE_SHA256" "$ORBIS_PYAPP_SOURCE_SHA256"
require_sha256 "UV checksum for ${build_target}" "$uv_sha256"
export PYAPP_UV_SHA256="$uv_sha256"

source_url="https://github.com/ofek/pyapp/releases/download/v${ORBIS_PYAPP_VERSION}/source.tar.gz"
patch_file="${ROOT}/scripts/pyapp-${ORBIS_PYAPP_VERSION}-uv-sha256.patch"
[ -f "$patch_file" ] || {
  echo "[pyapp-build] missing downstream patch: ${patch_file}" >&2
  exit 2
}

temp_parent="${TMPDIR:-/tmp}"
temp_parent="${temp_parent%/}"
work_dir="$(mktemp -d "${temp_parent}/orbis-pyapp-source.XXXXXX")"
cleanup() {
  case "${work_dir:-}" in
    "${temp_parent}"/orbis-pyapp-source.*) rm -rf -- "$work_dir" ;;
  esac
}
trap cleanup EXIT

archive="${work_dir}/source.tar.gz"
echo "[pyapp-build] downloading PyApp ${ORBIS_PYAPP_VERSION} source"
curl --proto '=https' --tlsv1.2 --fail --location --silent --show-error \
  --output "$archive" "$source_url"

actual_source_sha256="$(sha256_file "$archive")"
if [ "$actual_source_sha256" != "$ORBIS_PYAPP_SOURCE_SHA256" ]; then
  echo "[pyapp-build] PyApp source checksum mismatch" >&2
  echo "[pyapp-build] expected ${ORBIS_PYAPP_SOURCE_SHA256}" >&2
  echo "[pyapp-build] actual   ${actual_source_sha256}" >&2
  exit 3
fi

tar -xzf "$archive" -C "$work_dir"
source_dir="${work_dir}/pyapp-v${ORBIS_PYAPP_VERSION}"
[ -f "${source_dir}/Cargo.toml" ] || {
  echo "[pyapp-build] source archive has no expected pyapp-v${ORBIS_PYAPP_VERSION} root" >&2
  exit 3
}
grep -Fqx "version = \"${ORBIS_PYAPP_VERSION}\"" "${source_dir}/Cargo.toml" || {
  echo "[pyapp-build] source package version does not match ${ORBIS_PYAPP_VERSION}" >&2
  exit 3
}

patch -f -p1 -d "$source_dir" -i "$patch_file"

if [ "$mode" = "test" ]; then
  export PYAPP_PROJECT_NAME="checksum-regression"
  export PYAPP_PROJECT_VERSION="1.0.0"
  export PYAPP_PYTHON_VERSION="3.11"
  export PYAPP_EXEC_SPEC="app:main"
  cargo test --manifest-path "${source_dir}/Cargo.toml" --locked
  exit 0
fi

mkdir -p "$install_root"
cargo install --path "$source_dir" --root "$install_root" --locked "${cargo_args[@]}"
