//! Native audio engine for ORBIS desktop.
//!
//! All code here is compiled only when the `native-audio` Cargo feature
//! is enabled. Production macOS builds also enable `voice-processing`,
//! which routes microphone input through AVAudioEngine's Apple-tuned
//! AEC + AGC + noise-suppression chain. Output remains CPAL.
//!
//! Architecture:
//!
//!   AVAudioEngine/CPAL mic capture → socket → Python sidecar
//!   Python sidecar               → socket → CPAL playback ring

pub mod aec;
pub mod engine;
pub mod socket;

// Phase 2 — opt-in via the `voice-processing` Cargo feature. Replaces
// the CPAL input path with AVAudioEngine voice-processing IO (Apple-
// tuned AEC + AGC + NS, per WWDC23-10235). Output stays on CPAL.
#[cfg(all(feature = "voice-processing", target_os = "macos"))]
pub mod voice_processing_input;

// Core Audio input-device selection for the voice-processing path (orbis-zj5):
// AVAudioEngine ignores a CPAL device name, so we pin the AudioDeviceID on its
// input node's audio unit.
#[cfg(all(feature = "voice-processing", target_os = "macos"))]
pub mod coreaudio_devices;
