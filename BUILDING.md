# Building & forking ORBIS

ORBIS ships to users as a **signed, notarized `.dmg`** — the repo is private and
end users download the app, not the source (see
[Getting started](docs/tutorials/getting-started.md)). This doc is for
**contributors and forkers** building from source.

> **Platform:** an Apple-Silicon Mac (M1+) on a recent macOS is the only
> first-class native build target today — the audio engine is
> AVAudioEngine/CoreAudio. Intel Macs / Linux / Windows aren't supported for the
> native shell yet; the Python backend alone runs in Docker.

## Prerequisites

- **Apple-Silicon Mac**, recent macOS, **Xcode Command Line Tools**
  (`xcode-select --install` — provides `codesign`, `plutil`, `cc`).
- **Rust** (stable) + **Tauri CLI**: `cargo install tauri-cli` (gives `cargo tauri`).
- **Python 3.11** — the shipped sidecar runs on 3.11, so match your dev venv to
  it (`uv venv --python 3.11`, or pyenv).
- **uv** — the clean rebuild runs a version-pinned sdist frontend through
  `uv tool run`; the project virtualenv does not need the `build` package
  installed. Resolution follows your uv configuration and global cache, so a
  first build with a cold cache requires index access.
- **Bun** (`curl -fsSL https://bun.sh/install | bash`).
- `jq` (and `hdiutil`, which ships with macOS).

## Build & run — use the script, not `cargo tauri build`

```bash
./scripts/nuke-and-rebuild.sh --launch          # clean build (~90s) + launch with logs
./scripts/nuke-and-rebuild.sh --launch --tail   # + follow the sidecar log
```

**Don't run `cargo tauri build` directly to iterate** — it has two silent traps:

1. **It does NOT rebuild `web/dist`.** Tauri serves the React bundle as a static
   asset; only `bun run build` (in `web/`) regenerates it. Edit a `.tsx`, run
   `cargo tauri build`, and your new code doesn't ship — the app runs stale
   frontend. (This has cost real days.)
2. **Features matter.** A no-feature build compiles and runs but reports
   `audio.input_mode == "unsupported"` — a voice app with no voice. The
   supported build uses `--features native-audio,voice-processing`.

The script handles both, plus a **load-bearing build order** (frontend → sdist →
PyApp sidecar → stage → Tauri bundle) and wipes a set of caches that have each
silently broken sessions (the PyApp content-hashed env cache, lingering audio
sockets, WKWebView state under two bundle IDs, the append-only sidecar log). The
comment block at the top of `scripts/nuke-and-rebuild.sh` is the authoritative
list.

**Logs** (after `--launch`):
- Rust audio engine + sidecar lifecycle → `/tmp/orbis-tauri.stderr`
- Python pipeline → `~/Library/Logs/studio.protolabs.orbis/sidecar.log`

## What you actually need to run it

Once built, ORBIS runs **offline / standalone** — none of proto-labs' infra is
required. Building from source may need network access for uncached tool and
project dependencies.

- **LLM** — defaults to `http://localhost:8100/v1`; point it at any
  OpenAI-compatible endpoint, Ollama, or an MLX model during first-run setup or
  `Settings → Brain`.
- **Auth, Infisical, Stripe, Langfuse** — all optional. The paid-unlock gate
  defaults to **open** (`ORBIS_GATE=open`), so customization is unlocked.
- Config is `config/*.yaml` + a gitignored `.env` (copy `.env.example`). No
  secret is committed; real secrets stay in your `.env` or Infisical.

## Forking & rebranding — the find-replace checklist

ORBIS is wired to the **protoLabs** identity in a handful of places. To make it
your own:

| What | Where |
|---|---|
| Bundle id `studio.protolabs.orbis` | `src-tauri/tauri.conf.json`, `scripts/nuke-and-rebuild.sh`, `scripts/validate-macos-native-audio.sh`, `.github/workflows/desktop-build.yml` |
| Cargo package / 2nd bundle id `orbis-tauri` | `src-tauri/Cargo.toml` |
| Product name + copyright | `src-tauri/tauri.conf.json` |
| App-name string "ORBIS" | ~34× in `web/src/`, ~10× in `src-tauri/src/` |
| Site + in-app URLs `orbis.protolabs.studio` | `sites/marketing/`, in-app help links in `web/src/plugins/...` |
| Public DMG downloads — this repo's GitHub Releases (legacy `protoLabsAI/orbis-releases` for pre-v0.2.123 DMGs) | `.github/workflows/desktop-build.yml`, `sites/marketing/data/changelog.json` |
| CI repo guards `github.repository == 'protoLabsAI/ORBIS'` | `.github/workflows/{desktop-build,release,docker-publish,prepare-release}.yml` — these **no-op on a fork** until changed |
| Paywall (license pubkey + Stripe issuer) | `config/license_pubkey.pem`, repo var `ORBIS_LICENSE_PUBKEY`, `sites/license-issuer/`. To ship **free**: leave `ORBIS_GATE=open` and remove the Unlock UI. To run your own paid tier: `docs/internal/paywall-go-live-runbook.md`. |
| Legacy key prefix `pv_ak_` | `auth/users.py`, `auth/infisical.py`, `web/src/plugins/settings-panel/ApiKeyField.tsx` |

**Local builds need no Apple Developer account** — the script ad-hoc-signs. Only
signed/notarized **CI releases** require the Apple secrets (`APPLE_CERTIFICATE`,
`APPLE_SIGNING_IDENTITY`, `APPLE_TEAM_ID`, App Store Connect keys).

## Architecture in one paragraph

A thin **Tauri/Rust shell** (`src-tauri/`) launches and supervises a local
**Python sidecar** (`app.py` + `agent/`, `voice/`, `memory/`, `a2a_*.py`) — the
brain + Pipecat voice pipeline — and serves the **React frontend** (`web/src/`).
Everything runs on `127.0.0.1`; nothing is exposed to the network. The shell
proxies all sidecar HTTP/SSE through Rust (`reqwest`) because WKWebView can't
reliably stream HTTP bodies. The orb is a plugin-based Three.js visualizer
(`web/src/plugins/orb/`). For day-2 detail see `docs/internal/` and the project's
`STATUS.md` / `DECISIONS.md`.
