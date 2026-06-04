//! Unix socket IPC between the Rust native audio engine and the Python sidecar.
//!
//! Wire protocol — all fields little-endian:
//!
//!   Header (8 bytes):
//!     [0..2]  u16  direction
//!               0x0001  mic → python
//!               0x0002  python → speaker
//!               0x0010  control frame (no PCM body)
//!     [2..4]  u16  sample_rate  (16000 | 24000)
//!     [4..6]  u16  channels     (always 1)
//!     [6..8]  u16  num_samples  (payload length in samples)
//!
//!   Body (num_samples × 2 bytes, i16 LE): PCM payload
//!         or for control frames (direction=0x0010):
//!     [0..2]  u16  control_code
//!               0x0001  barge-in interrupt — flush playback ring
//!               0x0002  TTS stream ended
//!     [2..8]  reserved (must be zero)
//!
//! Socket path: `$TMPDIR/orbis-audio-{pid}.sock`
//! Python reads this from `$ORBIS_AUDIO_SOCK`.

use std::path::PathBuf;
use std::sync::Arc;

use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::UnixListener;
use tokio::sync::mpsc;

use super::engine::{AudioEngine, AudioMsg, MIC_SAMPLE_RATE};

// Direction constants.
pub const DIR_MIC_TO_PYTHON: u16 = 0x0001;
pub const DIR_PYTHON_TO_SPEAKER: u16 = 0x0002;
pub const DIR_CONTROL: u16 = 0x0010;

/// Half-duplex echo-guard window (ms). Mic frames are dropped while real
/// TTS audio is playing and for this long after the last played sample —
/// covering the buffered playback tail so her own voice (which bleeds
/// into the laptop mic acoustically) never reaches STT.
const ECHO_GUARD_MS: u64 = 400;

// Control codes.
pub const CTRL_BARGE_IN: u16 = 0x0001;
pub const CTRL_TTS_END: u16 = 0x0002;
/// Python → Rust: the user said a cancel/dismiss phrase ("cancel", "never mind",
/// "stop listening") — close the listening window. Mutes the mic; in wake mode
/// the detector re-arms back to waiting for the phrase.
pub const CTRL_STOP_LISTENING: u16 = 0x0003;

const HEADER_LEN: usize = 8;

/// Compute the socket path for this process.
pub fn socket_path() -> PathBuf {
    let pid = std::process::id();
    let tmp = std::env::var("TMPDIR").unwrap_or_else(|_| "/tmp".to_string());
    PathBuf::from(tmp).join(format!("orbis-audio-{pid}.sock"))
}

/// Encode a PCM frame into the wire format.
pub fn encode_frame(direction: u16, sample_rate: u32, samples: &[i16]) -> Vec<u8> {
    let num_samples = samples.len() as u16;
    let mut buf = Vec::with_capacity(HEADER_LEN + samples.len() * 2);
    buf.extend_from_slice(&direction.to_le_bytes());
    buf.extend_from_slice(&(sample_rate as u16).to_le_bytes());
    buf.extend_from_slice(&1u16.to_le_bytes()); // channels
    buf.extend_from_slice(&num_samples.to_le_bytes());
    for &s in samples {
        buf.extend_from_slice(&s.to_le_bytes());
    }
    buf
}

/// Encode a control frame.
pub fn encode_control(control_code: u16) -> Vec<u8> {
    let mut buf = vec![0u8; HEADER_LEN + 6];
    buf[0..2].copy_from_slice(&DIR_CONTROL.to_le_bytes());
    buf[2..4].copy_from_slice(&0u16.to_le_bytes()); // sample_rate unused
    buf[4..6].copy_from_slice(&0u16.to_le_bytes()); // channels unused
    buf[6..8].copy_from_slice(&3u16.to_le_bytes()); // num_samples = body words
    buf[8..10].copy_from_slice(&control_code.to_le_bytes());
    // [10..14] reserved = 0
    buf
}

/// Decode a frame header. Returns (direction, sample_rate, channels, num_samples).
pub fn decode_header(buf: &[u8; HEADER_LEN]) -> (u16, u16, u16, u16) {
    let direction = u16::from_le_bytes([buf[0], buf[1]]);
    let sample_rate = u16::from_le_bytes([buf[2], buf[3]]);
    let channels = u16::from_le_bytes([buf[4], buf[5]]);
    let num_samples = u16::from_le_bytes([buf[6], buf[7]]);
    (direction, sample_rate, channels, num_samples)
}

/// The Unix socket server. Binds once, accepts one client (Python
/// sidecar). The single-client model matches Pipecat's single-pipeline
/// assumption.
pub struct SocketServer {
    path: PathBuf,
    listener: UnixListener,
}

impl SocketServer {
    /// Bind the socket at `socket_path()`. Call before spawning the
    /// sidecar so the socket exists when Python tries to connect.
    pub fn bind() -> Result<Self, String> {
        let path = socket_path();
        // Remove stale socket from a previous crashed run.
        let _ = std::fs::remove_file(&path);
        let listener =
            UnixListener::bind(&path).map_err(|e| format!("bind {}: {e}", path.display()))?;
        log::info!("[audio/socket] listening on {}", path.display());
        Ok(Self { path, listener })
    }

    /// The socket path — pass this to the sidecar as `ORBIS_AUDIO_SOCK`.
    pub fn path(&self) -> &PathBuf {
        &self.path
    }

    /// Accept one connection and run the bidirectional IPC loop.
    ///
    /// - Spawns a writer task: drains `mic_rx` and writes frames to the socket.
    /// - Runs a reader loop in the current task: reads TTS frames and control
    ///   frames from the socket and acts on them via `engine`.
    ///
    /// Returns when the connection closes or an unrecoverable error occurs.
    pub async fn accept_and_run(
        &self,
        engine: Arc<AudioEngine>,
        mut mic_rx: mpsc::UnboundedReceiver<AudioMsg>,
        wake: Option<(
            super::wake_word::WakeConfig,
            super::wake_word::WakeStateEmitter,
        )>,
    ) -> Result<(), String> {
        log::info!("[audio/socket] waiting for Python to connect…");
        let (stream, _addr) = self
            .listener
            .accept()
            .await
            .map_err(|e| format!("accept: {e}"))?;
        log::info!("[audio/socket] Python connected");

        let (mut reader, mut writer) = stream.into_split();

        // Mic gate: push-to-talk (set_mic_listening) + half-duplex echo
        // guard. Frames are still drained from the channel but dropped
        // (not forwarded) when muted OR while she's speaking — so the
        // sidecar never hears her own TTS (which bleeds into the laptop
        // mic acoustically → would feed back as a user turn) or
        // hallucinates on silence.
        let eng = Arc::clone(&engine);

        // Wake-word detector (only in wake_word activation mode). Runs on its
        // own OS thread; the writer tees every mic frame to it BEFORE the gate,
        // so it can listen for the phrase while the mic is otherwise muted.
        let det_tx = wake
            .map(|(cfg, emit)| super::wake_word::spawn_detector(cfg, Arc::clone(&engine), emit));

        // Writer task: take mic frames from the channel and send to Python.
        let write_task = tokio::spawn(async move {
            while let Some(msg) = mic_rx.recv().await {
                match msg {
                    AudioMsg::MicFrame(samples) => {
                        // Feed the detector regardless of the gate — wake mode
                        // listens for "Hey Orbis" while muted. Cheap (~640 B).
                        if let Some(ref dtx) = det_tx {
                            let _ = dtx.send(samples.clone());
                        }
                        if !eng.is_listening() || eng.echo_guard_active(ECHO_GUARD_MS) {
                            continue; // muted, or she's speaking — drop the frame
                        }
                        let frame = encode_frame(DIR_MIC_TO_PYTHON, MIC_SAMPLE_RATE, &samples);
                        if writer.write_all(&frame).await.is_err() {
                            break;
                        }
                    }
                }
            }
        });

        // Reader loop: receive TTS PCM and control frames from Python.
        let mut header_buf = [0u8; HEADER_LEN];
        let mut playback_frames_received = 0usize;
        loop {
            if reader.read_exact(&mut header_buf).await.is_err() {
                break; // EOF or error → Python disconnected
            }
            let (direction, _sample_rate, _channels, num_samples) = decode_header(&header_buf);

            let body_bytes = num_samples as usize * 2;
            let mut body = vec![0u8; body_bytes];
            if reader.read_exact(&mut body).await.is_err() {
                break;
            }

            match direction {
                DIR_PYTHON_TO_SPEAKER => {
                    // TTS PCM — push to playback ring.
                    let samples: Vec<i16> = body
                        .chunks_exact(2)
                        .map(|b| i16::from_le_bytes([b[0], b[1]]))
                        .collect();
                    playback_frames_received += 1;
                    if playback_frames_received == 1 {
                        log::info!(
                            "[audio/socket] first playback frame received: samples={}",
                            samples.len()
                        );
                    }
                    engine.push_playback(&samples);
                }
                DIR_CONTROL => {
                    if body.len() >= 2 {
                        let code = u16::from_le_bytes([body[0], body[1]]);
                        match code {
                            CTRL_BARGE_IN => {
                                log::info!("[audio/socket] barge-in interrupt — flushing playback");
                                engine.flush_playback();
                            }
                            CTRL_TTS_END => {
                                log::debug!("[audio/socket] TTS stream ended");
                            }
                            CTRL_STOP_LISTENING => {
                                log::info!("[audio/socket] stop-listening — closing window");
                                engine.set_listening(false);
                            }
                            _ => {
                                log::warn!("[audio/socket] unknown control code 0x{code:04x}");
                            }
                        }
                    }
                }
                other => {
                    log::warn!("[audio/socket] unexpected direction 0x{other:04x}, skipping");
                }
            }
        }

        write_task.abort();
        log::info!("[audio/socket] connection closed");
        Ok(())
    }
}

impl Drop for SocketServer {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.path);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn socket_path_contains_pid() {
        let path = socket_path();
        let pid = std::process::id().to_string();
        assert!(
            path.to_string_lossy().contains(&pid),
            "socket path {path:?} should contain PID {pid}"
        );
    }

    #[test]
    fn encode_decode_pcm_frame_roundtrip() {
        let samples: Vec<i16> = vec![100, -200, 300, i16::MAX, i16::MIN];
        let encoded = encode_frame(DIR_MIC_TO_PYTHON, MIC_SAMPLE_RATE, &samples);

        assert_eq!(encoded.len(), HEADER_LEN + samples.len() * 2);

        let header: [u8; HEADER_LEN] = encoded[..HEADER_LEN].try_into().unwrap();
        let (dir, sr, ch, ns) = decode_header(&header);
        assert_eq!(dir, DIR_MIC_TO_PYTHON);
        assert_eq!(sr, MIC_SAMPLE_RATE as u16);
        assert_eq!(ch, 1);
        assert_eq!(ns, samples.len() as u16);

        let decoded: Vec<i16> = encoded[HEADER_LEN..]
            .chunks_exact(2)
            .map(|b| i16::from_le_bytes([b[0], b[1]]))
            .collect();
        assert_eq!(decoded, samples);
    }

    #[test]
    fn encode_control_frame_roundtrip() {
        let encoded = encode_control(CTRL_BARGE_IN);
        let header: [u8; HEADER_LEN] = encoded[..HEADER_LEN].try_into().unwrap();
        let (dir, _, _, _) = decode_header(&header);
        assert_eq!(dir, DIR_CONTROL);
        let code = u16::from_le_bytes([encoded[HEADER_LEN], encoded[HEADER_LEN + 1]]);
        assert_eq!(code, CTRL_BARGE_IN);
    }

    #[test]
    fn socket_path_is_in_tmp() {
        let path = socket_path();
        let path_str = path.to_string_lossy();
        assert!(
            path_str.contains("orbis-audio-"),
            "expected 'orbis-audio-' in path, got {path_str}"
        );
    }
}
