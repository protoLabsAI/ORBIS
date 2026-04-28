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
