//! CPAL audio engine — mic capture and TTS playback.
//!
//! Opens the default (or named) CoreAudio device for input at 16 kHz
//! mono and for output at 24 kHz mono. If the device doesn't natively
//! support those rates, rubato resamples in the stream callback.
//!
//! Mic frames (320 samples = 20 ms at 16 kHz) are sent to the socket
//! layer via an unbounded channel. TTS frames are received from the
//! socket layer and pushed into a playback ring buffer that the output
//! callback drains.

use std::collections::VecDeque;
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::{Arc, Mutex};

use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::{Device, SampleFormat, SampleRate, Stream, StreamConfig};

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
    /// AEC processor, shared with the input callback.
    aec: Arc<Mutex<AecProcessor>>,
    /// Current input RMS level (0.0–1.0), updated by the input callback.
    /// Stored as f32 bits in an AtomicU32 for lock-free reads from the UI.
    rms: Arc<AtomicU32>,
    // Keep streams alive — they stop when dropped.
    _input_stream: Stream,
    _output_stream: Stream,
}

// CPAL streams are !Send by default on some platforms. We only ever
// access them via drop (cleanup), so this is safe.
unsafe impl Send for AudioEngine {}
unsafe impl Sync for AudioEngine {}

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

        // --- Input device ---
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
            "[audio] input device: {}",
            input_device.name().unwrap_or_default()
        );

        // --- Output device ---
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

        let input_stream =
            build_input_stream(&input_device, tx.clone(), Arc::clone(&aec), Arc::clone(&rms))?;
        let output_stream = build_output_stream(&output_device, Arc::clone(&playback_ring))?;

        input_stream
            .play()
            .map_err(|e| format!("input stream play: {e}"))?;
        output_stream
            .play()
            .map_err(|e| format!("output stream play: {e}"))?;

        Ok(Self {
            tx,
            playback_ring,
            aec,
            rms,
            _input_stream: input_stream,
            _output_stream: output_stream,
        })
    }

    /// Current microphone RMS level (0.0–1.0), updated every mic frame.
    pub fn current_rms(&self) -> f32 {
        f32::from_bits(self.rms.load(Ordering::Relaxed))
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
        // Push to playback ring.
        if let Ok(mut ring) = self.playback_ring.lock() {
            ring.extend(samples.iter().copied());
        }
    }

    /// Flush the playback ring immediately (barge-in).
    pub fn flush_playback(&self) {
        if let Ok(mut ring) = self.playback_ring.lock() {
            ring.clear();
        }
    }

    /// List all available CPAL input device names.
    pub fn list_input_devices() -> Vec<String> {
        let host = cpal::default_host();
        host.input_devices()
            .map(|devs| {
                devs.filter_map(|d| d.name().ok()).collect()
            })
            .unwrap_or_default()
    }
}

// ---------------------------------------------------------------------------
// Stream builders
// ---------------------------------------------------------------------------

/// Build the CPAL input stream. Target 16 kHz mono i16; if the device
/// doesn't support that natively, we request the closest supported
/// config and resample in the callback.
fn build_input_stream(
    device: &Device,
    tx: tokio::sync::mpsc::UnboundedSender<AudioMsg>,
    aec: Arc<Mutex<AecProcessor>>,
    rms: Arc<AtomicU32>,
) -> Result<Stream, String> {
    // Try to get a 16 kHz mono config; fall back to default supported.
    let config = preferred_input_config(device)?;
    log::info!(
        "[audio] input config: {:?} {}ch {}Hz",
        config.sample_format(),
        config.channels(),
        config.sample_rate().0
    );

    let native_rate = config.sample_rate().0;
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
                            let frame: Vec<i16> =
                                acc.drain(..MIC_FRAME_SAMPLES).collect();

                            // AEC: subtract delayed reference.
                            let frame = if let Ok(mut a) = aec.lock() {
                                a.process_mic(&frame)
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

/// Build the CPAL output stream. Drains from `playback_ring` into the
/// hardware buffer; outputs silence when the ring is empty.
fn build_output_stream(
    device: &Device,
    playback_ring: PlaybackRing,
) -> Result<Stream, String> {
    let config = preferred_output_config(device)?;
    log::info!(
        "[audio] output config: {:?} {}ch {}Hz",
        config.sample_format(),
        config.channels(),
        config.sample_rate().0
    );

    let native_rate = config.sample_rate().0;
    let channels = config.channels() as usize;
    let stream_config: StreamConfig = config.into();

    let stream = device
        .build_output_stream(
            &stream_config,
            move |data: &mut [f32], _: &cpal::OutputCallbackInfo| {
                let frames_needed = data.len() / channels;
                // Resample TTS_SAMPLE_RATE → native_rate worth of samples.
                let ring_samples_needed = if native_rate != TTS_SAMPLE_RATE {
                    (frames_needed as u64 * TTS_SAMPLE_RATE as u64 / native_rate as u64)
                        as usize
                } else {
                    frames_needed
                };

                let mut pcm_16: Vec<i16> = Vec::with_capacity(ring_samples_needed);
                if let Ok(mut ring) = playback_ring.lock() {
                    let take = ring_samples_needed.min(ring.len());
                    pcm_16.extend(ring.drain(..take));
                }
                // Pad with silence if ring ran dry.
                pcm_16.resize(ring_samples_needed, 0i16);

                // Resample to native device rate if needed.
                let resampled = if native_rate != TTS_SAMPLE_RATE {
                    resample_linear(&pcm_16, TTS_SAMPLE_RATE, native_rate)
                } else {
                    pcm_16
                };

                // Convert i16 → f32, expand to channel count.
                for (frame_idx, frame) in data.chunks_mut(channels).enumerate() {
                    let s = resampled
                        .get(frame_idx)
                        .copied()
                        .unwrap_or(0);
                    let f = s as f32 / i16::MAX as f32;
                    for ch in frame.iter_mut() {
                        *ch = f;
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
            && range.min_sample_rate() <= SampleRate(MIC_SAMPLE_RATE)
            && range.max_sample_rate() >= SampleRate(MIC_SAMPLE_RATE)
        {
            return Ok(range.with_sample_rate(SampleRate(MIC_SAMPLE_RATE)));
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
            && range.min_sample_rate() <= SampleRate(TTS_SAMPLE_RATE)
            && range.max_sample_rate() >= SampleRate(TTS_SAMPLE_RATE)
        {
            return Ok(range.with_sample_rate(SampleRate(TTS_SAMPLE_RATE)));
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
        out.push((a + frac * (b - a)).round().clamp(i16::MIN as f64, i16::MAX as f64) as i16);
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
