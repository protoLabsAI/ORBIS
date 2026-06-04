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

Runtime (new `src-tauri/src/audio/wake_word.rs`, via the **`tract`** crate —
pure-Rust ONNX, no onnxruntime to bundle; see "Verified pipeline" below for why):
- Accumulate 4× 320-sample frames → 1280 samples (80 ms) → melspec → embedding →
  push into the embedding ring → wake model → score. Fire when score > threshold
  for a debounce window. (This mirrors oWW's `Model.predict` buffering; the
  constants — mel hop, embedding window=16, step — must match the trained model.)
- Load the 3 models at engine start from the bundled `models/` dir.
- Cheap: melspec+embedding are tiny; the wake model is a small classifier. Runs
  in the socket-writer tokio task, off the audio callback.

## Verified pipeline + constants (2026-06-02)

Cracked the exact pipeline by inspecting the ONNX models + building a Python
reference (`scripts/wakeword_reference.py`, the oracle the Rust port matches).
**Runtime decision: `tract` (pure-Rust ONNX), not `ort`** — model-op inspection
showed melspec/embedding/wake use only Conv/MatMul/Gemm/Mul/Clip/Pow/Log/
Sigmoid/LayerNormalization/Relu/MaxPool (no STFT/DFT/custom ops), all
tract-supported. So no ~15 MB onnxruntime to bundle — fits the lean-native
direction.

Exact pipeline (Hey Orbis = stock openWakeWord I/O, verified):
1. **Input:** 16 kHz, fed as **int16-range float32** (NOT normalized to
   [-1,1] — the melspec model was trained on the int16 range).
2. **melspectrogram.onnx** — internal STFT, **hop=160 (10 ms), win=640 (40 ms)**
   → `[1,1,F,32]`. Squeeze → **`mel/10 + 2`** (the oWW normalization — easy to
   miss, breaks matching if omitted).
3. **embedding_model.onnx** — sliding **76-mel-frame** window, **step 8**
   (= 1280 samples = 80 ms), input `[1,76,32,1]` → 96-dim embedding.
4. **`<wake>.onnx`** — last **16 embeddings** `[1,16,96]` → **sigmoid** 0..1.

**Strategy: full-window recompute**, not oWW's incremental per-chunk mel
buffering — melspec the whole ~2 s ring each 80 ms tick. The models are tiny
(runs comfortably at cadence) and it sidesteps the STFT chunk-boundary-overlap
bug a naive streaming port would hit. **~31360 samples (1.96 s)** produce the
first 16 embeddings → one score.

Reference scores (onnxruntime, non-wake inputs, all near 0 — the Rust/tract
port must match within tolerance): `zeros→0.000309`, `sine440→0.000420`,
`prng(seed42)→0.000376`. A real "Hey Orbis" clip is still needed to set the
fire threshold (the one remaining lab-node ask) — default oWW threshold ~0.5.

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

### ✅ Model I/O — verified (2026-06-02, by ONNX inspection)
Downloaded all three `.onnx` from the HF + openWakeWord releases and read their
graph I/O with onnxruntime. **Hey Orbis is the *exact* standard openWakeWord
pipeline** — no custom shapes:
- `melspectrogram.onnx` — in `[batch, samples]` (raw 16 kHz), out `[time,1,*,32]`
  → **32 mel bins**. Standard, shared.
- `embedding_model.onnx` — in `[*,76,32,1]` (**76-mel-frame** window × 32 bins),
  out `[*,1,1,96]` → **96-dim** embedding. Standard, shared.
- `hey_orbis.onnx` — in `x [1,16,96]` (**16-embedding** window), out
  `sigmoid [1,1]` (score 0–1). Standard oWW classifier I/O.

So the runtime constants are the documented oWW defaults (16 kHz · 32 mel ·
76-frame mel window → embedding · 16-embedding window → wake). The Rust detector
can be built against the stock oWW buffering with confidence; the picker already
fetches all three models.

### ❓ Still needed from the lab node (the one remaining thing)
- A couple of **test clips** — positive "Hey Orbis" + hard negatives — to tune
  the fire threshold and verify the Rust pipeline matches the Python eval. Until
  these land we ship the oWW default threshold (~0.5) and tune live.

(Models no longer need to be hand-dropped — the picker downloads `hey_orbis.onnx`
+ the shared models into the app-data `models/` dir the detector reads.)

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
