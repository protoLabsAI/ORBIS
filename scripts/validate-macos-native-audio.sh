#!/usr/bin/env bash
# Validate the macOS native-audio ORBIS app after a local or CI build.
#
# This is the repeatable Apple Silicon soak harness for the production
# Mac path. It cannot grant TCC permissions for the user, but it does
# verify the bundle shape, optional release signing/notarization, launch
# health, AVAudioEngine voice-processing startup, first input tap,
# non-silent input while the tester speaks, and the sidecar's
# voice-processing mic-gain mode.
#
# Usage:
#   scripts/validate-macos-native-audio.sh
#   scripts/validate-macos-native-audio.sh --launch --duration 240 [--keep-running]
#   scripts/validate-macos-native-audio.sh --release --dmg path/to/ORBIS.dmg
#
# During --launch, speak to ORBIS and complete one short turn so the
# AVAudioEngine tap and LocalAudioTransport connection both produce log
# evidence.

set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

APP="${ROOT}/src-tauri/target/release/bundle/macos/ORBIS.app"
DMG=""
LAUNCH=0
RELEASE=0
DURATION=240
KEEP_RUNNING=0
APP_PID=""
DMG_MOUNT=""
MAIN_EXECUTABLE=""

usage() {
  sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --app)
      APP="${2:?--app requires a path}"
      shift 2
      ;;
    --dmg)
      DMG="${2:?--dmg requires a path}"
      shift 2
      ;;
    --launch)
      LAUNCH=1
      shift
      ;;
    --release)
      RELEASE=1
      shift
      ;;
    --duration)
      DURATION="${2:?--duration requires seconds}"
      shift 2
      ;;
    --keep-running)
      KEEP_RUNNING=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown arg: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

failures=0
REPORT="${ROOT}/macos-native-audio-validation.txt"
RUST_LOG_PATH="/tmp/orbis-tauri.validation.stderr"
STDOUT_LOG_PATH="/tmp/orbis-tauri.validation.stdout"
ORBIS_LOG_DIR="${HOME}/Library/Logs/studio.protolabs.orbis"
SIDECAR_LOG="${ORBIS_LOG_DIR}/sidecar.log"
ORBIS_LOG="${ORBIS_LOG_DIR}/orbis.log"

exec > >(tee "$REPORT") 2>&1

ts() { date "+%Y-%m-%d %H:%M:%S"; }
log() { printf '[%s] %s\n' "$(ts)" "$*"; }
pass() { log "PASS: $*"; }
fail() {
  log "FAIL: $*"
  failures=$((failures + 1))
}
warn() { log "WARN: $*"; }

dump_log_tail() {
  local label="$1"
  local file="$2"
  if [ -f "$file" ]; then
    echo
    log "last 120 lines from ${label}: ${file}"
    tail -n 120 "$file" || true
  else
    echo
    log "${label} log missing: ${file}"
  fi
}

cleanup() {
  if [ -n "${APP_PID}" ] && [ "${KEEP_RUNNING}" != "1" ]; then
    if kill -0 "${APP_PID}" >/dev/null 2>&1; then
      log "stopping launched app pid=${APP_PID}"
      kill "${APP_PID}" >/dev/null 2>&1 || true
    fi
  fi
  if [ -n "${DMG_MOUNT}" ] && [ -d "${DMG_MOUNT}" ]; then
    hdiutil detach "${DMG_MOUNT}" -quiet >/dev/null 2>&1 || true
    rmdir "${DMG_MOUNT}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    fail "missing required command: $1"
    return 1
  fi
}

log_has_pattern() {
  local pattern="$1"
  for file in "$ORBIS_LOG" "$SIDECAR_LOG" "$RUST_LOG_PATH"; do
    [ -f "$file" ] || continue
    if grep -Fq "$pattern" "$file"; then
      return 0
    fi
  done
  return 1
}

ready_url_from_logs() {
  for file in "$ORBIS_LOG" "$SIDECAR_LOG" "$RUST_LOG_PATH"; do
    [ -f "$file" ] || continue
    sed -n 's/.*ORBIS_READY \(http:\/\/[^[:space:]]*\).*/\1/p' "$file" | tail -1
  done | tail -1
}

mount_dmg_for_validation() {
  if [ -n "${DMG_MOUNT}" ] && [ -d "${DMG_MOUNT}" ]; then
    return 0
  fi
  DMG_MOUNT="$(mktemp -d /tmp/orbis-dmg-validation.XXXXXX)"
  if hdiutil attach "$DMG" -nobrowse -readonly -mountpoint "$DMG_MOUNT" >/tmp/orbis-dmg-attach.txt 2>&1; then
    pass "DMG mounted for contents validation"
    return 0
  fi
  fail "DMG mount failed: $(cat /tmp/orbis-dmg-attach.txt 2>/dev/null || true)"
  return 1
}

validate_dmg_contents() {
  local dmg_app="$DMG_MOUNT/ORBIS.app"
  if [ -d "$dmg_app" ]; then
    pass "DMG contains ORBIS.app"
    local dmg_info="$dmg_app/Contents/Info.plist"
    if [ -f "$dmg_info" ]; then
      local dmg_bid
      dmg_bid="$(plutil -extract CFBundleIdentifier raw "$dmg_info" 2>/dev/null || true)"
      if [ "$dmg_bid" = "studio.protolabs.orbis" ]; then
        pass "DMG app bundle identifier is stable"
      else
        fail "DMG app bundle identifier is unexpected: ${dmg_bid:-missing}"
      fi
      local dmg_executable_name
      local dmg_executable
      dmg_executable_name="$(plutil -extract CFBundleExecutable raw "$dmg_info" 2>/dev/null || true)"
      dmg_executable="$dmg_app/Contents/MacOS/$dmg_executable_name"
      if [ -n "$dmg_executable_name" ] && [ -x "$dmg_executable" ]; then
        local dmg_archs
        dmg_archs="$(lipo -archs "$dmg_executable" 2>/dev/null || true)"
        if [ "$dmg_archs" = "arm64" ]; then
          pass "DMG app executable is arm64"
        else
          fail "DMG app executable archs are unexpected: ${dmg_archs:-missing}"
        fi
      else
        fail "DMG app executable missing or not executable: ${dmg_executable:-unknown}"
      fi
    else
      fail "DMG app Info.plist missing"
    fi

    local dmg_sidecar="$dmg_app/Contents/MacOS/orbis"
    if [ -x "$dmg_sidecar" ]; then
      local dmg_sidecar_archs
      dmg_sidecar_archs="$(lipo -archs "$dmg_sidecar" 2>/dev/null || true)"
      if [ "$dmg_sidecar_archs" = "arm64" ]; then
        pass "DMG app sidecar is arm64"
      else
        fail "DMG app sidecar archs are unexpected: ${dmg_sidecar_archs:-missing}"
      fi
    else
      fail "DMG app sidecar missing or not executable: $dmg_sidecar"
    fi
    local resource
    for resource in config/orbis.example.yaml config/persona.md config/starter_orbs.yaml; do
      if [ -f "$dmg_app/Contents/Resources/$resource" ]; then
        pass "DMG app resource exists: $resource"
      else
        fail "DMG app resource missing: $resource"
      fi
    done
  else
    fail "DMG does not contain ORBIS.app at the volume root"
  fi
}

entitlement_is_true() {
  local plist="$1"
  local key="$2"
  python3 - "$plist" "$key" <<'PY'
import plistlib
import sys

plist_path, key = sys.argv[1], sys.argv[2]
with open(plist_path, "rb") as f:
    entitlements = plistlib.load(f)
raise SystemExit(0 if entitlements.get(key) is True else 1)
PY
}

entitlement_is_absent() {
  local plist="$1"
  local key="$2"
  python3 - "$plist" "$key" <<'PY'
import plistlib
import sys

plist_path, key = sys.argv[1], sys.argv[2]
with open(plist_path, "rb") as f:
    entitlements = plistlib.load(f)
raise SystemExit(0 if key not in entitlements else 1)
PY
}

validate_release_app_signing() {
  local app="$1"
  local label="$2"
  local details="/tmp/orbis-codesign-details-${label//[^A-Za-z0-9]/-}.txt"
  local entitlements="/tmp/orbis-entitlements-${label//[^A-Za-z0-9]/-}.plist"

  if codesign --verify --deep --strict --verbose=2 "$app"; then
    pass "$label codesign verify passed"
  else
    fail "$label codesign verify failed"
  fi

  # `codesign -d` increases the displayed signature detail once per `v`.
  # The Developer ID authority chain is not guaranteed at the single-v level.
  if codesign -dvv "$app" 2> "$details" \
      && grep -q "Authority=Developer ID Application:" "$details" \
      && grep -q "TeamIdentifier=" "$details"; then
    pass "$label Developer ID authority and TeamIdentifier present"
  else
    fail "$label Developer ID authority or TeamIdentifier missing"
  fi

  if codesign -d --entitlements :- "$app" > "$entitlements" 2>/dev/null; then
    if entitlement_is_true "$entitlements" "com.apple.security.device.audio-input"; then
      pass "$label signed entitlements include true audio input"
    else
      fail "$label signed entitlements missing true audio input"
    fi
    if entitlement_is_absent "$entitlements" "com.apple.security.device.camera"; then
      pass "$label signed entitlements do not include camera"
    else
      fail "$label signed entitlements unexpectedly include camera"
    fi
    if entitlement_is_true "$entitlements" "com.apple.security.network.client"; then
      pass "$label signed entitlements include true network client"
    else
      fail "$label signed entitlements missing true network client"
    fi
    if entitlement_is_true "$entitlements" "com.apple.security.network.server"; then
      pass "$label signed entitlements include true network server"
    else
      fail "$label signed entitlements missing true network server"
    fi
    if entitlement_is_absent "$entitlements" \
        "com.apple.security.cs.allow-unsigned-executable-memory"; then
      pass "$label signed entitlements do not allow unsigned executable memory"
    else
      fail "$label signed entitlements unexpectedly allow unsigned executable memory"
    fi
    if entitlement_is_absent "$entitlements" "com.apple.security.cs.disable-library-validation"; then
      pass "$label signed entitlements keep library validation enabled"
    else
      fail "$label signed entitlements unexpectedly disable library validation"
    fi
  else
    fail "$label could not read signed entitlements"
  fi

  if spctl --assess --type execute --verbose=4 "$app"; then
    pass "$label Gatekeeper execute assessment passed"
  else
    fail "$label Gatekeeper execute assessment failed"
  fi
  if xcrun stapler validate "$app"; then
    pass "$label has a valid stapled notarization ticket"
  else
    fail "$label stapler validation failed"
  fi
}

log "ORBIS macOS native-audio validation"
log "repo: $ROOT"
log "app: $APP"
[ -n "$DMG" ] && log "dmg: $DMG"
log "duration: ${DURATION}s"
echo

if [ "$(uname -s)" != "Darwin" ]; then
  fail "host OS must be macOS for live validation"
else
  pass "host OS is macOS"
fi

if [ "$(uname -m)" != "arm64" ]; then
  fail "host must be Apple Silicon arm64"
else
  pass "host is Apple Silicon arm64"
fi

require_cmd plutil || true
require_cmd codesign || true
require_cmd spctl || true
require_cmd xcrun || true
require_cmd lipo || true
require_cmd curl || true
require_cmd python3 || true
require_cmd hdiutil || true

if [ "$RELEASE" = "1" ] && [ -z "$DMG" ]; then
  fail "--release requires --dmg <path-to-dmg>"
fi

# A release is the DMG users download, not a build-tree app that Tauri may
# delete or a reconstructed copy whose nested signatures can differ. Always
# make the pristine app mounted from that DMG authoritative for release checks.
# Non-release `--dmg` validation keeps the same fallback when no app remains.
if { [ "$RELEASE" = "1" ] || [ ! -d "$APP" ]; } \
    && [ -n "$DMG" ] && [ -f "$DMG" ]; then
  if mount_dmg_for_validation; then
    DMG_APP="$DMG_MOUNT/ORBIS.app"
    if [ -d "$DMG_APP" ]; then
      APP="$DMG_APP"
      log "using authoritative app mounted from DMG for validation: $APP"
    else
      fail "DMG does not contain ORBIS.app at the volume root"
    fi
  fi
fi

if [ -d "$APP" ]; then
  pass "app bundle exists"
else
  fail "app bundle missing: $APP"
fi

INFO_PLIST="$APP/Contents/Info.plist"
if [ -f "$INFO_PLIST" ]; then
  pass "built Info.plist exists"
  if plutil -extract NSMicrophoneUsageDescription raw "$INFO_PLIST" >/dev/null 2>&1; then
    pass "built app has NSMicrophoneUsageDescription"
  else
    fail "built app missing NSMicrophoneUsageDescription"
  fi
  if plutil -extract NSCameraUsageDescription raw "$INFO_PLIST" >/dev/null 2>&1; then
    fail "built app unexpectedly has NSCameraUsageDescription"
  else
    pass "built app has no NSCameraUsageDescription"
  fi
  BID="$(plutil -extract CFBundleIdentifier raw "$INFO_PLIST" 2>/dev/null || true)"
  if [ "$BID" = "studio.protolabs.orbis" ]; then
    pass "bundle identifier is stable: $BID"
  else
    fail "unexpected bundle identifier: ${BID:-missing}"
  fi

  EXECUTABLE_NAME="$(plutil -extract CFBundleExecutable raw "$INFO_PLIST" 2>/dev/null || true)"
  EXECUTABLE="$APP/Contents/MacOS/$EXECUTABLE_NAME"
  if [ -n "$EXECUTABLE_NAME" ] && [ -x "$EXECUTABLE" ]; then
    MAIN_EXECUTABLE="$EXECUTABLE"
    pass "main executable exists and is executable: $EXECUTABLE_NAME"
    ARCHS="$(lipo -archs "$EXECUTABLE" 2>/dev/null || true)"
    if [ "$ARCHS" = "arm64" ]; then
      pass "main executable is arm64"
    else
      fail "main executable archs are unexpected: ${ARCHS:-missing}"
    fi
  else
    fail "main executable missing or not executable: ${EXECUTABLE:-unknown}"
  fi
else
  fail "built Info.plist missing"
fi

SIDECAR="$APP/Contents/MacOS/orbis"
if [ -x "$SIDECAR" ]; then
  SIDECAR_BYTES="$(wc -c < "$SIDECAR" | tr -d ' ')"
  if [ "${SIDECAR_BYTES:-0}" -gt 1000000 ]; then
    pass "bundled PyApp sidecar exists and is executable (${SIDECAR_BYTES} bytes)"
  else
    fail "bundled PyApp sidecar is too small: ${SIDECAR_BYTES:-missing} bytes"
  fi
  SIDECAR_ARCHS="$(lipo -archs "$SIDECAR" 2>/dev/null || true)"
  if [ "$SIDECAR_ARCHS" = "arm64" ]; then
    pass "bundled PyApp sidecar is arm64"
  else
    fail "bundled PyApp sidecar archs are unexpected: ${SIDECAR_ARCHS:-missing}"
  fi
else
  fail "bundled PyApp sidecar missing or not executable: $SIDECAR"
fi

for resource in config/orbis.example.yaml config/persona.md config/starter_orbs.yaml; do
  if [ -f "$APP/Contents/Resources/$resource" ]; then
    pass "bundled resource exists: $resource"
  else
    fail "bundled resource missing: $resource"
  fi
done

if [ -n "$DMG" ]; then
  if [ -f "$DMG" ]; then
    pass "DMG exists"
    if mount_dmg_for_validation; then
      validate_dmg_contents
    fi
  else
    fail "DMG missing: $DMG"
  fi
fi

if [ "$RELEASE" = "1" ]; then
  log "release signing/notarization checks"
  if [ -n "$DMG_MOUNT" ] && [ "$APP" = "$DMG_MOUNT/ORBIS.app" ] && [ -d "$APP" ]; then
    validate_release_app_signing "$APP" "DMG app"
  else
    fail "authoritative DMG app unavailable for release signing checks"
  fi

  if [ -f "$DMG" ]; then
    if spctl --assess --type open --context context:primary-signature --verbose=4 "$DMG"; then
      pass "Gatekeeper open assessment passed for DMG"
    else
      fail "Gatekeeper open assessment failed for DMG"
    fi
    if xcrun stapler validate "$DMG"; then
      pass "DMG has a valid stapled notarization ticket"
    else
      fail "DMG stapler validation failed"
    fi
  else
    fail "DMG missing: $DMG"
  fi
fi

if [ "$LAUNCH" = "1" ]; then
  log "launching app for live audio validation"
  if [ -z "$MAIN_EXECUTABLE" ] || [ ! -x "$MAIN_EXECUTABLE" ]; then
    fail "cannot launch because the verified app executable is missing"
  fi
  mkdir -p "$ORBIS_LOG_DIR"
  rm -f "$RUST_LOG_PATH" "$STDOUT_LOG_PATH"

  pkill -9 -f "orbis-tauri" 2>/dev/null || true
  pkill -9 -f "ORBIS.app" 2>/dev/null || true
  sleep 1
  : > "$SIDECAR_LOG"
  : > "$ORBIS_LOG"
  pass "truncated app logs so validation only sees this launch"

  if [ -n "$MAIN_EXECUTABLE" ] && [ -x "$MAIN_EXECUTABLE" ]; then
    RUST_LOG=info "$MAIN_EXECUTABLE" \
      >"$STDOUT_LOG_PATH" \
      2>"$RUST_LOG_PATH" &
    APP_PID=$!
    pass "launched app pid=$APP_PID executable=$MAIN_EXECUTABLE"
  fi
  log "while this runs, grant microphone access if prompted, speak normally, and complete one short turn"
  log "rust stderr: $RUST_LOG_PATH"
  log "sidecar log: $SIDECAR_LOG"
  log "orbis log: $ORBIS_LOG"

  deadline=$(( $(date +%s) + DURATION ))
  saw_engine=0
  saw_tap=0
  saw_audible=0
  saw_mode=0
  saw_transport_connected=0
  saw_mic_frame=0
  saw_speaker_frame=0
  saw_rust_playback_frame=0
  saw_ready=0
  ready_url=""
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if [ -n "$APP_PID" ] && ! kill -0 "$APP_PID" >/dev/null 2>&1; then
      fail "launched app exited before live validation completed"
      break
    fi
    if [ "$saw_engine" = "0" ] && log_has_pattern "[voice-processing] engine started"; then
      saw_engine=1
      pass "AVAudioEngine voice-processing engine started"
    fi
    if [ "$saw_tap" = "0" ] && log_has_pattern "[voice-processing] first input tap"; then
      saw_tap=1
      pass "first AVAudioEngine input tap observed"
    fi
    if [ "$saw_audible" = "0" ] && log_has_pattern "[voice-processing] input became audible"; then
      saw_audible=1
      pass "AVAudioEngine input produced non-silent audio"
    fi
    if [ "$saw_mode" = "0" ] && log_has_pattern "audio_input_mode=voice_processing mic_gain=1.00"; then
      saw_mode=1
      pass "sidecar is in voice_processing mode with unity mic gain"
    fi
    if [ "$saw_transport_connected" = "0" ] && log_has_pattern "[local_transport] connected"; then
      saw_transport_connected=1
      pass "Python local audio transport connected to native socket"
    fi
    if [ "$saw_mic_frame" = "0" ] && log_has_pattern "[local_transport] first mic frame"; then
      saw_mic_frame=1
      pass "Python local audio transport received a mic frame"
    fi
    if [ "$saw_speaker_frame" = "0" ] && log_has_pattern "[local_transport] first speaker frame"; then
      saw_speaker_frame=1
      pass "Python local audio transport sent a speaker frame"
    fi
    if [ "$saw_rust_playback_frame" = "0" ] && log_has_pattern "[audio/socket] first playback frame received"; then
      saw_rust_playback_frame=1
      pass "Rust audio socket received a playback frame"
    fi
    if [ "$saw_ready" = "0" ] && log_has_pattern "ORBIS_READY http://"; then
      saw_ready=1
      ready_url="$(ready_url_from_logs)"
      pass "sidecar reached ORBIS_READY: ${ready_url:-url not parsed}"
    fi
    [ "$saw_engine$saw_tap$saw_audible$saw_mode$saw_transport_connected$saw_mic_frame$saw_speaker_frame$saw_rust_playback_frame$saw_ready" = "111111111" ] && break
    sleep 2
  done

  [ "$saw_engine" = "1" ] || fail "did not observe AVAudioEngine voice-processing startup"
  [ "$saw_tap" = "1" ] || fail "did not observe first AVAudioEngine input tap"
  [ "$saw_audible" = "1" ] || fail "did not observe non-silent AVAudioEngine input"
  [ "$saw_mode" = "1" ] || fail "did not observe sidecar voice_processing unity-gain mode"
  [ "$saw_transport_connected" = "1" ] || fail "did not observe Python local audio transport socket connection"
  [ "$saw_mic_frame" = "1" ] || fail "did not observe Python local audio transport receiving mic frames"
  [ "$saw_speaker_frame" = "1" ] || fail "did not observe Python local audio transport sending speaker frames"
  [ "$saw_rust_playback_frame" = "1" ] || fail "did not observe Rust audio socket receiving playback frames"
  [ "$saw_ready" = "1" ] || fail "did not observe ORBIS_READY"

  if [ "$saw_ready" = "1" ]; then
    ready_url="${ready_url:-$(ready_url_from_logs)}"
    if [ -n "$ready_url" ]; then
      health_url="${ready_url%/}/healthz"
      if health_json="$(curl -fsS --max-time 5 "$health_url")"; then
        pass "healthz responded at $health_url"
        if python3 - "$health_json" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
if payload.get("status") != "ok":
    raise SystemExit(1)
PY
        then
          pass "healthz status is ok"
        else
          fail "healthz status was not ok: $health_json"
        fi
        if python3 - "$health_json" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
if payload.get("audio", {}).get("transport") != "native":
    raise SystemExit(1)
PY
        then
          pass "healthz reports audio.transport=native"
        else
          fail "healthz did not report audio.transport=native: $health_json"
        fi
        if python3 - "$health_json" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
if payload.get("audio", {}).get("input_mode") != "voice_processing":
    raise SystemExit(1)
PY
        then
          pass "healthz reports audio.input_mode=voice_processing"
        else
          fail "healthz did not report audio.input_mode=voice_processing: $health_json"
        fi
        if python3 - "$health_json" <<'PY'
import json
import math
import sys

payload = json.loads(sys.argv[1])
gain = payload.get("audio", {}).get("mic_gain")
if not isinstance(gain, (int, float)) or not math.isclose(float(gain), 1.0, abs_tol=0.001):
    raise SystemExit(1)
PY
        then
          pass "healthz reports audio.mic_gain=1.0"
        else
          fail "healthz did not report audio.mic_gain=1.0: $health_json"
        fi
        if python3 - "$health_json" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
if payload.get("audio", {}).get("socket_configured") is not True:
    raise SystemExit(1)
PY
        then
          pass "healthz reports native audio socket configured"
        else
          fail "healthz did not report native audio socket configured: $health_json"
        fi
        if python3 - "$health_json" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
if payload.get("audio", {}).get("socket_connected") is not True:
    raise SystemExit(1)
PY
        then
          pass "healthz reports Python audio socket connected"
        else
          fail "healthz did not report Python audio socket connected: $health_json"
        fi
        if python3 - "$health_json" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
if payload.get("audio", {}).get("pipeline_running") is not True:
    raise SystemExit(1)
PY
        then
          pass "healthz reports native voice pipeline running"
        else
          fail "healthz did not report native voice pipeline running: $health_json"
        fi
        if python3 - "$health_json" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
frames = payload.get("audio", {}).get("mic_frames_received")
if not isinstance(frames, int) or frames <= 0:
    raise SystemExit(1)
PY
        then
          pass "healthz reports mic_frames_received > 0"
        else
          fail "healthz did not report mic_frames_received > 0: $health_json"
        fi
        if python3 - "$health_json" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
frames = payload.get("audio", {}).get("speaker_frames_sent")
if not isinstance(frames, int) or frames <= 0:
    raise SystemExit(1)
PY
        then
          pass "healthz reports speaker_frames_sent > 0"
        else
          fail "healthz did not report speaker_frames_sent > 0: $health_json"
        fi
      else
        fail "healthz request failed: $health_url"
      fi
    else
      fail "could not parse ORBIS_READY URL from logs"
    fi
  fi

  if grep -Fq "native audio engine failed" "$RUST_LOG_PATH" "$ORBIS_LOG" "$SIDECAR_LOG" 2>/dev/null; then
    fail "native audio engine failure appeared in logs"
  else
    pass "no native audio engine failure found in logs"
  fi
  if grep -Fq "microphone permission is" "$RUST_LOG_PATH" "$ORBIS_LOG" "$SIDECAR_LOG" 2>/dev/null; then
    fail "microphone permission failure appeared in logs"
  else
    pass "no microphone permission failure found in logs"
  fi
fi

echo
if [ "$failures" -eq 0 ]; then
  pass "macOS native-audio validation passed"
else
  log "FAIL: macOS native-audio validation failed with $failures issue(s)"
fi

if [ "$failures" -ne 0 ]; then
  dump_log_tail "Rust stderr" "$RUST_LOG_PATH"
  dump_log_tail "Rust stdout" "$STDOUT_LOG_PATH"
  dump_log_tail "sidecar" "$SIDECAR_LOG"
  dump_log_tail "orbis" "$ORBIS_LOG"
  echo "validation report: $REPORT" >&2
  exit 1
fi

echo "validation report: $REPORT"
