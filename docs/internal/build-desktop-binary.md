# Build the desktop sidecar binary

ORBIS's Tauri desktop app ships a **self-contained `orbis-<target>`
sidecar binary** — the Python backend, Pipecat pipeline, Whisper,
Kokoro, and all deps wrapped in a Rust launcher via [PyApp]. The
Tauri shell (see PR 3) spawns it on app boot and reads its stdout
for the `ORBIS_READY http://...` line.

This page covers building that binary locally. CI does the same
thing on every semver tag — see
[`.github/workflows/desktop-build.yml`](../.github/workflows/desktop-build.yml).

## Requirements

- **Rust + cargo** on `PATH`. One-liner install:
  ```sh
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
  ```
- **Python 3.11** — only used to read `pyproject.toml`; the built
  binary ships its own isolated Python interpreter.
- For Windows / Linux: an NVIDIA driver ≥ 570 at runtime (matches the
  cu128 torch wheel the binary bundles). For macOS: an M1-series or
  newer Apple Silicon chip for MPS. Builds succeed on any host; the
  *runtime* hardware check (`agent/hardware.py`) is what hard-fails.

## Quick start

```sh
# Builds ./dist/orbis-<target>. Target is auto-detected from your host.
scripts/build-desktop-binary.sh

# Smoke-test — run with CPU opt-in so the hardware probe doesn't
# refuse to start on a dev box without CUDA/MPS.
ORBIS_ALLOW_CPU=1 ./dist/orbis-* --port 0
```

Expected first-run behavior: the binary downloads Python plus the project's
wheel dependencies and installs them with PyApp's UV path. On macOS, PyApp's
versioned environment lives under `~/Library/Application Support/pyapp/orbis/`;
PyApp caches its pinned UV executable under `~/Library/Caches/pyapp/uv/`, and
UV's shared package cache defaults to `~/.cache/uv/`. `ORBIS_CACHE_DIR` controls
model/application caches, not these installer locations. A later ORBIS version
creates a new PyApp environment but reuses UV's cache and APFS clones, so it
does not download and physically duplicate the whole dependency set.

All release and local builders source `scripts/pyapp-installer-env.sh`, which
pins PyApp and UV. Treat those as release inputs: update the shared pins only
after rebuilding a sidecar and repeating the benchmark/boot checks documented
in [the UV benchmark](./pyapp-uv-benchmark.md).

You should see on stdout, roughly:

```
ORBIS_READY http://127.0.0.1:54123
INFO     [boot] accelerator: cpu   # or cuda / mps on real hardware
...
```

## Per-platform notes

### macOS (Apple Silicon)

The default `pip install torch` wheel includes MPS support. No index
override needed. The binary is ~700 MB installed size after first-run
unpack.

### Linux / Windows (NVIDIA)

The CI workflow + `scripts/build-desktop-binary.sh` add
`--extra-index-url https://download.pytorch.org/whl/cu128` so pip
picks the `torch==X.Y.Z+cu128` wheels. That's ~2.5 GB of CUDA
runtime libraries (cublas, cudnn, nccl, triton, …) on top of torch
itself — total ~3 GB after unpack.

If your runner / dev box has less than ~6 GB free, the first-run
install will fail with an `[Errno 28] No space left on device`.
We hit this in CI before the disk-space fix
([#10](https://github.com/protoLabsAI/ORBIS/pull/10)); the same
`jlumbroso/free-disk-space` step runs in the desktop-build workflow.

## CI integration

The workflow fires on every semver tag pushed by `release.yml`.
Resulting binaries are:

- Uploaded as GitHub release assets attached to the matching tag
- Uploaded as 14-day workflow artifacts for iteration

Naming follows Tauri's `externalBin` target-suffix convention so the
`src-tauri` config in PR 3 can reference a single `binaries/orbis`
entry that Tauri resolves per-target automatically:

```
orbis-aarch64-apple-darwin
orbis-x86_64-pc-windows-msvc.exe
orbis-x86_64-unknown-linux-gnu
```

## Troubleshooting

**"cargo install pyapp … failed"** — make sure Rust is up to date
(`rustup update stable`).

**First launch has no network** — the embedded ORBIS sdist is not an offline
bundle of its third-party dependencies. A first-ever install still needs the
network. Once UV's shared cache is warm, the same environment can be recreated
offline.

**"torch 2.x.y+cu128 is not compatible with this platform"** — you're
on a non-Linux/Windows host. Drop the `--extra-index-url` (the script
does that for macOS); you'll get the default `torch` wheel.

**Binary runs but exits with code 2 immediately** — the hardware probe
refused to start. Either the host lacks a supported accelerator, or
you want to opt into CPU for dev: `ORBIS_ALLOW_CPU=1 ./orbis-*`.

**"ORBIS_READY" line never appears** — check stderr for a Python
traceback. The most common cause is a dep that wasn't in
`pyproject.toml` and therefore didn't land in the PyApp bundle.

[PyApp]: https://ofek.dev/pyapp/
