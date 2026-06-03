# Wake word — implementation plan

Status: **PLAN** (2026-06-02). Decisions locked with Josh: **openWakeWord**
(local/offline), **Rust-native**, custom **"Hey Orbis"** (model trained on the
lab node). Part of the engagement-modes activation layer (`engagement-modes.md`).
This is the ARMED state in `MUTED → ARMED → LISTENING → auto-close`.

## Where it taps (grounded in the audio map)

Every captured mic frame — CPAL today, AVAudioEngine under
`--features voice-processing` — converges at the **socket writer**
(`src-tauri/src/audio/socket.rs` ~line 153), already **16 kHz mono i16 in
320-sample (20 ms) frames**, right before the `is_listening()` gate:

```rust
AudioMsg::MicFrame(samples) => {
    wake.process(&samples);                 // ← NEW: feed the detector (always)
    if wake.fired() { eng.arm_listening_window(); }  // open gate + start auto-close timer
    if !eng.is_listening() || eng.echo_guard_active(ECHO_GUARD_MS) { continue; }
    // … forward to Python …
}
```

- **Survives Phase 2** (input swap): both audio paths feed the same `tx` channel,
  so the writer is unchanged. ✓
- **Phase 3 (Q2) deletes `socket.rs`** (→ `protolabs-voice-core` WS). The detector
  *logic* (model load, windowing, gate-open) ports forward; only its host moves.
  So: keep `wake_word.rs` self-contained and host-agnostic, plan a small Phase-3
  reseat. Acceptable — it's a ~2-3 month home and the value lands now.

## The openWakeWord Rust pipeline

openWakeWord is three ONNX models in sequence (no Python needed at runtime):
1. **melspectrogram** — raw 16 kHz audio → mel features. *(shared, ships with oWW)*
2. **embedding** — mel → 96-dim speech embeddings (Google `speech_embedding`).
   *(shared, ships with oWW)*
3. **wake model** — a sliding window of embeddings → score 0–1. *("Hey Orbis",
   from the lab node.)*

Runtime (new `src-tauri/src/audio/wake_word.rs`, via the **`ort`** crate — ONNX
Runtime, no Python):
- Accumulate 4× 320-sample frames → 1280 samples (80 ms) → melspec → embedding →
  push into the embedding ring → wake model → score. Fire when score > threshold
  for a debounce window. (This mirrors oWW's `Model.predict` buffering; the
  constants — mel hop, embedding window=16, step — must match the trained model.)
- Load the 3 models at engine start from the bundled `models/` dir.
- Cheap: melspec+embedding are tiny; the wake model is a small classifier. Runs
  in the socket-writer tokio task, off the audio callback.

## Model catalog + picker (Josh, 2026-06-02)

The wake-word UI shouldn't be Hey-Orbis-only — offer the **common pre-trained
openWakeWord models** too (from the original `dscripka/openWakeWord` repo):
`alexa`, `hey_mycroft`, `hey_jarvis`, `hey_rhasspy`, `timer`, `weather` (the stock
release set). Every one is just a different **wake `.onnx`** sharing the same
melspec + embedding models — so the catalog is cheap: one manifest entry per wake
word, all pointing at the two shared models + their own classifier.

This is exactly the Handy-style **model picker + download** UX (from the Handy
audit): a per-model manifest (`id`, label, size, sha256, source url, recommended),
resumable + SHA256-verified downloads, throttled progress events, stored under the
app-data `models/` dir, and a small Tauri command set + a picker UI. Seed it with:
- **Hey Orbis** (custom, `protoLabsAI/hey-orbis-wakeword`) — recommended/default.
- the stock oWW set above (pull from the openWakeWord GitHub releases).
- the two shared models (melspec + embedding) as a base dependency.
The user enables one (or several) wake words; the detector loads each enabled
wake `.onnx` against the shared embedding ring.

### ❓ What the lab node must export (the one thing I need from you)
Please confirm what your "Hey Orbis" training pipeline produces, so I match the
runtime exactly:
- The **wake model** as `hey_orbis.onnx` (oWW-standard: input = embedding window
  `[1,16,96]`, output = score `[1,1]`)? Or a different I/O shape?
- Do you also have the **shared `melspectrogram.onnx` + `embedding_model.onnx`**
  (the standard oWW ones), or should I pull them from the openWakeWord release?
- Training **sample rate / mel params** — standard oWW (16 kHz, 32 mel, 76-frame
  window)? If your pipeline customized these, I need the constants.
- A couple of **test clips** (positive "Hey Orbis" + hard negatives) to tune the
  threshold + verify the Rust pipeline matches your Python eval.

Drop the model(s) in `src-tauri/models/`; I'll bundle them via tauri.conf
`resources` and resolve at runtime.

## Gate-open + the listen window (the "listen then stop")

On fire: `set_listening(true)` + start an **auto-close timer**. Stay open while
there's speech; close after `listen_window_s` (~8–15 s, configurable) of trailing
silence → back to ARMED. Two distinct timers (keep separate):
- end-of-utterance `VAD_STOP_SECS` (~0.4 s) — Python turn-taking, unchanged.
- **`listen_window_s`** — Rust-side; close the mic after sustained silence.
  Silence = frame energy (RMS) below a threshold for the window (Rust already has
  the raw frames). Follow-ups within the window don't need the phrase again.

## Composition with what's shipped
- **Mic toggle** (master mute): muted → detector OFF (truly silent). Unmuted →
  the activation style applies.
- **Activation style** (new config, default push-to-talk): `push_to_talk` (today)
  · `wake_word` (armed; detector runs) · `open_mic` (always hot, no auto-close).
- **Double-click orb**: still force-opens a listening window (manual override).

## Feedback (orb)
Rust emits a state event (Tauri event → `voiceStore`): `armed` (idle, listening
for the phrase — subtle), `listening` (hot, post-fire — the current listening
state), plus an optional soft chime on fire. The mic-toggle icon already reflects
listening; ARMED needs a distinct subtle cue.

## Config / UI
`engagement.activation` block (persona YAML + runtime `.env` override): style,
`listen_window_s`, wake threshold, phrase label. Surface in Settings (the
"elevate config to UI" rule) once the engine works.

## Build order
1. **Model-independent scaffold (testable now):** the `listen_window_s`
   auto-close timer + the `activation_style` config (push-to-talk / open-mic
   working; wake_word stubbed). Ships the timing UX; the detector becomes the
   trigger later.
2. **Detector (when the model lands):** `ort` dep + `wake_word.rs` (3-model
   pipeline) + the socket tap + threshold tuning against your test clips.
3. **Orb ARMED state + the activation-style setting in Settings.**
4. **Phase-3 note:** reseat `wake_word.rs` onto `protolabs-voice-core` when
   `socket.rs` is retired (Q2).

## Privacy line (worth stating in the UI)
With the detector local, nothing leaves the machine until the phrase fires — a
clean "ORBIS isn't streaming your audio" story that fits the private-license
direction.
