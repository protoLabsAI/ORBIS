//! CPAL audio engine — mic capture and TTS playback.
//!
//! Opens the default (or named) CoreAudio device for input at 16 kHz
//! mono and for output at 24 kHz mono. If the device doesn't natively
//! support those rates, we resample (linearly) in the stream callback.
//!
//! Mic frames (320 samples = 20 ms at 16 kHz) are sent to the socket
//! layer via an unbounded channel. TTS frames are received from the
//! socket layer and pushed into a playback ring buffer that the output
//! callback drains.
//!
//! TODO(cpal-0.18+): migrate from `DeviceTrait::name()` (deprecated in
//! 0.17) to `description()` for human-readable strings + `id()` for
//! stable device identification. Held back today because the user's
//! saved-preference store keys on `name()` strings and changing the
//! match value would invalidate existing settings; do this with a
//! migration path when we touch the preferred-device store next.
#![allow(deprecated)]

use std::collections::VecDeque;
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::{Device, SampleFormat, Stream, StreamConfig};

use super::aec::AecProcessor;

/// Mic frame size: 20 ms at 16 kHz mono.
pub const MIC_FRAME_SAMPLES: usize = 320;
/// Mic sample rate sent to Python.
pub const MIC_SAMPLE_RATE: u32 = 16_000;
/// TTS sample rate received from Python (Kokoro output).
pub const TTS_SAMPLE_RATE: u32 = 24_000;

/// Messages from the engine to the socket layer.
pub enum AudioMsg {
    /// A processed mic frame ready to send to Python.
    MicFrame(Vec<i16>),
}

/// Shared playback ring: TTS PCM frames enqueued by the socket reader,
/// drained by the CPAL output callback.
type PlaybackRing = Arc<Mutex<VecDeque<i16>>>;

/// Runtime-selected mic input source. The voice-processing build tries VPIO
/// (Apple AEC) first and falls back to CPAL + software AEC when VPIO delivers no
/// audio — e.g. the default output is a USB interface, which can't form a
/// voice-processing aggregate, leaving the mic silently dead. The non-VP build
/// only ever has the CPAL variant.
///
/// The held value (a CPAL `Stream` or the VPIO engine) is kept solely so its
/// `Drop` stops the hardware — never read — hence `allow(dead_code)`.
#[allow(dead_code)]
enum InputSource {
    Cpal(Stream),
    #[cfg(all(feature = "voice-processing", target_os = "macos"))]
    VoiceProcessing(super::voice_processing_input::VoiceProcessingInput),
}

/// The live CPAL audio engine.
///
/// Holds the open CPAL streams (dropping them stops the hardware).
/// Lifetime is tied to the Tauri app — created in `supervise_sidecar`,
/// stored as Tauri managed state, dropped on `ExitRequested`.
pub struct AudioEngine {
    /// Channel sender — mic frames go to the socket task.
    pub tx: tokio::sync::mpsc::UnboundedSender<AudioMsg>,
    /// TTS playback ring shared with the CPAL output callback.
    playback_ring: PlaybackRing,
    /// AEC processor, shared with the CPAL input callback when that
    /// path is in use. With voice-processing IO, Apple does AEC and
    /// this Mutex is unused — kept for ABI compatibility while both
    /// paths coexist.
    aec: Arc<Mutex<AecProcessor>>,
    /// Current input RMS level (0.0–1.0), updated by whichever input
    /// path is active.
    /// Stored as f32 bits in an AtomicU32 for lock-free reads from the UI.
    rms: Arc<AtomicU32>,
    /// Push-to-talk gate. When false (default) the socket writer drops
    /// mic frames so the sidecar receives no audio — this prevents the
    /// always-listening behavior and Whisper hallucinations on silence.
    /// Toggled by double-clicking the orb via `set_mic_listening`.
    pub listening: Arc<AtomicBool>,
    /// Full-duplex override. When true, the socket writer keeps the mic open
    /// while she speaks (instead of the half-duplex echo mute) so the user can
    /// barge in. Safe only with echo cancellation (VPIO) OR headphones (no
    /// acoustic bleed) — default false. Toggled live via `set_full_duplex`
    /// (Settings → Activation → "Allow interruptions").
    pub full_duplex: Arc<AtomicBool>,
    /// Monotonic-ms timestamp of the last real TTS audio frame sent to the
    /// speaker. Half-duplex echo guard: the socket writer drops mic frames
    /// while (and just after) she's speaking. Confirmed needed by mic-diag
    /// — the laptop mic picks up her TTS acoustically (~88 RMS) even on
    /// headphones, which STT would otherwise transcribe as a user turn.
    pub last_play_ms: Arc<AtomicU64>,
    /// Current TTS playback level (0.0–1.0), updated by the output
    /// callback. Drives the orb's audio-reactivity (she pulses to her
    /// own voice). Stored as f32 bits in an AtomicU32 for lock-free reads.
    playback_rms: Arc<AtomicU32>,
    /// Output device sample rate, published by the output stream once it's
    /// built. `push_playback` resamples each TTS chunk to this rate ONCE
    /// (full context) so the output callback can drain native-rate samples
    /// without per-block resampling artifacts (crackle).
    output_rate: Arc<AtomicU32>,
    // Keep the input + output alive — they stop when dropped.
    /// Active mic input (VPIO or CPAL, chosen at runtime). Dropping stops capture.
    _input: InputSource,
    _output_stream: Stream,
    /// True when the active input is VPIO (Apple AEC — mic stays open during
    /// playback for barge-in). False for CPAL (software AEC — half-duplex mute).
    /// Set once at construction; drives `half_duplex()` so the voice-processing
    /// build behaves correctly when it falls back to CPAL.
    vpio_active: Arc<AtomicBool>,
}

// cpal 0.17.0 made Stream Send on all hosts (RustAudio/cpal#818, #1021).
// AudioEngine is naturally Send/Sync without an unsafe impl.

impl AudioEngine {
    /// Open the default input and output devices and start streaming.
    ///
    /// `input_device_name` — if `Some`, selects the named CPAL input
    ///   device; if `None`, uses the host default.
    pub fn new(
        input_device_name: Option<&str>,
        tx: tokio::sync::mpsc::UnboundedSender<AudioMsg>,
    ) -> Result<Self, String> {
        let host = cpal::default_host();

        // --- Output device (always CPAL — Phase 2 only swaps input) ---
        let output_device = host
            .default_output_device()
            .ok_or_else(|| "no default output device".to_string())?;
        log::info!(
            "[audio] output device: {}",
            output_device.name().unwrap_or_default()
        );

        let aec = Arc::new(Mutex::new(AecProcessor::from_env()));
        let playback_ring: PlaybackRing = Arc::new(Mutex::new(VecDeque::with_capacity(48_000)));
        let rms = Arc::new(AtomicU32::new(0));
        let listening = Arc::new(AtomicBool::new(false)); // push-to-talk: muted by default
                                                          // Full-duplex starts off (half-duplex, safe). ORBIS_FULL_DUPLEX=1 seeds
                                                          // it on for testing; the persisted Settings toggle sets it at startup.
        let full_duplex = Arc::new(AtomicBool::new(
            std::env::var("ORBIS_FULL_DUPLEX")
                .map(|v| v == "1")
                .unwrap_or(false),
        ));
        let last_play_ms = Arc::new(AtomicU64::new(0)); // half-duplex echo guard
        let playback_rms = Arc::new(AtomicU32::new(0)); // TTS level for orb reactivity
        let output_rate = Arc::new(AtomicU32::new(TTS_SAMPLE_RATE)); // set by the output stream

        let output_stream = build_output_stream(
            &output_device,
            Arc::clone(&playback_ring),
            Arc::clone(&last_play_ms),
            Arc::clone(&playback_rms),
            Arc::clone(&output_rate),
        )?;
        output_stream
            .play()
            .map_err(|e| format!("output stream play: {e}"))?;

        // --- Input path: VPIO when it works, CPAL + software-AEC fallback ----
        // The mic is the one thing that must never silently die. VPIO gives
        // Apple AEC but only forms its capture aggregate with built-in /
        // aggregatable output; with a USB interface as the default output it
        // delivers ZERO input (the tap never fires). So we try VPIO, watchdog
        // the tap, and fall back to CPAL + software AEC if nothing arrives.
        let vpio_active = Arc::new(AtomicBool::new(false));

        #[cfg(not(feature = "voice-processing"))]
        let _input = InputSource::Cpal(build_cpal_input(
            &host,
            input_device_name,
            tx.clone(),
            Arc::clone(&aec),
            Arc::clone(&rms),
            1.0, // sidecar (cpal mode) applies its own 16× gain
        )?);

        #[cfg(all(feature = "voice-processing", target_os = "macos"))]
        let _input = {
            let dev_id =
                input_device_name.and_then(super::coreaudio_devices::input_device_id_for_name);
            if input_device_name.is_some() && dev_id.is_none() {
                log::warn!(
                    "[audio] input device '{}' not found in Core Audio — using default",
                    input_device_name.unwrap_or("")
                );
            }
            log::info!("[audio] input path: AVAudioEngine voice-processing (CPAL fallback armed)");
            let tap_count = Arc::new(AtomicU64::new(0));
            match super::voice_processing_input::VoiceProcessingInput::new(
                tx.clone(),
                Arc::clone(&rms),
                dev_id,
                Arc::clone(&tap_count),
            ) {
                Ok(vp) => {
                    // Watchdog: the first tap normally fires within ~1–2 s. Zero
                    // ticks after ~4 s ⇒ VPIO can't capture (non-aggregatable
                    // output, e.g. a USB interface) ⇒ fall back.
                    let mut fired = false;
                    for _ in 0..80 {
                        if tap_count.load(Ordering::Relaxed) > 0 {
                            fired = true;
                            break;
                        }
                        std::thread::sleep(std::time::Duration::from_millis(50));
                    }
                    if fired {
                        log::info!("[audio] VPIO input live — Apple AEC active (full-duplex)");
                        vpio_active.store(true, Ordering::Relaxed);
                        InputSource::VoiceProcessing(vp)
                    } else {
                        log::warn!(
                            "[audio] VPIO delivered no mic audio in 4 s — the default output \
                             can't form a voice-processing aggregate (USB interface?). Falling \
                             back to CPAL + software AEC so the mic works (half-duplex)."
                        );
                        drop(vp); // tear down the dead VPIO engine, release the mic
                        InputSource::Cpal(build_cpal_input(
                            &host,
                            input_device_name,
                            tx.clone(),
                            Arc::clone(&aec),
                            Arc::clone(&rms),
                            16.0, // sidecar is voice_processing (gain 1×); boost here
                        )?)
                    }
                }
                Err(e) => {
                    log::warn!(
                        "[audio] VPIO init failed: {e} — falling back to CPAL + software AEC"
                    );
                    InputSource::Cpal(build_cpal_input(
                        &host,
                        input_device_name,
                        tx.clone(),
                        Arc::clone(&aec),
                        Arc::clone(&rms),
                        16.0,
                    )?)
                }
            }
        };

        Ok(Self {
            tx,
            playback_ring,
            aec,
            rms,
            listening,
            full_duplex,
            last_play_ms,
            playback_rms,
            output_rate,
            _input,
            _output_stream: output_stream,
            vpio_active,
        })
    }

    /// Current microphone RMS level (0.0–1.0), updated every mic frame.
    pub fn current_rms(&self) -> f32 {
        f32::from_bits(self.rms.load(Ordering::Relaxed))
    }

    /// Toggle push-to-talk. When off, mic frames are dropped before
    /// reaching the sidecar.
    pub fn set_listening(&self, on: bool) {
        self.listening.store(on, Ordering::Relaxed);
        log::info!("[audio] mic listening = {on}");
    }

    /// Whether the mic is currently forwarding audio to the sidecar.
    pub fn is_listening(&self) -> bool {
        self.listening.load(Ordering::Relaxed)
    }

    /// Open a listening window from a wake-word fire (the ARMED → LISTENING
    /// transition). Idempotent while already listening, so a burst of fires is
    /// one open. For now this just unmutes the mic — the same gate a manual
    /// double-click opens; the auto-close timer + orb ARMED cue land in the
    /// engagement-modes UX pass (docs/internal/wake-word.md, build step 3).
    pub fn arm_listening_window(&self) {
        if !self.is_listening() {
            self.set_listening(true);
            log::info!("[wake] fired → listening window opened");
        }
    }

    /// Half-duplex echo guard: true if real TTS audio played within the
    /// last `guard_ms`, so the mic should stay muted (her voice bleeds
    /// into the mic acoustically and would otherwise be transcribed).
    pub fn echo_guard_active(&self, guard_ms: u64) -> bool {
        now_ms().saturating_sub(self.last_play_ms.load(Ordering::Relaxed)) < guard_ms
    }

    /// Whether the mic must be hard-muted while TTS plays (half-duplex).
    ///
    /// True in CPAL mode: there's no echo cancellation, so an open mic
    /// during playback would transcribe the bot's own voice and she'd
    /// interrupt herself. False in voice-processing mode: the AVAudioEngine
    /// VPIO unit cancels the speaker echo in hardware, so the mic can stay
    /// open during playback for real barge-in — Python's `BargeInGate`
    /// (grace window + transcription confirmation) filters any residual.
    /// `vpio_active` is set once at construction (true only when VPIO actually
    /// captured audio), so a voice-processing build that fell back to CPAL
    /// correctly reverts to half-duplex; the `full_duplex` override then lets
    /// headphone users open the mic during playback on a CPAL path (no AEC, but
    /// no acoustic echo either).
    pub fn half_duplex(&self) -> bool {
        !self.vpio_active.load(Ordering::Relaxed) && !self.full_duplex.load(Ordering::Relaxed)
    }

    /// Toggle the full-duplex override live (Settings → Activation). When on,
    /// the socket writer stops muting the mic during playback so the user can
    /// barge in. The writer reads `half_duplex()` per frame, so this is instant.
    pub fn set_full_duplex(&self, on: bool) {
        self.full_duplex.store(on, Ordering::Relaxed);
        log::info!("[audio] full_duplex = {on} (mic open during playback for barge-in)");
    }

    pub fn is_full_duplex(&self) -> bool {
        self.full_duplex.load(Ordering::Relaxed)
    }

    /// Current TTS playback level (0.0–1.0) for orb audio-reactivity.
    pub fn playback_level(&self) -> f32 {
        f32::from_bits(self.playback_rms.load(Ordering::Relaxed))
    }

    /// Enqueue TTS PCM samples for playback.
    ///
    /// Samples must be i16 at `TTS_SAMPLE_RATE` Hz mono. The socket
    /// reader calls this as TTS frames arrive from Python.
    ///
    /// Also feeds the AEC reference buffer (resampled to 16 kHz) so the
    /// input callback can subtract speaker bleed from the mic signal.
    pub fn push_playback(&self, samples: &[i16]) {
        // Feed AEC reference (TTS at 24 kHz → down to 16 kHz via
        // simple 2-in-3-out decimation for the reference path; Phase 3
        // uses rubato for quality).
        let reference_16k = decimate_24k_to_16k(samples);
        if let Ok(mut aec) = self.aec.lock() {
            aec.feed_reference(&reference_16k);
        }
        // Resample the whole chunk ONCE to the output device rate (full
        // context) before queueing, so the output callback just drains —
        // this eliminates the per-block resampling crackle.
        let out_rate = self.output_rate.load(Ordering::Relaxed);
        if let Ok(mut ring) = self.playback_ring.lock() {
            if out_rate != TTS_SAMPLE_RATE {
                ring.extend(resample_cubic(samples, TTS_SAMPLE_RATE, out_rate));
            } else {
                ring.extend(samples.iter().copied());
            }
        }
    }

    /// Flush the playback ring immediately (barge-in).
    pub fn flush_playback(&self) {
        if let Ok(mut ring) = self.playback_ring.lock() {
            ring.clear();
        }
    }

    /// List available input device names for the mic picker.
    ///
    /// On macOS this goes through the Core Audio HAL
    /// (`coreaudio_devices::list_input_device_names`) — pure property reads, no
    /// stream is opened — so enumerating never lights the mic indicator. CPAL's
    /// `input_devices()` *probes* (opens) each device on recent macOS, which
    /// made the indicator flicker on/off and stalled the Voice tab on mount.
    /// CPAL stays the path on other platforms (Linux CI).
    pub fn list_input_devices() -> Vec<String> {
        #[cfg(target_os = "macos")]
        {
            let names = super::coreaudio_devices::list_input_device_names();
            if !names.is_empty() {
                return names;
            }
            // HAL found nothing — fall through to the CPAL probe below as a
            // last resort (rare; e.g. an empty HAL device list).
        }

        let host = cpal::default_host();
        let mut names: Vec<String> = host
            .input_devices()
            .map(|devs| devs.filter_map(|d| d.name().ok()).collect())
            .unwrap_or_default();
        // Some hosts return an empty input_devices() enumeration even when a
        // usable default input exists — surface the default so the list is
        // never blank.
        if let Some(default_name) = host.default_input_device().and_then(|d| d.name().ok()) {
            if !names.iter().any(|n| n == &default_name) {
                names.insert(0, default_name);
            }
        }
        names
    }
}

// ---------------------------------------------------------------------------
// Stream builders
// ---------------------------------------------------------------------------

/// Open the chosen (or default) CPAL input device and start it. Used directly
/// in the non-voice-processing build and as the VPIO→CPAL fallback (when the
/// default output can't form a voice-processing aggregate — e.g. a USB
/// interface — and VPIO delivers no input). `gain` is a Rust-side per-frame
/// multiplier (see `build_input_stream`). Compiled in both builds now that the
/// voice-processing build can fall back to it.
fn build_cpal_input(
    host: &cpal::Host,
    input_device_name: Option<&str>,
    tx: tokio::sync::mpsc::UnboundedSender<AudioMsg>,
    aec: Arc<Mutex<AecProcessor>>,
    rms: Arc<AtomicU32>,
    gain: f32,
) -> Result<Stream, String> {
    let input_device = match input_device_name {
        Some(name) => host
            .input_devices()
            .map_err(|e| format!("enumerate input devices: {e}"))?
            .find(|d| d.name().map(|n| n == name).unwrap_or(false))
            .ok_or_else(|| format!("input device '{name}' not found"))?,
        None => host
            .default_input_device()
            .ok_or_else(|| "no default input device".to_string())?,
    };
    log::info!(
        "[audio] input device: {} (CPAL, {gain}× gain)",
        input_device.name().unwrap_or_default()
    );
    let stream = build_input_stream(&input_device, tx, aec, rms, gain)?;
    stream
        .play()
        .map_err(|e| format!("input stream play: {e}"))?;
    Ok(stream)
}

fn build_input_stream(
    device: &Device,
    tx: tokio::sync::mpsc::UnboundedSender<AudioMsg>,
    aec: Arc<Mutex<AecProcessor>>,
    rms: Arc<AtomicU32>,
    // Per-frame multiplier applied AFTER AEC, before send. 1.0 in the pure CPAL
    // build (the sidecar applies its own 16×); 16.0 in the VPIO→CPAL fallback,
    // where the sidecar is in voice_processing mode (gain 1×) but the raw
    // AGC-less mic is quiet.
    gain: f32,
) -> Result<Stream, String> {
    // Try to get a 16 kHz mono config; fall back to default supported.
    let config = preferred_input_config(device)?;
    log::info!(
        "[audio] input config: {:?} {}ch {}Hz",
        config.sample_format(),
        config.channels(),
        config.sample_rate()
    );

    let native_rate = config.sample_rate();
    let channels = config.channels() as usize;
    let stream_config: StreamConfig = config.into();

    // Accumulator: collect samples until we have MIC_FRAME_SAMPLES worth.
    let accumulator: Arc<Mutex<Vec<i16>>> = Arc::new(Mutex::new(Vec::new()));

    let stream = device
        .build_input_stream(
            &stream_config,
            {
                let accumulator = Arc::clone(&accumulator);
                move |data: &[f32], _: &cpal::InputCallbackInfo| {
                    // Downmix to mono, convert f32 → i16.
                    let mono: Vec<i16> = data
                        .chunks(channels)
                        .map(|ch| {
                            let avg = ch.iter().sum::<f32>() / channels as f32;
                            (avg * i16::MAX as f32).clamp(i16::MIN as f32, i16::MAX as f32) as i16
                        })
                        .collect();

                    // Update RMS for the UI level meter (computed on raw
                    // f32 mono before resampling for accuracy).
                    let rms_val = {
                        let sum_sq: f32 = data
                            .chunks(channels)
                            .map(|ch| {
                                let avg = ch.iter().sum::<f32>() / channels as f32;
                                avg * avg
                            })
                            .sum();
                        (sum_sq / data.len().max(1) as f32).sqrt()
                    };
                    rms.store(rms_val.to_bits(), Ordering::Relaxed);

                    // Resample to 16 kHz if needed.
                    let resampled = if native_rate != MIC_SAMPLE_RATE {
                        resample_linear(&mono, native_rate, MIC_SAMPLE_RATE)
                    } else {
                        mono
                    };

                    // Accumulate and emit complete 20 ms frames.
                    if let Ok(mut acc) = accumulator.lock() {
                        acc.extend_from_slice(&resampled);
                        while acc.len() >= MIC_FRAME_SAMPLES {
                            let frame: Vec<i16> = acc.drain(..MIC_FRAME_SAMPLES).collect();

                            // AEC: subtract delayed reference (disabled by
                            // default — alpha=0 — so this is a passthrough;
                            // echo is handled by the half-duplex mic gate).
                            let frame = if let Ok(mut a) = aec.lock() {
                                a.process_mic(&frame)
                            } else {
                                frame
                            };

                            // Rust-side gain (VPIO fallback path only; 1.0 = no-op).
                            let frame = if gain != 1.0 {
                                frame
                                    .iter()
                                    .map(|s| {
                                        ((*s as f32) * gain)
                                            .clamp(i16::MIN as f32, i16::MAX as f32)
                                            as i16
                                    })
                                    .collect()
                            } else {
                                frame
                            };

                            let _ = tx.send(AudioMsg::MicFrame(frame));
                        }
                    }
                }
            },
            |err| log::error!("[audio] input stream error: {err}"),
            None,
        )
        .map_err(|e| format!("build input stream: {e}"))?;

    Ok(stream)
}

/// Monotonic milliseconds since the audio subsystem first started — used
/// by the half-duplex echo guard.
fn now_ms() -> u64 {
    use std::sync::OnceLock;
    use std::time::Instant;
    static START: OnceLock<Instant> = OnceLock::new();
    START.get_or_init(Instant::now).elapsed().as_millis() as u64
}

/// Build the CPAL output stream. Drains from `playback_ring` into the
/// hardware buffer; outputs silence when the ring is empty. Stamps
/// `last_play_ms` whenever real audio is played so the mic writer can run
/// half-duplex (mute the mic while, and just after, she's speaking).
fn build_output_stream(
    device: &Device,
    playback_ring: PlaybackRing,
    last_play_ms: Arc<AtomicU64>,
    playback_rms: Arc<AtomicU32>,
    output_rate: Arc<AtomicU32>,
) -> Result<Stream, String> {
    let config = preferred_output_config(device)?;
    log::info!(
        "[audio] output config: {:?} {}ch {}Hz",
        config.sample_format(),
        config.channels(),
        config.sample_rate()
    );

    let native_rate = config.sample_rate();
    // Publish the device rate so push_playback resamples each TTS chunk to it
    // once (continuous) — the callback below then just drains, no per-block
    // resampling.
    output_rate.store(native_rate, Ordering::Relaxed);
    let channels = config.channels() as usize;
    let stream_config: StreamConfig = config.into();

    // Held across callbacks so an underrun fades from the last real sample
    // instead of snapping to silence.
    let mut prev_last: i16 = 0;
    let stream = device
        .build_output_stream(
            &stream_config,
            move |data: &mut [f32], _: &cpal::OutputCallbackInfo| {
                let frames_needed = data.len() / channels;

                // The ring already holds samples at the device rate
                // (push_playback resampled each chunk once, with full
                // context), so just drain what we need — no per-block
                // resampling, which was the dominant crackle source.
                let mut pcm: Vec<i16> = Vec::with_capacity(frames_needed);
                if let Ok(mut ring) = playback_ring.lock() {
                    let take = frames_needed.min(ring.len());
                    if take > 0 {
                        // Real TTS audio is going out the speaker now.
                        last_play_ms.store(now_ms(), Ordering::Relaxed);
                    }
                    pcm.extend(ring.drain(..take));
                }
                // Underrun: ramp the tail down to zero instead of a hard cut
                // (which clicks). Start from the last real sample, or from the
                // previous callback's last sample if fully dry.
                if pcm.len() < frames_needed {
                    let start = *pcm.last().unwrap_or(&prev_last) as f32;
                    let missing = frames_needed - pcm.len();
                    for i in 0..missing {
                        let t = (i + 1) as f32 / missing as f32; // 0..1
                        pcm.push((start * (1.0 - t)) as i16);
                    }
                }
                prev_last = *pcm.last().unwrap_or(&0);

                // Playback level (0..1 RMS) for orb audio-reactivity — she
                // pulses to her own voice while speaking.
                let pb = if pcm.is_empty() {
                    0.0
                } else {
                    let sum_sq: f64 = pcm.iter().map(|&s| (s as f64).powi(2)).sum();
                    ((sum_sq / pcm.len() as f64).sqrt() / 32768.0) as f32
                };
                playback_rms.store(pb.to_bits(), Ordering::Relaxed);

                // Convert i16 → f32 and write the mono signal to the
                // first TWO channels (L+R) so ordinary stereo headphones
                // get sound in both ears. Channels beyond the first two
                // (pro/multi-out interfaces — Rodecaster, aggregates,
                // monitor + USB returns) stay silent so we don't bleed
                // into auxiliary buses the user isn't monitoring.
                for (frame_idx, frame) in data.chunks_mut(channels).enumerate() {
                    let s = pcm.get(frame_idx).copied().unwrap_or(0);
                    let f = s as f32 / i16::MAX as f32;
                    for (i, ch) in frame.iter_mut().enumerate() {
                        *ch = if i < 2 { f } else { 0.0 };
                    }
                }
            },
            |err| log::error!("[audio] output stream error: {err}"),
            None,
        )
        .map_err(|e| format!("build output stream: {e}"))?;

    Ok(stream)
}

// ---------------------------------------------------------------------------
// Device config helpers
// ---------------------------------------------------------------------------

fn preferred_input_config(device: &Device) -> Result<cpal::SupportedStreamConfig, String> {
    // Prefer 16 kHz mono f32 → i16 conversion happens in callback.
    // Fall back to default if unsupported.
    let supported = device
        .supported_input_configs()
        .map_err(|e| format!("supported input configs: {e}"))?;

    for range in supported {
        if range.channels() == 1
            && range.sample_format() == SampleFormat::F32
            && range.min_sample_rate() <= MIC_SAMPLE_RATE
            && range.max_sample_rate() >= MIC_SAMPLE_RATE
        {
            return Ok(range.with_sample_rate(MIC_SAMPLE_RATE));
        }
    }
    // Fallback: use device default (we'll resample in the callback).
    device
        .default_input_config()
        .map_err(|e| format!("default input config: {e}"))
}

fn preferred_output_config(device: &Device) -> Result<cpal::SupportedStreamConfig, String> {
    let supported = device
        .supported_output_configs()
        .map_err(|e| format!("supported output configs: {e}"))?;

    for range in supported {
        if range.sample_format() == SampleFormat::F32
            && range.min_sample_rate() <= TTS_SAMPLE_RATE
            && range.max_sample_rate() >= TTS_SAMPLE_RATE
        {
            return Ok(range.with_sample_rate(TTS_SAMPLE_RATE));
        }
    }
    device
        .default_output_config()
        .map_err(|e| format!("default output config: {e}"))
}

// ---------------------------------------------------------------------------
// Simple linear resampler (placeholder — Phase 3 uses rubato)
// ---------------------------------------------------------------------------

/// Resample `input` from `from_rate` Hz to `to_rate` Hz using linear
/// interpolation. Sufficient for the AEC reference path and device-rate
/// bridging; Phase 3 replaces with rubato for quality.
fn resample_linear(input: &[i16], from_rate: u32, to_rate: u32) -> Vec<i16> {
    if from_rate == to_rate || input.is_empty() {
        return input.to_vec();
    }
    let ratio = from_rate as f64 / to_rate as f64;
    let out_len = ((input.len() as f64) / ratio).ceil() as usize;
    let mut out = Vec::with_capacity(out_len);
    for i in 0..out_len {
        let src_pos = i as f64 * ratio;
        let src_idx = src_pos as usize;
        let frac = src_pos - src_idx as f64;
        let a = input.get(src_idx).copied().unwrap_or(0) as f64;
        let b = input.get(src_idx + 1).copied().unwrap_or(0) as f64;
        out.push(
            (a + frac * (b - a))
                .round()
                .clamp(i16::MIN as f64, i16::MAX as f64) as i16,
        );
    }
    out
}

/// Resample using Catmull-Rom cubic interpolation. Used for the
/// TTS → speaker playback path, where linear upsampling (24k→48k) aliased
/// audibly ("crackle"/buzz). Stateless, low-risk improvement over linear;
/// a full band-limited (rubato) resampler is a later option if needed.
fn resample_cubic(input: &[i16], from_rate: u32, to_rate: u32) -> Vec<i16> {
    if from_rate == to_rate || input.is_empty() {
        return input.to_vec();
    }
    let ratio = from_rate as f64 / to_rate as f64;
    let out_len = ((input.len() as f64) / ratio).ceil() as usize;
    let n = input.len() as i64;
    let at = |idx: i64| -> f64 { input[idx.clamp(0, n - 1) as usize] as f64 };
    let mut out = Vec::with_capacity(out_len);
    for i in 0..out_len {
        let src_pos = i as f64 * ratio;
        let i0 = src_pos.floor() as i64;
        let t = src_pos - i0 as f64;
        let (p0, p1, p2, p3) = (at(i0 - 1), at(i0), at(i0 + 1), at(i0 + 2));
        // Catmull-Rom spline through p1..p2.
        let val = 0.5
            * ((2.0 * p1)
                + (-p0 + p2) * t
                + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * t * t
                + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * t * t * t);
        out.push(val.round().clamp(i16::MIN as f64, i16::MAX as f64) as i16);
    }
    out
}

/// Decimate 24 kHz PCM to 16 kHz by dropping every 3rd sample out of
/// each group of 3 (2 out of 3 kept). Used for the AEC reference path
/// only — not for playback quality. Phase 3 uses rubato here.
fn decimate_24k_to_16k(samples: &[i16]) -> Vec<i16> {
    // 24000 / 16000 = 3/2 — keep 2 out of every 3 samples.
    resample_linear(samples, TTS_SAMPLE_RATE, MIC_SAMPLE_RATE)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resample_upsample_length() {
        // Upsample 8 samples from 16 kHz to 24 kHz → should produce 12.
        let input: Vec<i16> = (0..8).map(|i| i * 1000).collect();
        let out = resample_linear(&input, 16_000, 24_000);
        assert_eq!(out.len(), 12, "expected 12 samples, got {}", out.len());
    }

    #[test]
    fn resample_downsample_length() {
        // Downsample 12 samples from 24 kHz to 16 kHz → should produce 8.
        let input: Vec<i16> = (0..12).map(|i| i * 1000).collect();
        let out = resample_linear(&input, 24_000, 16_000);
        assert_eq!(out.len(), 8, "expected 8 samples, got {}", out.len());
    }

    #[test]
    fn resample_noop_when_rates_equal() {
        let input: Vec<i16> = vec![100, 200, 300];
        let out = resample_linear(&input, 16_000, 16_000);
        assert_eq!(out, input);
    }

    #[test]
    fn decimate_halves_length() {
        // 24 samples at 24 kHz → 16 samples at 16 kHz.
        let input: Vec<i16> = vec![1000i16; 24];
        let out = decimate_24k_to_16k(&input);
        assert_eq!(out.len(), 16);
    }
}
