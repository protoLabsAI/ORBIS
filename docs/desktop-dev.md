# Desktop shell — development

The Tauri 2 shell at `src-tauri/` wraps the ORBIS Python backend as a
native desktop app. It spawns `binaries/orbis-<target>` on boot, reads
stdout for the `ORBIS_READY http://...` line defined in
`app.py:main()`, and navigates the webview to that URL.

## Requirements

- **Rust + cargo** on `PATH` (`curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh`)
- **tauri-cli** (`cargo install tauri-cli --version '^2' --locked`)
- Python 3.11 + a GPU (or `ORBIS_ALLOW_CPU=1`) — same as the standalone
  backend

On macOS: Xcode Command Line Tools (`xcode-select --install`).
Linux and Windows desktop builds are planned after the Mac native-audio
build is stable; Docker self-host remains the current non-Mac path.

## Two ways to run the shell

### Against a locally-running Python backend (fast iteration)

Skip the sidecar bundling entirely — point the shell at your
dev-mode ORBIS:

```sh
# Terminal 1 — the backend
python app.py --port 7866

# Terminal 2 — the Tauri shell
cd src-tauri
cargo run
```

The shell will spawn a bundled binary by default, which won't exist
during dev. Drop a stub script at `src-tauri/binaries/orbis-<host-target>`
that prints the ready line and sleeps.

One-liner on Linux/macOS (uses `rustc` to pick the host's full Rust target
triple — `uname -m` gives e.g. `arm64` on macOS but Rust expects
`aarch64-apple-darwin`):

```sh
mkdir -p src-tauri/binaries
TARGET=$(rustc -vV | sed -n 's/host: //p')
cat > src-tauri/binaries/orbis-${TARGET} <<'EOF'
#!/usr/bin/env bash
echo "ORBIS_READY http://127.0.0.1:7866"
sleep infinity
EOF
chmod +x src-tauri/binaries/orbis-*
```

Then `cargo run` in `src-tauri/` launches the shell + navigates the
webview to your locally-running `python app.py`.

### Full bundle flow (what CI produces)

Exercises the PyApp binary + Tauri packaging end-to-end. Slower —
each iteration rebuilds the sidecar.

```sh
# 1. Build the PyApp sidecar binary (see docs/build-desktop-binary.md)
scripts/build-desktop-binary.sh

# 2. Stage it where Tauri expects it
mkdir -p src-tauri/binaries
cp dist/orbis-* src-tauri/binaries/

# 3. Generate icons (first run only — idempotent, safe to re-run)
cd src-tauri
cargo tauri icon ../web/public/pwa-512.png

# 4. Bundle the current Mac production path
cargo tauri build --features native-audio,voice-processing
```

The current production build emits a DMG under
`src-tauri/target/release/bundle/dmg/`.

For the full clean local flow, prefer:

```sh
scripts/nuke-and-rebuild.sh --launch --tail
```

That script now builds the same `native-audio,voice-processing` Mac
path as CI. Use `--legacy-cpal` only as a temporary fallback while
debugging a device-specific AVAudioEngine issue.

To exercise local installer packaging too:

```sh
scripts/nuke-and-rebuild.sh --dmg
```

`--dmg` builds and stable-ad-hoc-signs `ORBIS.app` first, then creates
a local DMG whose volume root contains `ORBIS.app`. CI release DMGs are
still produced by Tauri with Developer ID signing and notarization.

## CI

`.github/workflows/desktop-build.yml` currently runs this flow on
semver tags for macOS arm64: it produces both the raw
`orbis-aarch64-apple-darwin` sidecar binary and a native `.dmg`. Both
are attached to the matching GitHub release. Linux and Windows jobs
can be reintroduced once their native-audio packaging paths are ready.

Manual dispatch builds can emit unsigned test DMGs. Semver tag builds
require the full Apple secret set, produce a Developer-ID-signed and
notarized DMG, run the release validation harness, and upload
`macos-native-audio-validation.txt` as a workflow artifact. The
desktop job waits for the parallel Docker release workflow to create
the GitHub Release, then attaches the sidecar and DMG; if the Release
does not appear within 30 minutes, the desktop build fails instead of
silently leaving the DMG only as a workflow artifact.

`.github/workflows/native-audio-preflight.yml` runs on PRs and main
pushes before release prep. It runs the macOS release config guardrail,
builds the web app, checks Rust formatting, and runs both default and
`native-audio,voice-processing` Rust test sets with a dummy sidecar.
It also has a macOS arm64 job that compiles/tests the Apple-specific
AVAudioEngine voice-processing path without doing a full signed DMG
release build.

For a local host-side equivalent before moving work to a Mac, run:

```sh
scripts/preflight-native-audio-host.sh
```

## Apple Silicon native-audio validation

Before building a release candidate, run the host-portable static
guardrail:

```sh
scripts/check-macos-release-config.py
```

It checks the source configuration that must stay true for the Mac
production path: DMG-only bundling, hardened runtime, microphone-only
permissions, voice-processing sidecar mode, unity mic gain, and CI's
macOS arm64 feature set.

After a Mac build, run the validation harness on Apple Silicon:

```sh
scripts/validate-macos-native-audio.sh --launch --duration 240
```

During the launch window, grant microphone access if prompted, speak
normally, and complete one short turn. The script writes
`macos-native-audio-validation.txt`, truncates app logs before launch
so stale success lines cannot pass the soak, and fails if it cannot observe
the built app's arm64 executable, the bundled arm64 PyApp sidecar,
the sidecar's arm64 architecture, AVAudioEngine voice-processing startup, the first input tap, the
sidecar's `voice_processing`/unity-gain mode, non-silent AVAudioEngine input
while you speak, the Python local audio transport socket connection,
Python-side mic frame receipt, Python-side speaker frame send, Rust-side
playback frame receipt, `ORBIS_READY`, or `/healthz` reporting
`audio.transport: native`, `audio.input_mode: voice_processing`, and
`audio.mic_gain: 1.0`, `audio.socket_configured: true`, and
`audio.socket_connected: true`, `audio.pipeline_running: true`, and
`audio.mic_frames_received > 0`, and `audio.speaker_frames_sent > 0`.
By default it stops the launched app when validation exits; add
`--keep-running` if you want to continue the session afterward.

For a signed release artifact, add `--release --dmg <path-to-dmg>` to
also verify Developer ID signing, embedded entitlements, Gatekeeper
assessment, stapled notarization tickets, the narrow entitlement set
(microphone + network, no camera or broad code-signing exceptions), and the
mounted DMG contents (`ORBIS.app`, arm64 main executable, arm64 PyApp sidecar,
and bundled first-run config resources). Release mode applies the app
signing/notarization checks to both the build-tree `.app` and the
`ORBIS.app` mounted from the DMG, so the exact installed payload is covered.
When the local build `.app` is absent, the harness validates the app mounted
from the DMG directly.

Unsigned manual CI builds and local `--dmg` builds should still run the same
harness without `--release`:

```sh
scripts/validate-macos-native-audio.sh --dmg path/to/ORBIS.dmg
```

## Dev loop tips

- Rust compile for the shell is slow on cold builds (~3 min) but
  incremental; rebuilds under changes to `src-tauri/src/` finish in
  seconds.
- The Python sidecar's output shows up in the Rust process's stderr
  — run with `RUST_LOG=info` to see the full stream.
- Kill `cargo run` / `cargo tauri dev` with Ctrl-C; the sidecar
  SIGKILLs cleanly via the `RunEvent::ExitRequested` handler.
- If you change `tauri.conf.json`, `cargo build -p tauri-build`
  regenerates the compile-time capability + schema artifacts.

## See also

- [docs/build-desktop-binary.md](./build-desktop-binary.md) — PyApp
  sidecar build
- [Tauri 2 docs](https://v2.tauri.app/)
- The protoApp repo at `protoLabsAI/protoApp` for a reference Tauri 2
  shell using the in-process engine substrate (different product
  shape; not a direct template for ORBIS)
