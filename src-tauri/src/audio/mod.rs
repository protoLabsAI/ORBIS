//! Native CPAL audio engine for ORBIS desktop.
//!
//! All code here is compiled only when the `native-audio` Cargo feature
//! is enabled (`cargo build --features native-audio`). The runtime
//! behaviour is additionally gated on `AUDIO_TRANSPORT=native` in the
//! environment — the feature controls compilation, the env var controls
//! whether the engine actually starts.
//!
//! Architecture:
//!
//!   CPAL mic capture → AecProcessor → socket → Python sidecar
//!   Python sidecar   → socket → CPAL playback ring

pub mod aec;
pub mod engine;
pub mod socket;

// Phase 2 — opt-in via the `voice-processing` Cargo feature. Replaces
// the CPAL input path with AVAudioEngine voice-processing IO (Apple-
// tuned AEC + AGC + NS, per WWDC23-10235). Output stays on CPAL.
#[cfg(all(feature = "voice-processing", target_os = "macos"))]
pub mod voice_processing_input;
