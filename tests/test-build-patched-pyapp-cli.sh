#!/usr/bin/env bash
# Exercise the PyApp builder's public install CLI without downloading or
# compiling upstream sources. macOS CI runs this with the system Bash 3.2,
# matching the shell used by desktop-build.yml.

set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
BASH_BIN="${BASH_BIN:-/bin/bash}"

sandbox="$(mktemp -d "${TMPDIR:-/tmp}/orbis-pyapp-cli-test.XXXXXX")"
cleanup() {
  rm -rf -- "$sandbox"
}
trap cleanup EXIT

fake_bin="${sandbox}/bin"
fake_tmp="${sandbox}/tmp"
mkdir -p "$fake_bin" "$fake_tmp"

cat > "${fake_bin}/cargo" <<'EOF'
#!/bin/sh
set -eu
printf '%s\n' "$@" > "${CARGO_LOG:?}"
EOF

cat > "${fake_bin}/curl" <<'EOF'
#!/bin/sh
set -eu
output=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --output)
      output="$2"
      shift 2
      ;;
    *) shift ;;
  esac
done
[ -n "$output" ]
: > "$output"
EOF

cat > "${fake_bin}/patch" <<'EOF'
#!/bin/sh
exit 0
EOF

cat > "${fake_bin}/rustc" <<'EOF'
#!/bin/sh
set -eu
[ "${1:-}" = "-vV" ]
printf '%s\n' 'rustc 1.91.0' 'host: aarch64-apple-darwin'
EOF

cat > "${fake_bin}/sha256sum" <<'EOF'
#!/bin/sh
set -eu
printf '%s  %s\n' "${ORBIS_PYAPP_SOURCE_SHA256:?}" "$1"
EOF

cat > "${fake_bin}/tar" <<'EOF'
#!/bin/sh
set -eu
destination=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    -C)
      destination="$2"
      shift 2
      ;;
    *) shift ;;
  esac
done
[ -n "$destination" ]
source_dir="${destination}/pyapp-v${ORBIS_PYAPP_VERSION:?}"
mkdir -p "$source_dir"
printf 'version = "%s"\n' "$ORBIS_PYAPP_VERSION" > "${source_dir}/Cargo.toml"
EOF

chmod +x "${fake_bin}"/*
export PATH="${fake_bin}:${PATH}"
export TMPDIR="$fake_tmp"
export CARGO_LOG="${sandbox}/cargo-args"

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

assert_line() {
  line_number="$1"
  expected="$2"
  actual="$(sed -n "${line_number}p" "$CARGO_LOG")"
  [ "$actual" = "$expected" ] || {
    fail "cargo argument ${line_number}: expected '${expected}', got '${actual}'"
  }
}

assert_common_install_args() {
  install_root="$1"
  assert_line 1 "install"
  assert_line 2 "--path"
  source_path="$(sed -n '3p' "$CARGO_LOG")"
  case "$source_path" in
    "${fake_tmp}"/orbis-pyapp-source.*/pyapp-v0.29.0) ;;
    *) fail "unexpected patched source path: ${source_path}" ;;
  esac
  assert_line 4 "--root"
  assert_line 5 "$install_root"
  assert_line 6 "--locked"
}

# This is the exact argument shape used by desktop-build.yml. On Bash 3.2,
# the former empty cargo_args array expansion failed under set -u before cargo
# could run.
no_options_root="${sandbox}/release-root"
"$BASH_BIN" --noprofile --norc \
  "${ROOT}/scripts/build-patched-pyapp.sh" --root "$no_options_root"
assert_common_install_args "$no_options_root"
[ "$(wc -l < "$CARGO_LOG" | tr -d ' ')" = "6" ] || {
  fail "no-option invocation forwarded unexpected cargo arguments"
}

options_root="${sandbox}/options-root"
"$BASH_BIN" --noprofile --norc \
  "${ROOT}/scripts/build-patched-pyapp.sh" --root "$options_root" --force --quiet
assert_common_install_args "$options_root"
assert_line 7 "--force"
assert_line 8 "--quiet"
[ "$(wc -l < "$CARGO_LOG" | tr -d ' ')" = "8" ] || {
  fail "option invocation did not forward exactly --force --quiet"
}

printf 'PASS: PyApp builder CLI works with zero or forwarded cargo options (%s)\n' \
  "$("$BASH_BIN" --version | sed -n '1p')"
