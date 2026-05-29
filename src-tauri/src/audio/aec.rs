//! Acoustic echo cancellation — Phase 1 implementation.
//!
//! Uses a simple delay-and-subtract approach: we maintain a ring buffer
//! of recently-played TTS reference samples and subtract a scaled,
//! delay-aligned copy from the incoming mic signal. This eliminates the
//! gross echo without requiring any C dependencies.
//!
//! Phase 3 will replace this with speexdsp (NLMS-based AEC) for better
//! double-talk handling and non-linear echo suppression. The interface
//! is intentionally identical so the swap is mechanical.
//!
//! All samples are i16 at 16 kHz mono (the mic capture rate). The TTS
//! reference is resampled to 16 kHz before being fed here.

use std::collections::VecDeque;

/// Delay-and-subtract AEC processor.
///
/// Configurable via environment variables:
///   `AEC_DELAY_MS`  — estimated playback delay in ms (default: 120)
///   `AEC_ALPHA`     — suppression coefficient 0.0–1.0 (default: 0.85)
pub struct AecProcessor {
    /// Ring buffer of reference (TTS playback) samples at 16 kHz.
    /// Sized to hold `delay_samples` + one extra frame of lookahead.
    reference_buf: VecDeque<i16>,
    /// Estimated round-trip delay from push_reference to audible
    /// output, in samples at 16 kHz.
    delay_samples: usize,
    /// Suppression coefficient applied to the aligned reference before
    /// subtraction. 0.0 = no cancellation; 1.0 = full subtraction.
    alpha: f32,
}

impl AecProcessor {
    /// Create a new processor.
    ///
    /// `delay_ms` — estimated playback latency (speaker + driver).
    ///              Typical on macOS: 80–150 ms.
    /// `alpha`    — suppression strength [0.0, 1.0].
    pub fn new(delay_ms: u32, alpha: f32) -> Self {
        let delay_samples = (delay_ms as usize * 16_000) / 1_000;
        // Pre-fill the ring buffer with silence so the first mic frame
        // doesn't subtract garbage.
        let mut reference_buf = VecDeque::with_capacity(delay_samples + 1024);
        for _ in 0..delay_samples {
            reference_buf.push_back(0i16);
        }
        Self {
            reference_buf,
            delay_samples,
            alpha,
        }
    }

    /// Build from environment variables, with defaults.
    pub fn from_env() -> Self {
        let delay_ms = std::env::var("AEC_DELAY_MS")
            .ok()
            .and_then(|v| v.parse::<u32>().ok())
            .unwrap_or(120);
        let alpha = std::env::var("AEC_ALPHA")
            .ok()
            .and_then(|v| v.parse::<f32>().ok())
            .unwrap_or(0.85)
            .clamp(0.0, 1.0);
        Self::new(delay_ms, alpha)
    }

    /// Feed TTS playback samples into the reference buffer.
    ///
    /// Call this every time a chunk of TTS audio is enqueued for
    /// playback, before it reaches the speaker. The samples must be
    /// at 16 kHz (resampled from Kokoro's 24 kHz output upstream).
    pub fn feed_reference(&mut self, samples: &[i16]) {
        for &s in samples {
            self.reference_buf.push_back(s);
        }
    }

    /// Process a mic frame, subtracting the delayed reference signal.
    ///
    /// Returns a new Vec<i16> with the echo-cancelled mic audio.
    /// Input and output are at 16 kHz mono.
    pub fn process_mic(&mut self, mic: &[i16]) -> Vec<i16> {
        let mut out = Vec::with_capacity(mic.len());
        for &m in mic {
            // Pull the oldest reference sample (delayed by delay_samples
            // because we pre-filled with silence). If the buffer is
            // shorter than expected (shouldn't happen in steady state),
            // treat as silence.
            let ref_sample = self.reference_buf.pop_front().unwrap_or(0);
            // Push silence as a replacement so the buffer length stays
            // stable. If new reference audio arrives via feed_reference
            // it will extend the tail naturally.
            self.reference_buf.push_back(0i16);

            // Subtract scaled reference from mic.
            let cancelled = (m as f32) - self.alpha * (ref_sample as f32);
            // Clamp to i16 range to prevent wrap-around.
            out.push(cancelled.clamp(i16::MIN as f32, i16::MAX as f32) as i16);
        }
        out
    }

    /// RMS of a sample slice, in linear amplitude.
    pub fn rms(samples: &[i16]) -> f32 {
        if samples.is_empty() {
            return 0.0;
        }
        let sum_sq: f64 = samples.iter().map(|&s| (s as f64).powi(2)).sum();
        ((sum_sq / samples.len() as f64).sqrt()) as f32
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Inject a known reference tone into the AEC and confirm the
    /// residual after cancellation is at least 6 dB lower than the
    /// raw mixed signal.
    ///
    /// Setup: mic = echo (0.5× reference) + orthogonal noise (0.05×).
    /// alpha=0.5 exactly cancels the echo; only the noise floor remains.
    #[test]
    fn aec_reduces_echo_by_6db() {
        const FRAME: usize = 320;
        const PI: f32 = std::f32::consts::PI;
        const FREQ: f32 = 200.0;
        const SR: f32 = 16_000.0;
        const AMP: f32 = 16_000.0; // headroom so products don't overflow i16

        // TTS reference: 200 Hz sine at AMP amplitude.
        let reference: Vec<i16> = (0..FRAME)
            .map(|i| (f32::sin(2.0 * PI * FREQ * i as f32 / SR) * AMP) as i16)
            .collect();

        // Mic = 0.5× echo of reference + 0.05× orthogonal cosine noise.
        // Using cosine ensures it survives the subtraction and gives a
        // measurable noise floor to compare against.
        let mic_with_echo: Vec<i16> = (0..FRAME)
            .map(|i| {
                let echo = reference[i] as f32 * 0.5;
                let noise = f32::cos(2.0 * PI * FREQ * i as f32 / SR) * AMP * 0.05;
                (echo + noise).clamp(i16::MIN as f32, i16::MAX as f32) as i16
            })
            .collect();

        let rms_before = AecProcessor::rms(&mic_with_echo);

        // alpha=0.5 should exactly cancel the 0.5× echo component,
        // leaving only the ~0.05× noise floor.
        let mut aec = AecProcessor::new(0, 0.5);
        aec.feed_reference(&reference);
        let cancelled = aec.process_mic(&mic_with_echo);

        let rms_after = AecProcessor::rms(&cancelled);

        // rms_before ≈ 0.5 × AMP/√2 ≈ 5657.
        // rms_after  ≈ 0.05 × AMP/√2 ≈ 565.
        // Ratio > 2× (6 dB reduction).
        assert!(
            rms_after < rms_before / 2.0,
            "expected at least 6 dB reduction, got rms_before={rms_before:.1} rms_after={rms_after:.1}"
        );
    }

    #[test]
    fn aec_passes_through_when_no_reference() {
        let mic: Vec<i16> = vec![1000, -1000, 500, -500];
        let mut aec = AecProcessor::new(0, 0.85);
        // No feed_reference — reference buffer is all zeros.
        let out = aec.process_mic(&mic);
        // With zero reference, output should equal input (no subtraction).
        for (i, (&m, &o)) in mic.iter().zip(out.iter()).enumerate() {
            assert_eq!(m, o, "sample {i}: expected {m} got {o} with zero reference");
        }
    }

    #[test]
    fn rms_of_silence_is_zero() {
        assert_eq!(AecProcessor::rms(&[0i16; 320]), 0.0);
    }
}
