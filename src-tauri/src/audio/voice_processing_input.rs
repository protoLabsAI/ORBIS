//! AVAudioEngine voice-processing input (Phase 2).
//!
//! macOS-only Apple-native input path that replaces CPAL's microphone
//! capture. With voice-processing IO enabled, the input node ships
//! samples that have already passed through Apple's tuned AEC + AGC +
//! noise-suppression chain (per WWDC23-10235, "What's new in voice
//! processing"). Phase 1's 8× software gain in the Python sidecar and
//! the STT_MIN_RMS hallucination gates exist *because* the CPAL input
//! path has none of those — Phase 2 makes them unnecessary.
//!
//! Reference:
//! - WWDC23-10235: <https://developer.apple.com/videos/play/wwdc2023/10235/>
//! - Apple docs: <https://developer.apple.com/documentation/avfaudio/audio_engine/audio_units/using_voice_processing>
//! - objc2-avf-audio: <https://docs.rs/objc2-avf-audio>
//!
//! The tap updates the same RMS meter as the CPAL path and logs the
//! first callback so production diagnostics can distinguish "permission
//! granted but no render callbacks" from downstream STT issues.

use std::ptr::NonNull;
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use block2::RcBlock;
use objc2::rc::Retained;
use objc2::runtime::{Bool, NSObjectProtocol};
use objc2::{sel, AnyThread};
use objc2_avf_audio::{
    AVAudioEngine, AVAudioInputNode, AVAudioNode, AVAudioNodeBus, AVAudioPCMBuffer, AVAudioTime,
    AVAudioVoiceProcessingOtherAudioDuckingConfiguration,
    AVAudioVoiceProcessingOtherAudioDuckingLevel,
};

use super::engine::{AudioMsg, MIC_FRAME_SAMPLES, MIC_SAMPLE_RATE};

const AUDIBLE_RMS_THRESHOLD: f32 = 0.001;

/// Owns the AVAudioEngine + tap installation. Drops the engine and
/// removes the tap on `Drop`.
pub struct VoiceProcessingInput {
    engine: Retained<AVAudioEngine>,
    /// Held so the underlying Obj-C block is retained for the engine's
    /// lifetime. AVAudioEngine retains a strong reference to the block
    /// when the tap is installed; we keep one too just for safety
    /// (matches AVFoundation Obj-C samples).
    _tap_block: RcBlock<dyn Fn(NonNull<AVAudioPCMBuffer>, NonNull<AVAudioTime>)>,
}

// AVAudioEngine + RcBlock are not Send/Sync by default. We only ever
// drop from the same task that constructed us, and the tap callback
// runs on AVAudioEngine's render thread (managed by Apple) — Rust just
// owns the struct. Tauri's managed-state pattern requires Send + Sync,
// so we assert it explicitly.
unsafe impl Send for VoiceProcessingInput {}
unsafe impl Sync for VoiceProcessingInput {}

impl VoiceProcessingInput {
    /// Build the engine, enable voice-processing, install the tap, and
    /// start. Frames are sent to `tx` as `AudioMsg::MicFrame(Vec<i16>)`
    /// of `MIC_FRAME_SAMPLES` samples each.
    pub fn new(
        tx: tokio::sync::mpsc::UnboundedSender<AudioMsg>,
        rms: Arc<AtomicU32>,
        input_device_id: Option<u32>,
    ) -> Result<Self, String> {
        // SAFETY: AVAudioEngine, the input node, and tap installation
        // are all standard AVFAudio API surface. The tap block runs on
        // a render thread Apple manages — we send samples through an
        // mpsc UnboundedSender (Send + Sync) so no shared mutable
        // state escapes the block.
        unsafe {
            let engine: Retained<AVAudioEngine> = AVAudioEngine::init(AVAudioEngine::alloc());

            let input_node: Retained<AVAudioInputNode> = engine.inputNode();
            let _input_av_node: &AVAudioNode = &*input_node;

            // Pin a specific input device (else system default) BEFORE enabling
            // voice-processing — setting it AFTER doesn't rebind the already-
            // realized input format (it stayed on the default 5ch aggregate).
            // AVAudioEngine ignores device *names*, so set the AudioDeviceID on
            // the input node's underlying AUHAL. orbis-zj5.
            if let Some(dev_id) = input_device_id {
                let au: *mut std::os::raw::c_void = objc2::msg_send![&*input_node, audioUnit];
                if au.is_null() {
                    log::warn!(
                        "[voice-processing] input node has no audioUnit; using default device"
                    );
                } else if let Err(e) =
                    super::coreaudio_devices::set_current_input_device(au, dev_id)
                {
                    log::warn!("[voice-processing] couldn't pin input device {dev_id}: {e} — using default");
                } else {
                    log::info!("[voice-processing] input device pinned: AudioDeviceID {dev_id}");
                }
            }

            // Enable voice processing — the whole point of Phase 2.
            // Returns an NSError if the audio session can't be put
            // into voice-processing mode (some older Macs, certain
            // device combinations). We surface the error so the caller
            // can fall back to the CPAL path during the soak window.
            input_node
                .setVoiceProcessingEnabled_error(true)
                .map_err(|e| {
                    format!("AVAudioInputNode::setVoiceProcessingEnabled(true) failed: {e:?}")
                })?;

            // macOS treats a voice-processing input like a VoIP call and
            // ducks ALL other system audio for the engine's whole lifetime
            // — so music/video go quiet the entire time ORBIS runs, even
            // idling muted. macOS 14+ lets us tune that. Optional via
            // ORBIS_VP_DUCKING:
            //   min (default) — minimal duck, advanced (voice-activity) off
            //   mid | max     — progressively more ducking
            //   default/full  — leave Apple's full VoIP ducking on
            // Guarded by respondsToSelector since the selector is macOS 14+
            // and the deployment target is 13.
            if input_node
                .respondsToSelector(sel!(setVoiceProcessingOtherAudioDuckingConfiguration:))
            {
                let pref = std::env::var("ORBIS_VP_DUCKING").unwrap_or_else(|_| "min".to_string());
                let level = match pref.as_str() {
                    "max" => Some(AVAudioVoiceProcessingOtherAudioDuckingLevel::Max),
                    "mid" => Some(AVAudioVoiceProcessingOtherAudioDuckingLevel::Mid),
                    "default" | "full" | "on" => None, // leave Apple's default
                    _ => Some(AVAudioVoiceProcessingOtherAudioDuckingLevel::Min),
                };
                match level {
                    Some(level) => {
                        input_node.setVoiceProcessingOtherAudioDuckingConfiguration(
                            AVAudioVoiceProcessingOtherAudioDuckingConfiguration {
                                enableAdvancedDucking: Bool::new(false),
                                duckingLevel: level,
                            },
                        );
                        log::info!(
                            "[voice-processing] other-audio ducking = {pref} (advanced off)"
                        );
                    }
                    None => {
                        log::info!("[voice-processing] other-audio ducking = default (Apple VoIP)");
                    }
                }
            }

            let format = input_node.outputFormatForBus(0 as AVAudioNodeBus);
            log::info!(
                "[voice-processing] input format: {} ch @ {} Hz, voice-processing={}",
                format.channelCount(),
                format.sampleRate(),
                input_node.isVoiceProcessingEnabled()
            );

            let native_rate = format.sampleRate() as u32;
            let native_channels = format.channelCount() as usize;

            // Resampling + framing state shared with the tap callback.
            // Mutex is fine — the render thread is the only writer.
            let state = std::sync::Arc::new(Mutex::new(TapState {
                accumulator: Vec::with_capacity(MIC_FRAME_SAMPLES * 4),
                native_rate,
                native_channels,
                rms,
                tap_count: Arc::new(AtomicU64::new(0)),
                audible_logged: Arc::new(AtomicBool::new(false)),
            }));

            let state_clone = std::sync::Arc::clone(&state);
            let tx_clone = tx.clone();
            let tap_block = RcBlock::new(
                move |buf: NonNull<AVAudioPCMBuffer>, _time: NonNull<AVAudioTime>| {
                    handle_tap(&state_clone, &tx_clone, buf);
                },
            );

            // Install the tap on bus 0 with the native format. Buffer
            // size 1024 frames is Apple's recommended starting point
            // for low-latency voice work; the engine may deliver
            // smaller buffers.
            input_node.installTapOnBus_bufferSize_format_block(
                0,
                1024,
                Some(&format),
                &*tap_block as *const _ as *mut _,
            );

            engine
                .startAndReturnError()
                .map_err(|e| format!("AVAudioEngine::startAndReturnError: {e:?}"))?;

            log::info!("[voice-processing] engine started — AEC + AGC + NS active");

            Ok(Self {
                engine,
                _tap_block: tap_block,
            })
        }
    }
}

impl Drop for VoiceProcessingInput {
    fn drop(&mut self) {
        unsafe {
            let input_node = self.engine.inputNode();
            input_node.removeTapOnBus(0);
            self.engine.stop();
            log::info!("[voice-processing] engine stopped");
        }
    }
}

struct TapState {
    /// i16 samples at MIC_SAMPLE_RATE waiting to be packed into
    /// MIC_FRAME_SAMPLES-sized chunks for the socket layer.
    accumulator: Vec<i16>,
    native_rate: u32,
    native_channels: usize,
    rms: Arc<AtomicU32>,
    tap_count: Arc<AtomicU64>,
    audible_logged: Arc<AtomicBool>,
}

/// Tap callback body. Runs on AVAudioEngine's render thread.
///
/// SAFETY: caller (the Obj-C block) ensures `buf` is a valid
/// `AVAudioPCMBuffer` pointer for the duration of the call.
unsafe fn handle_tap(
    state: &std::sync::Arc<Mutex<TapState>>,
    tx: &tokio::sync::mpsc::UnboundedSender<AudioMsg>,
    buf: NonNull<AVAudioPCMBuffer>,
) {
    let buf_ref = unsafe { buf.as_ref() };
    let frame_length = buf_ref.frameLength() as usize;
    if frame_length == 0 {
        return;
    }

    // floatChannelData returns a pointer to channelCount pointers to f32.
    // For voice-processing input, this is typically 1 channel (mono).
    let channel_data = unsafe { buf_ref.floatChannelData() };
    if channel_data.is_null() {
        // Buffer isn't f32 — voice-processing should always give us f32,
        // but bail safely if not.
        return;
    }

    let Ok(mut state) = state.lock() else {
        return;
    };

    // Downmix to mono f32 in a small scratch vec on the stack.
    // floatChannelData returns *mut NonNull<f32> — outer ptr to an
    // array of channelCount channel pointers, each pointing at
    // frameLength f32 samples.
    let native_channels = state.native_channels;
    let mut mono: Vec<f32> = Vec::with_capacity(frame_length);
    if native_channels == 1 {
        let chan0_ptr = unsafe { (*channel_data).as_ptr() };
        for i in 0..frame_length {
            mono.push(unsafe { *chan0_ptr.add(i) });
        }
    } else {
        // Some VP setups (esp. an aggregate default device) hand us a buffer
        // with the mic on ONE channel and the rest silent/reference. Averaging
        // diluted the voice by ~Nx into near-silence (the dead-meter bug) — pick
        // the highest-energy channel (the active mic) and use it. orbis-zj5.
        let mut best_ch = 0usize;
        let mut best_energy = -1.0_f32;
        for ch in 0..native_channels {
            let chan_ptr = unsafe { (*channel_data.add(ch)).as_ptr() };
            let mut e = 0.0_f32;
            for i in 0..frame_length {
                let s = unsafe { *chan_ptr.add(i) };
                e += s * s;
            }
            if e > best_energy {
                best_energy = e;
                best_ch = ch;
            }
        }
        let chan_ptr = unsafe { (*channel_data.add(best_ch)).as_ptr() };
        for i in 0..frame_length {
            mono.push(unsafe { *chan_ptr.add(i) });
        }
    }

    let rms_val = {
        let sum_sq: f32 = mono.iter().map(|sample| sample * sample).sum();
        (sum_sq / mono.len().max(1) as f32).sqrt().min(1.0)
    };
    state.rms.store(rms_val.to_bits(), Ordering::Relaxed);

    let tap_index = state.tap_count.fetch_add(1, Ordering::Relaxed);
    if tap_index == 0 {
        log::info!(
            "[voice-processing] first input tap: {} frames, {} channel(s), rms={:.4}",
            frame_length,
            native_channels,
            rms_val
        );
    }
    if rms_val >= AUDIBLE_RMS_THRESHOLD && !state.audible_logged.swap(true, Ordering::Relaxed) {
        log::info!(
            "[voice-processing] input became audible: rms={:.4}",
            rms_val
        );
    }

    // Resample to MIC_SAMPLE_RATE if needed (linear; same algorithm
    // as the CPAL path until Phase 2.5 gates a quality upgrade).
    let resampled = if state.native_rate == MIC_SAMPLE_RATE {
        // Convert f32 [-1, 1] → i16 in place.
        mono.iter()
            .map(|s| (s * i16::MAX as f32).clamp(i16::MIN as f32, i16::MAX as f32) as i16)
            .collect::<Vec<i16>>()
    } else {
        let ratio = state.native_rate as f64 / MIC_SAMPLE_RATE as f64;
        let out_len = ((mono.len() as f64) / ratio).ceil() as usize;
        let mut out = Vec::with_capacity(out_len);
        for i in 0..out_len {
            let src_pos = i as f64 * ratio;
            let src_idx = src_pos as usize;
            let frac = src_pos - src_idx as f64;
            let a = mono.get(src_idx).copied().unwrap_or(0.0) as f64;
            let b = mono.get(src_idx + 1).copied().unwrap_or(0.0) as f64;
            let v = a + frac * (b - a);
            out.push(
                (v * i16::MAX as f64)
                    .clamp(i16::MIN as f64, i16::MAX as f64)
                    .round() as i16,
            );
        }
        out
    };

    // Accumulate and emit complete MIC_FRAME_SAMPLES chunks.
    state.accumulator.extend_from_slice(&resampled);
    while state.accumulator.len() >= MIC_FRAME_SAMPLES {
        let frame: Vec<i16> = state.accumulator.drain(..MIC_FRAME_SAMPLES).collect();
        let _ = tx.send(AudioMsg::MicFrame(frame));
    }
}
