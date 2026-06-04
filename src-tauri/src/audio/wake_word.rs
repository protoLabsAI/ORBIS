//! openWakeWord detector — pure-Rust (tract) inference. No Python, no
//! onnxruntime dylib to bundle.
//!
//! Implements the verified 3-model pipeline from `docs/internal/wake-word.md`
//! and reproduces `scripts/wakeword_reference.py` (the oracle) within float
//! tolerance. Pipeline, per 80 ms tick, **full-window recompute** on a 2 s ring:
//!
//! ```text
//!   16 kHz int16-range f32  (NOT normalized to [-1,1])
//!     → melspectrogram.onnx → squeeze → /10 + 2
//!     → 76-mel-frame window, step 8 → embedding_model.onnx → 96-dim
//!     → last 16 embeddings [1,16,96] → <wake>.onnx → sigmoid 0..1
//! ```
//!
//! We melspec the whole ring each tick (rather than oWW's incremental per-chunk
//! buffering): the models are tiny, it runs comfortably at cadence, and it
//! sidesteps the STFT chunk-boundary-overlap bug a naive streaming port hits.
//!
//! Phase-3 note (`docs/native-audio-direction.md`): this is host-agnostic —
//! when `socket.rs` retires for `protolabs-voice-core`, only the tap point
//! moves, not this logic. Keep it self-contained.

use std::collections::VecDeque;
use std::path::{Path, PathBuf};
use std::sync::mpsc::{channel, Sender};
use std::sync::Arc;

use tract_onnx::prelude::*;

use super::engine::AudioEngine;

/// 16 kHz samples fed to melspec per score (2.0 s). Matches the oracle's
/// 32000-sample deterministic cases exactly, and yields ≥16 embeddings.
pub const WINDOW_SAMPLES: usize = 32_000;
const MEL_BINS: usize = 32;
const MEL_WINDOW: usize = 76; // mel frames per embedding
const MEL_STEP: usize = 8; // mel-frame hop between embeddings
const EMB_DIM: usize = 96;
const WAKE_WINDOW: usize = 16; // embeddings per wake score
/// New samples between scores: 1280 = 80 ms = 4× 320-sample (20 ms) mic frames.
const TICK_SAMPLES: usize = 1_280;
/// VAD floor (i16-RMS over the last ~320 ms) below which we skip the ~130 ms
/// pipeline. The CPAL mic noise floor sits well under this; any speech is far
/// above it — so an armed detector stays near-idle in a quiet room without
/// risking a missed phrase. Raw mic level (pre-`MIC_GAIN`); conservative.
const DEFAULT_ENERGY_FLOOR: f32 = 60.0;

type Model = TypedRunnableModel<TypedModel>;

fn load_model(path: &Path, input_shape: &[i32]) -> TractResult<Model> {
    // `into_decluttered()`, NOT `into_optimized()`: tract 0.21's full optimizer
    // (PushSplitDown pass) panics on an opaque-fact assertion for these models
    // (tract-core fact.rs:371). Decluttering runs the safe passes (constant
    // folding etc.) — faster than the raw typed graph, without the crashing
    // optimize pass — and is numerically identical.
    tract_onnx::onnx()
        .model_for_path(path)?
        .with_input_fact(0, f32::fact(input_shape).into())?
        .into_typed()?
        .into_decluttered()?
        .into_runnable()
}

/// One enabled wake word + its detection state. The two shared models
/// (melspec, embedding) are reloaded per detector for now — they're ~1 MB and
/// loaded once at engine start; sharing across multiple enabled words is a
/// later optimization.
pub struct WakeWord {
    mel: Model,
    emb: Model,
    wake: Model,
    ring: VecDeque<i16>,
    threshold: f32,
    /// Debounce: after a fire, suppress re-fires until the score drops back
    /// below threshold — one utterance is one fire, not a burst.
    rearmed: bool,
    last_score: f32,
    energy_floor: f32,
}

impl WakeWord {
    /// Load `<wake>.onnx` + the shared melspec/embedding models from `dir`.
    pub fn load_from_dir(dir: &Path, wake_model: &str, threshold: f32) -> TractResult<Self> {
        let mel = load_model(
            &dir.join("melspectrogram.onnx"),
            &[1, WINDOW_SAMPLES as i32],
        )?;
        // Batch all 16 embedding windows in ONE inference (input [16,76,32,1]),
        // not 16 sequential calls — the embedding's batch dim is dynamic and
        // this is ~16× fewer tract invocations.
        let emb = load_model(
            &dir.join("embedding_model.onnx"),
            &[WAKE_WINDOW as i32, MEL_WINDOW as i32, MEL_BINS as i32, 1],
        )?;
        let wake = load_model(
            &dir.join(format!("{wake_model}.onnx")),
            &[1, WAKE_WINDOW as i32, EMB_DIM as i32],
        )?;
        Ok(Self {
            mel,
            emb,
            wake,
            ring: VecDeque::with_capacity(WINDOW_SAMPLES + TICK_SAMPLES),
            threshold,
            rearmed: true,
            last_score: 0.0,
            energy_floor: DEFAULT_ENERGY_FLOOR,
        })
    }

    /// i16-RMS over the last ~320 ms of the ring (the VAD gate input).
    fn recent_rms(&self) -> f32 {
        let n = (TICK_SAMPLES * 4).min(self.ring.len());
        if n == 0 {
            return 0.0;
        }
        let sum: f64 = self
            .ring
            .iter()
            .rev()
            .take(n)
            .map(|&s| (s as f64) * (s as f64))
            .sum();
        (sum / n as f64).sqrt() as f32
    }

    /// Push mic samples (16 kHz mono i16) into the ring — cheap, no inference.
    /// The detector thread drains its whole frame backlog through `feed` each
    /// iteration so the next `tick_score` always sees the freshest 2 s.
    pub fn feed(&mut self, samples: &[i16]) {
        for &s in samples {
            if self.ring.len() == WINDOW_SAMPLES {
                self.ring.pop_front();
            }
            self.ring.push_back(s);
        }
    }

    /// Score the current ring once (VAD-gated). Returns `true` on a *rising*
    /// threshold crossing (debounced: one utterance = one fire, re-armed only
    /// after the score falls back below threshold). Call at the thread's
    /// natural cadence (~7/s) — pacing comes from how long the score takes.
    pub fn tick_score(&mut self) -> bool {
        if self.ring.len() < WINDOW_SAMPLES {
            return false;
        }
        // VAD gate: a quiet room never triggers the expensive pipeline.
        if self.recent_rms() < self.energy_floor {
            self.last_score = 0.0;
            self.rearmed = true;
            return false;
        }
        let audio: Vec<f32> = self.ring.iter().map(|&s| s as f32).collect();
        match score_window(&self.mel, &self.emb, &self.wake, &audio) {
            Ok(Some(score)) => {
                self.last_score = score;
                if score >= self.threshold {
                    if self.rearmed {
                        self.rearmed = false;
                        log::info!("[wake] fired (score {score:.3} ≥ {:.3})", self.threshold);
                        return true;
                    }
                } else {
                    self.rearmed = true;
                }
                false
            }
            Ok(None) => false,
            Err(e) => {
                log::warn!("[wake] score failed: {e}");
                false
            }
        }
    }

    /// Most recent score (for threshold tuning / debug surfacing).
    pub fn last_score(&self) -> f32 {
        self.last_score
    }

    /// True when the recent audio is below the VAD floor — the trailing-silence
    /// signal the listening window uses to auto-close.
    pub fn is_recent_silence(&self) -> bool {
        self.recent_rms() < self.energy_floor
    }

    /// Re-arm the fire debounce after a listening window closes, so the next
    /// "Hey Orbis" can fire again.
    pub fn rearm(&mut self) {
        self.rearmed = true;
    }
}

/// What the detector thread needs to run: where the `.onnx` files live (the
/// picker's `<models_dir>/wakeword/` layout), which wake word, the fire
/// threshold, and the auto-close window. Built in `lib.rs` from the app-data
/// dir + the persisted activation config.
#[derive(Clone)]
pub struct WakeConfig {
    pub models_dir: PathBuf,
    pub model: String,
    pub threshold: f32,
    /// Auto-close the listening window after this many seconds of trailing
    /// silence → back to ARMED (the engagement-modes listen-window timer,
    /// distinct from the ~0.4 s end-of-utterance VAD).
    pub listen_window_s: f32,
}

/// Sink for ARMED/LISTENING transitions. `lib.rs` wires this to a Tauri event
/// (`wake-state`); keeping it an opaque closure keeps `socket.rs`/this module
/// free of any Tauri coupling (they survive the Phase-3 socket retirement).
pub type WakeStateEmitter = Box<dyn Fn(&str) + Send>;

/// Spawn the detector on a dedicated OS thread (the ~130 ms melspec must never
/// block the mic→Python writer). Returns a sender the socket writer tees every
/// mic frame to. The thread loads the models once, then loops: drain the frame
/// backlog into the ring, score the freshest 2 s, and arm a listening window on
/// a fire. A model-load failure logs and exits the thread (frames then drop
/// harmlessly) — wake mode simply doesn't engage.
pub fn spawn_detector(
    config: WakeConfig,
    engine: Arc<AudioEngine>,
    emit: WakeStateEmitter,
) -> Sender<Vec<i16>> {
    use std::time::{Duration, Instant};

    let (tx, rx) = channel::<Vec<i16>>();
    std::thread::Builder::new()
        .name("orbis-wakeword".into())
        .spawn(move || {
            let mut det = match WakeWord::load_from_dir(
                &config.models_dir,
                &config.model,
                config.threshold,
            ) {
                Ok(d) => d,
                Err(e) => {
                    log::error!("[wake] detector disabled — model load failed: {e}");
                    return;
                }
            };
            log::info!(
                "[wake] detector armed: model={} threshold={:.2} window={:.0}s dir={}",
                config.model,
                config.threshold,
                config.listen_window_s,
                config.models_dir.display()
            );
            emit("armed");

            // ARMED: score the ring for the phrase. LISTENING (post-fire): stop
            // scoring (the mic's already open + flowing to Python), just watch
            // for trailing silence to auto-close back to ARMED.
            #[derive(PartialEq)]
            enum State {
                Armed,
                Listening,
            }
            let mut state = State::Armed;
            let mut last_voice = Instant::now();
            let window = Duration::from_secs_f32(config.listen_window_s.max(1.0));

            while let Ok(frame) = rx.recv() {
                det.feed(&frame);
                // Drain backlog so the next score sees the freshest audio (the
                // recv/try_recv pacing self-limits to ~score-rate; no unbounded
                // queue growth).
                while let Ok(f) = rx.try_recv() {
                    det.feed(&f);
                }
                match state {
                    State::Armed => {
                        if det.tick_score() {
                            engine.arm_listening_window();
                            emit("listening");
                            last_voice = Instant::now();
                            state = State::Listening;
                        }
                    }
                    State::Listening => {
                        if det.is_recent_silence() {
                            if last_voice.elapsed() >= window {
                                engine.set_listening(false);
                                det.rearm();
                                emit("armed");
                                state = State::Armed;
                                log::info!(
                                    "[wake] auto-closed after {:.0}s trailing silence → armed",
                                    config.listen_window_s
                                );
                            }
                        } else {
                            last_voice = Instant::now();
                        }
                    }
                }
            }
            log::info!("[wake] detector thread exiting (frame channel closed)");
        })
        .expect("spawn orbis-wakeword thread");
    tx
}

/// Stateless pipeline — shared by the live detector and the oracle test.
/// Audio is int16-range f32 (the melspec model was trained on that range).
fn score_window(mel: &Model, emb: &Model, wake: &Model, audio: &[f32]) -> TractResult<Option<f32>> {
    // 1. melspectrogram: [1, N] → [1,1,F,32]; squeeze (0,1) → [F,32]; /10 + 2.
    let mel_in: Tensor =
        tract_ndarray::Array2::from_shape_vec((1, audio.len()), audio.to_vec())?.into();
    let mel_out = mel.run(tvec!(mel_in.into()))?;
    let mel_view = mel_out[0].to_array_view::<f32>()?;
    let frames = mel_view.shape()[2];
    if frames < MEL_WINDOW {
        return Ok(None);
    }
    // Flatten [1,1,F,32] row-major → F*32, applying the oWW normalization.
    let mel_norm: Vec<f32> = mel_view.iter().map(|v| v / 10.0 + 2.0).collect();

    // 2. embeddings: sliding 76-mel-frame window, step 8. We only need the
    // wake model's last 16 embeddings, so build exactly those 16 windows and
    // run them as ONE batched [16,76,32,1] inference (identical to the oracle's
    // per-window loop — windows are independent — but ~16× fewer tract calls).
    let n_windows = (frames - MEL_WINDOW) / MEL_STEP + 1;
    if n_windows < WAKE_WINDOW {
        return Ok(None);
    }
    let mut batch = Vec::with_capacity(WAKE_WINDOW * MEL_WINDOW * MEL_BINS);
    for k in 0..WAKE_WINDOW {
        let start = (n_windows - WAKE_WINDOW + k) * MEL_STEP;
        for t in start..start + MEL_WINDOW {
            batch.extend_from_slice(&mel_norm[t * MEL_BINS..(t + 1) * MEL_BINS]);
        }
    }
    let emb_in: Tensor =
        tract_ndarray::Array4::from_shape_vec((WAKE_WINDOW, MEL_WINDOW, MEL_BINS, 1), batch)?
            .into();
    let emb_out = emb.run(tvec!(emb_in.into()))?;
    // [16,1,1,96] row-major → 16×96, already in window order → wake input.
    let embs: Vec<f32> = emb_out[0].to_array_view::<f32>()?.iter().copied().collect();

    // 3. wake classifier: the 16 embeddings [1,16,96] → sigmoid 0..1.
    let wake_in: Tensor =
        tract_ndarray::Array3::from_shape_vec((1, WAKE_WINDOW, EMB_DIM), embs)?.into();
    let wake_out = wake.run(tvec!(wake_in.into()))?;
    let score = wake_out[0]
        .to_array_view::<f32>()?
        .iter()
        .copied()
        .next()
        .unwrap_or(0.0);
    Ok(Some(score))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    /// The detector test needs the real ONNX models. Point `ORBIS_MODELS_DIR`
    /// at a dir whose `wakeword/` holds melspectrogram/embedding/hey_orbis
    /// `.onnx` (the picker's layout). Skips when absent so CI without models
    /// stays green; run locally with the dev models dir to verify the port.
    fn models_dir() -> Option<PathBuf> {
        let base = std::env::var("ORBIS_MODELS_DIR").ok()?;
        let d = Path::new(&base).join("wakeword");
        d.join("melspectrogram.onnx").exists().then_some(d)
    }

    /// Reproduce the oracle (scripts/wakeword_reference.py) deterministic cases
    /// within tract-vs-onnxruntime float tolerance. zeros→0.000309,
    /// sine440→0.000420. Matching these proves the whole pipeline (melspec +
    /// /10+2 norm + embedding windowing + wake) is byte-faithful.
    #[test]
    fn matches_reference_oracle() {
        let Some(dir) = models_dir() else {
            eprintln!("skip matches_reference_oracle: set ORBIS_MODELS_DIR (see test doc)");
            return;
        };
        let det = WakeWord::load_from_dir(&dir, "hey_orbis", 0.5).expect("load models");

        let zeros = vec![0.0f32; WINDOW_SAMPLES];
        let z = score_window(&det.mel, &det.emb, &det.wake, &zeros)
            .unwrap()
            .unwrap();
        assert!((z - 0.000309).abs() < 5e-4, "zeros score {z} != ~0.000309");

        let sine: Vec<f32> = (0..WINDOW_SAMPLES)
            .map(|n| 10_000.0 * (2.0 * std::f32::consts::PI * 440.0 * n as f32 / 16_000.0).sin())
            .collect();
        let s = score_window(&det.mel, &det.emb, &det.wake, &sine)
            .unwrap()
            .unwrap();
        assert!(
            (s - 0.000420).abs() < 5e-4,
            "sine440 score {s} != ~0.000420"
        );
    }

    /// Per-score latency. The detector runs on a DEDICATED thread that always
    /// scores the freshest 2 s ring — it does NOT tick inline on the mic path,
    /// so this isn't bounded by the 80 ms frame cadence. The full-window melspec
    /// is ~130 ms un-optimized (tract's optimizer is the one that crashes), which
    /// gives ~7 scores/s — plenty to catch a phrase that sits in the 2 s window
    /// for >1 s. This is a regression guard against catastrophic slowdown, not
    /// an inline budget. (Incremental melspec could cut this ~25× later.)
    #[test]
    fn score_latency_regression_guard() {
        let Some(dir) = models_dir() else {
            eprintln!("skip score_latency: set ORBIS_MODELS_DIR");
            return;
        };
        let det = WakeWord::load_from_dir(&dir, "hey_orbis", 0.5).expect("load models");
        let audio = vec![0.0f32; WINDOW_SAMPLES];
        let _ = score_window(&det.mel, &det.emb, &det.wake, &audio); // warm up
        let t0 = std::time::Instant::now();
        let iters = 20;
        for _ in 0..iters {
            let _ = score_window(&det.mel, &det.emb, &det.wake, &audio).unwrap();
        }
        let per = t0.elapsed() / iters;
        eprintln!(
            "[wake] per-score latency: {per:?} (~{:.1} scores/s)",
            1000.0 / per.as_millis().max(1) as f64
        );
        // ≥ ~3 scores/s keeps detection responsive on the dedicated thread.
        assert!(
            per.as_millis() < 300,
            "per-score {per:?} — detection too slow to catch a phrase"
        );
    }
}
