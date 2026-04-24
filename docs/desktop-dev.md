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
On Linux: `libwebkit2gtk-4.1-dev` + `build-essential` + `curl` +
`file` + `libayatana-appindicator3-dev` + `librsvg2-dev`.
On Windows: WebView2 (shipped with Windows 11; bundled with Tauri
installer on Windows 10).

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
during dev. Two options:

1. **Skip sidecar spawn** by setting `ORBIS_DEV_URL=http://127.0.0.1:7866`
   when launching (handled in `src-tauri/src/lib.rs` dev branch — WIP).
2. **Drop a stub script** at `src-tauri/binaries/orbis-<host-target>`
   that just prints the ready line and sleeps. Simple, no code change
   needed.

Route #2 one-liner on Linux/macOS (uses `rustc` to pick the host's
full Rust target triple — `uname -m` gives e.g. `arm64` on macOS but
Rust expects `aarch64-apple-darwin`):

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

# 4. Bundle
cargo tauri build
```

Installer lands under `src-tauri/target/release/bundle/`:

- `dmg/` on macOS
- `msi/` on Windows
- `appimage/` on Linux

## CI

`.github/workflows/desktop-build.yml` runs this flow on semver tags:
one job per OS produces both the raw `orbis-<target>` sidecar binary
and a native installer (`.dmg` / `.msi` / `.AppImage`). Both are
attached to the matching GitHub release.

Installers are **unsigned** currently — PR 4 of the desktop-prep arc
wires Developer ID (macOS) + Authenticode (Windows) + Tauri updater
keys. First-install UX will have a Gatekeeper / SmartScreen warning
until then.

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
