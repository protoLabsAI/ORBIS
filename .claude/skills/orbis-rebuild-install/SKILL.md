---
name: orbis-rebuild-install
description: Full clean rebuild of ORBIS — wipes web/dist, dist-sdist, src-tauri bundle, staged sidecar binary, pyapp env cache, WKWebView state, sidecar.log, and stale unix sockets, then rebuilds frontend → sdist → pyapp sidecar → tauri bundle in the correct order, and launches with stderr captured. Use when voice doesn't work, the orb shows weird state, "Load failed" appears anywhere in the UI, or anything feels off after a code change.
---

# /orbis-rebuild-install

## When to use

Run this when:

- The voice loop misbehaves after a code change.
- The wizard shows "Load failed" on `/api/*` fetches.
- Microphone permission acts strangely after a rebuild.
- The orb renders stale state from a previous version.
- You changed Python (sidecar) or Rust (Tauri) code and aren't sure what got picked up.
- The user says "anything feels off."

ORBIS has a long list of stale-cache failure modes that look identical to "broken code." Don't poke at it — run this skill.

## What it does

Source: `scripts/nuke-and-rebuild.sh` — the comment block at the top of the script is the authoritative spec.

**Wipes** (every cache that has ever silently broken a dev session):
- `web/dist` (NOT auto-rebuilt by `cargo tauri build` — this trap has cost real days)
- `dist-sdist` (old Python tarballs)
- `src-tauri/target/release/bundle` (old `.app`)
- `src-tauri/binaries/orbis-aarch64-apple-darwin` (staged sidecar)
- `~/Library/Application Support/pyapp/orbis` (env cache — wiped twice; once before build, once after to rule out content-hash collision)
- `~/Library/Caches/studio.protolabs.orbis`
- `~/Library/WebKit/<bid>/` and `~/Library/HTTPStorages/<bid>*/` for both `studio.protolabs.orbis` AND `orbis-tauri` bundle IDs (covers Finder-launch and terminal-launch paths)
- `~/Library/Logs/studio.protolabs.orbis/sidecar.log`
- `/tmp/pyapp-build-fix`, `/tmp/orbis-audio-*.sock`, `/tmp/orbis-tauri.{stderr,stdout}`

**Keeps** (would be wasteful to rebuild):
- `~/Library/Application Support/studio.protolabs.orbis/orbis.yaml` (user wizard answers, persona, llm config)
- `web/node_modules` (kept; `bun install --silent` runs to verify)
- `src-tauri/target/release/{deps,build}` (Rust incremental cache; keeps subsequent rebuilds at ~30s instead of 10min)
- `~/.cargo`

**Rebuild order is load-bearing — don't reorder:**

1. `bun install` + `bun run build` → fresh `web/dist`
2. `python -m build --sdist` → fresh `orbis-<version>.tar.gz`
3. `cargo install pyapp --force` → fresh sidecar
4. Stage sidecar binary at `src-tauri/binaries/orbis-aarch64-apple-darwin`
5. `cargo tauri build --features native-audio --bundles app`
6. Final pyapp env wipe (paranoia)
7. (with `--launch`) Launch from terminal so Rust stderr lands at `/tmp/orbis-tauri.stderr`

End-to-end ~70–80s on M1.

## How to invoke

```bash
cd /Users/kj/dev/ORBIS
./scripts/nuke-and-rebuild.sh                                # rebuild only
./scripts/nuke-and-rebuild.sh --launch                       # rebuild + launch
./scripts/nuke-and-rebuild.sh --launch --tail                # rebuild + launch + tail orbis.log
./scripts/nuke-and-rebuild.sh --voice-processing --launch    # Phase 2a: AVAudioEngine input
```

Stops on first error (`set -euo pipefail`).

## Where the logs go after `--launch`

- **Rust audio engine + socket lifecycle:** `/tmp/orbis-tauri.stderr`
- **Python pipecat pipeline:** `~/Library/Logs/studio.protolabs.orbis/sidecar.log`

The `/tmp/orbis-tauri.stderr` file is critical for diagnosing "no audio reaches Python" issues — it shows CPAL device selection, audio config, and `[audio/socket] Python connected`. **Do not** `open ORBIS.app` for diagnosis; that path discards stderr.

## Why a partial rebuild silently misleads you

Failure modes the script defends against:

- **`web/dist` is NOT rebuilt by `cargo tauri build`.** Tauri serves it as a static asset; only `bun run build` regenerates it. If you change a `.tsx` file and run `cargo tauri build`, your new code does not ship. You can rebuild the sidecar 10 times and the frontend will keep running stale code.
- **`pyapp` env cache is content-hashed.** Almost always produces a fresh env dir for a new sdist, but content-hash collisions have happened. Always wipe before launch.
- **WKWebView state outlives builds.** Stale service worker can intercept `/api/*` fetches with "Load failed" against the new sidecar. The two-bundle-ID issue (`studio.protolabs.orbis` via `open` vs `orbis-tauri` from terminal) means the cache lives in two places.
- **Old `/tmp/orbis-audio-<pid>.sock` sockets linger** after crashes and can confuse fresh runs.
- **Sidecar `.log` is append-only.** Wipe before relaunch so historical errors don't mislead diagnosis.

## After running

If voice still doesn't work after a clean rebuild, follow the diagnosis checklist in `CLAUDE.md` § "Diagnosing voice doesn't work" — check `/healthz` transport mode, Rust audio engine startup in `/tmp/orbis-tauri.stderr`, pyapp env path freshness in sidecar.log.

## Roadmap note

This script is Phase 1-and-2 tooling. Phase 3 of the migration (see `docs/native-audio-direction.md`) replaces the PyApp sidecar pattern with `protolabs-voice-core` from `protoLabsAI/protoApp` plus the `orbis-sidecar` WebSocket contract. At that point the pyapp-cache wipe becomes irrelevant and this script's nuke list shrinks substantially.
