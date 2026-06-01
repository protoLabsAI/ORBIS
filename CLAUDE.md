# ORBIS — agent operating notes

## Read first
- **STATUS.md** — point-in-time snapshot of current state, active incidents, and how to pick up. Read this before digging into code on any resume.
- **DECISIONS.md** — architectural choices and the reasoning behind them. Don't re-litigate without reading the entry first.
- **HANDOFF.md** — QA checklist + open design questions + ordered next steps.
- **`docs/internal/native-audio-direction.md`** — comprehensive guide for the Apple-Silicon-only direction (locked 2026-04-28) and the 4-phase migration plan (strip web → AVAudioEngine → protoApp consolidation → iOS).

## Direction (locked 2026-04-28)

**Apple Silicon Mac is the only first-class platform. iOS / iPad is the planned secondary target. Web / PWA / browser is dropped as a supported runtime.** Don't add features that re-introduce browser/PWA support, WebRTC paths, or `getUserMedia` flows. The dual-transport `AUDIO_TRANSPORT=native|webrtc` architecture is being phased out — see DECISIONS.md amendment 2026-04-28 and `docs/internal/native-audio-direction.md`.

When making changes, ask: *does this make Phase 1 (strip web) easier or harder?* If harder, push back. If you're touching `voice/local_transport.py`, `voice/native_bargein.py`, `voice/sse_bus.py`, or `src-tauri/src/audio/{engine,socket,aec}.rs`, also check `docs/internal/native-audio-direction.md` § "What stays in Phase 1" — these files have a planned deletion / migration in Phase 2 or 3, so don't sink effort into improving things that go away.

## Dev flow — when in doubt, nuke and rebuild

ORBIS has **multiple stale-cache failure modes** that look identical to "broken code" and have eaten entire sessions. If voice doesn't work, the orb shows weird state, or anything feels off after a code change — **don't poke at it, run the nuke script**.

```bash
./scripts/nuke-and-rebuild.sh --launch --tail
```

### Why a partial rebuild silently misleads you

- **`web/dist` is NOT rebuilt by `cargo tauri build`.** The Tauri Rust shell loads the React bundle from `web/dist`, but only `bun run build` (run separately, in `web/`) regenerates it. If you change a `.tsx` file and run `cargo tauri build`, your new code does not ship. You can rebuild the sidecar 10 times and the frontend will keep running stale code. This trap has cost real days.
- **`pyapp` env cache (`~/Library/Application Support/pyapp/orbis/<hash>/<version>/`) is content-hashed.** A new sdist almost always produces a new env dir, so the new Python source IS executed — but content-hash collisions have happened. Always wipe this cache after a sidecar rebuild before launching.
- **Old unix sockets at `/tmp/orbis-audio-<pid>.sock` linger** after crashes and confuse fresh runs. Wipe them.
- **Sidecar `.log` is append-only.** When diagnosing a session, filter by `ORBIS_READY` markers or current timestamp — historical errors from previous launches will mislead you. Better: wipe the log before relaunch (`scripts/nuke-and-rebuild.sh` does this).

### What the nuke script does
Source: `scripts/nuke-and-rebuild.sh` — the comment block at the top is authoritative.

Nukes: `web/dist`, `dist-sdist`, `src-tauri/target/release/bundle`, the staged sidecar binary, `~/Library/Application Support/pyapp/orbis`, `~/Library/Caches/studio.protolabs.orbis`, sidecar.log, `/tmp/pyapp-build-fix`, `/tmp/orbis-audio-*.sock`.

Keeps: user config (`orbis.yaml` in app support), `web/node_modules`, Rust incremental cache in `src-tauri/target/release/{deps,build}`.

Rebuild order is load-bearing: frontend → sdist → pyapp sidecar → stage → tauri bundle → final pyapp wipe → launch. Don't reorder.

### Launching with logs visible
Don't `open ORBIS.app` for diagnosis — Rust-side `log::info!` (CPAL device, audio/socket events) goes to stderr, which `open` discards. Instead:

```bash
RUST_LOG=info src-tauri/target/release/bundle/macos/ORBIS.app/Contents/MacOS/orbis-tauri \
  >/tmp/orbis-tauri.stdout 2>/tmp/orbis-tauri.stderr &
```

Then watch:
- `/tmp/orbis-tauri.stderr` — Rust audio engine, socket server, sidecar lifecycle
- `~/Library/Logs/studio.protolabs.orbis/sidecar.log` — Python pipecat pipeline

The `--launch` flag of the nuke script does this for you. Add `--tail` to follow the sidecar log inline.

## Diagnosing voice doesn't work

Native-mode pipeline is up but no voice loop activity (no STT, no transcripts, no LLM calls):

1. **Confirm transport** — `curl -s http://127.0.0.1:<port>/healthz | jq .audio.transport`. Must be `"native"`. Find the port via `lsof -nP -iTCP -sTCP:LISTEN | grep python`.
2. **Confirm Rust audio engine started** — `grep '\[audio\]' /tmp/orbis-tauri.stderr` should show input device, output device, configs, and `[audio/socket] Python connected`.
3. **Confirm pyapp env is fresh** — the path in sidecar.log should reference the current pyproject version. If you see an old version dir, the cache wasn't wiped.
4. **If all three pass and voice still dead**: the frontend may be running the WebRTC code path and holding the mic via `getUserMedia`. Symptom: macOS mic permission popover when you double-click the orb. Fix: rebuild `web/dist` (the nuke script does this).

## Conventions / tripwires (don't change without reading)

See STATUS.md § "Known tripwires" for the full list. Highlights:
- `append_to_context=False` on every out-of-band TTSSpeakFrame — without it the LLM riffs on its own fillers.
- `cancel_on_interruption=True` default for sync tools.
- `cancel_on_idle_timeout=False` in native mode — Pipecat's 5-min default tears down the persistent pipeline mid-wizard.
- Filler/backchannel LLM URL must follow the persona, not the env `LLM_URL` default — otherwise it spam-retries `localhost:8100/v1` connection errors forever.
- M1 internal mic without AGC delivers ~0.013 RMS for normal speech — `MIC_GAIN=8` in `voice/local_transport.py` is required until Phase 2 (AVAudioEngine).
- WebView state outlives builds — `~/Library/WebKit/<bid>/` for both bundle IDs (`studio.protolabs.orbis` AND `orbis-tauri`) caches stale frontend bundles. Phase 1 replaces this with `Webview::clear_all_browsing_data()`.
- Whisper hallucinates on silence — phrase blocklist + `STT_MIN_RMS` gate filter them. Goes away in Phase 2.
- Backchannel + MicroAck are off by default in native mode — they false-trigger on bot tail without real AEC.
- FTS5 is required in the SQLite build — ORBIS refuses to start without it.
- Stripe webhook endpoint is unauth on purpose; signature is the auth.

## Where things live

- Python sidecar: `app.py` + `agent/`, `auth/`, `memory/`, `voice/`, `a2a/`
- Tauri shell: `src-tauri/src/`
  - Native audio engine: `src-tauri/src/audio/{engine,socket,aec}.rs` (only with `--features native-audio`)
- Frontend: `web/src/`
  - Native-mode bridge: `web/src/voice/{VoiceStateBridge,useNativeBridge}.tsx`
- Config: `config/*.yaml`
- Tests: `tests/` (run with `python -m pytest`)
