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

use serde::Serialize;
use std::time::Duration;
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
/// Rust → Python: live audio mode. The first reserved body byte carries a
/// flags bitfield; bit 0 = real (VPIO) AEC active. Sent on connect (first mic
/// frame) and whenever the mode changes (VPIO→CPAL watchdog fallback). Lets the
/// sidecar default listener-acks on only when the mic won't hear the bot's tail.
pub const CTRL_AUDIO_MODE: u16 = 0x0004;
/// Audio-mode flag bit: hardware AEC (VPIO) is active.
pub const AUDIO_MODE_AEC: u8 = 0x01;

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

/// Encode a control frame carrying a single u8 flag in the first reserved byte.
pub fn encode_control_flag(control_code: u16, flag: u8) -> Vec<u8> {
    let mut buf = encode_control(control_code);
    buf[10] = flag;
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

/// The Unix socket server. Binds once, serves one client at a time (Python
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

    /// Serve one sidecar at a time for the lifetime of the audio engine.
    /// The receiver and wake detector survive reconnects; offline audio is
    /// drained instead of replaying a stale microphone backlog into a new session.
    pub async fn accept_and_run(
        &self,
        engine: Arc<AudioEngine>,
        mic_rx: mpsc::UnboundedReceiver<AudioMsg>,
        wake: Option<(
            super::wake_word::WakeConfig,
            super::wake_word::WakeStateEmitter,
        )>,
        emit: impl Fn(AudioStatus),
    ) -> Result<(), String> {
        let det_tx = wake
            .map(|(cfg, emit)| super::wake_word::spawn_detector(cfg, Arc::clone(&engine), emit));
        self.serve(
            engine.as_ref(),
            mic_rx,
            det_tx,
            emit,
            Duration::from_secs(5),
        )
        .await
    }

    async fn serve(
        &self,
        engine: &impl AudioEndpoint,
        mut mic_rx: mpsc::UnboundedReceiver<AudioMsg>,
        det_tx: Option<std::sync::mpsc::Sender<Vec<i16>>>,
        emit: impl Fn(AudioStatus),
        capture_timeout: Duration,
    ) -> Result<(), String> {
        let mut capture_alive = false;
        let mut ever_connected = false;
        let mut capture_stalled = false;
        let mut last_capture = tokio::time::Instant::now();
        let mut watchdog = tokio::time::interval(capture_timeout / 5);
        loop {
            emit(if capture_stalled {
                AudioStatus::stalled(false)
            } else {
                AudioStatus::new(false, capture_alive, ever_connected)
            });
            // Keep draining while disconnected: neither a dead receiver nor an
            // unbounded backlog may outlive the first sidecar connection.
            let stream = loop {
                tokio::select! {
                    accepted = self.listener.accept() => {
                        break accepted.map_err(|e| format!("accept: {e}"))?.0;
                    }
                    msg = mic_rx.recv() => {
                        if msg.is_none() { return Ok(()); }
                        if !capture_alive {
                            emit(AudioStatus::new(false, true, ever_connected));
                        }
                        capture_alive = true;
                        capture_stalled = false;
                        last_capture = tokio::time::Instant::now();
                    }
                    _ = watchdog.tick() => {
                        if last_capture.elapsed() >= capture_timeout && !capture_stalled {
                            capture_alive = false;
                            capture_stalled = true;
                            engine.stop_audio();
                            emit(AudioStatus::stalled(false));
                        }
                    }
                }
            };
            // Discard frames queued before accept. They belong to the previous
            // connection (or pre-permission startup), never to this session.
            while mic_rx.try_recv().is_ok() {}
            ever_connected = true;
            log::info!("[audio/socket] Python connected");
            emit(if capture_stalled {
                AudioStatus::stalled(true)
            } else {
                AudioStatus::new(true, capture_alive, true)
            });
            let (mut reader, mut writer) = stream.into_split();
            // A persistent read future is essential: cancelling read_exact in
            // select! on every mic tick would lose partial protocol headers.
            let read_loop = read_playback(&mut reader, engine);
            tokio::pin!(read_loop);
            let mut last_aec = None;
            loop {
                tokio::select! {
                    result = &mut read_loop => {
                        log::info!("[audio/socket] connection closed: {result:?}");
                        break;
                    }
                    msg = mic_rx.recv() => {
                        let Some(AudioMsg::MicFrame(samples)) = msg else { return Ok(()); };
                        last_capture = tokio::time::Instant::now();
                        capture_stalled = false;
                        if !capture_alive {
                            capture_alive = true;
                            emit(AudioStatus::new(true, true, true));
                        }
                        let aec = engine.aec_active();
                        if last_aec != Some(aec) {
                            let ctrl = encode_control_flag(CTRL_AUDIO_MODE, if aec { AUDIO_MODE_AEC } else { 0 });
                            if write_frame(&mut writer, &ctrl).await.is_err() { break; }
                            last_aec = Some(aec);
                        }
                        if engine.is_muted() { continue; }
                        if let Some(ref dtx) = det_tx { let _ = dtx.send(samples.clone()); }
                        if !engine.is_listening() || engine.echo_muted() { continue; }
                        let frame = encode_frame(DIR_MIC_TO_PYTHON, MIC_SAMPLE_RATE, &samples);
                        if write_frame(&mut writer, &frame).await.is_err() { break; }
                    }
                    _ = watchdog.tick() => {
                        if last_capture.elapsed() >= capture_timeout {
                            if !capture_stalled {
                                engine.stop_audio();
                                capture_stalled = true;
                                capture_alive = false;
                                log::error!("[audio/socket] microphone frames stopped; relaunch required");
                                emit(AudioStatus::stalled(true));
                            }
                        }
                    }
                }
            }
            engine.stop_audio();
            // Both stream halves drop here before another client is accepted.
        }
    }
}

/// Retained Rust truth, independent of an HTTP/SSE connection or mic gate.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct AudioStatus {
    pub socket_connected: bool,
    pub capture_alive: bool,
    pub detail: String,
    pub relaunch_required: bool,
}

impl Default for AudioStatus {
    fn default() -> Self {
        Self::new(false, false, false)
    }
}
impl AudioStatus {
    fn new(socket_connected: bool, capture_alive: bool, ever_connected: bool) -> Self {
        let detail = if socket_connected && capture_alive {
            ""
        } else if !socket_connected && ever_connected {
            "Voice connection lost; relaunch ORBIS if it does not recover"
        } else {
            "Starting native audio…"
        };
        Self {
            socket_connected,
            capture_alive,
            detail: detail.into(),
            relaunch_required: !socket_connected && ever_connected,
        }
    }
    fn stalled(socket_connected: bool) -> Self {
        Self {
            socket_connected,
            capture_alive: false,
            detail: "Microphone input stopped; relaunch ORBIS".into(),
            relaunch_required: true,
        }
    }
    pub fn ready(&self) -> bool {
        self.socket_connected && self.capture_alive
    }
}

trait AudioEndpoint: Sync {
    fn aec_active(&self) -> bool;
    fn is_muted(&self) -> bool;
    fn is_listening(&self) -> bool;
    fn echo_muted(&self) -> bool;
    fn stop_audio(&self);
    fn stop_listening(&self);
    fn push_playback(&self, samples: &[i16]);
    fn flush_playback(&self);
}
impl AudioEndpoint for AudioEngine {
    fn aec_active(&self) -> bool {
        self.aec_active()
    }
    fn is_muted(&self) -> bool {
        self.is_muted()
    }
    fn is_listening(&self) -> bool {
        self.is_listening()
    }
    fn echo_muted(&self) -> bool {
        self.half_duplex() && self.echo_guard_active(ECHO_GUARD_MS)
    }
    fn stop_audio(&self) {
        self.set_listening(false);
        self.flush_playback();
    }
    fn stop_listening(&self) {
        self.set_listening(false);
    }
    fn push_playback(&self, samples: &[i16]) {
        self.push_playback(samples);
    }
    fn flush_playback(&self) {
        self.flush_playback();
    }
}

async fn write_frame(
    writer: &mut tokio::net::unix::OwnedWriteHalf,
    bytes: &[u8],
) -> Result<(), String> {
    tokio::time::timeout(Duration::from_secs(1), writer.write_all(bytes))
        .await
        .map_err(|_| "audio socket write timed out".to_string())?
        .map_err(|e| format!("audio socket write: {e}"))
}

async fn read_playback(
    reader: &mut tokio::net::unix::OwnedReadHalf,
    engine: &impl AudioEndpoint,
) -> Result<(), String> {
    let mut first_playback = true;
    loop {
        let mut header = [0u8; HEADER_LEN];
        reader
            .read_exact(&mut header)
            .await
            .map_err(|e| e.to_string())?;
        let (direction, _, _, num_samples) = decode_header(&header);
        let mut body = vec![0; num_samples as usize * 2];
        reader
            .read_exact(&mut body)
            .await
            .map_err(|e| e.to_string())?;
        match direction {
            DIR_PYTHON_TO_SPEAKER => {
                let samples: Vec<i16> = body
                    .chunks_exact(2)
                    .map(|b| i16::from_le_bytes([b[0], b[1]]))
                    .collect();
                if first_playback {
                    first_playback = false;
                    log::info!(
                        "[audio/socket] first playback frame received: samples={}",
                        samples.len()
                    );
                }
                engine.push_playback(&samples);
            }
            DIR_CONTROL if body.len() >= 2 => match u16::from_le_bytes([body[0], body[1]]) {
                CTRL_BARGE_IN => {
                    engine.flush_playback();
                }
                CTRL_STOP_LISTENING => engine.stop_listening(),
                CTRL_TTS_END => (),
                code => log::warn!("[audio/socket] unknown control code 0x{code:04x}"),
            },
            _ => log::warn!("[audio/socket] unexpected frame direction 0x{direction:04x}"),
        }
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

    #[derive(Default)]
    struct FakeAudio {
        listening: std::sync::atomic::AtomicBool,
        muted: std::sync::atomic::AtomicBool,
        playback: std::sync::Mutex<Vec<i16>>,
        flushes: std::sync::atomic::AtomicUsize,
    }
    impl AudioEndpoint for FakeAudio {
        fn aec_active(&self) -> bool {
            false
        }
        fn is_muted(&self) -> bool {
            self.muted.load(std::sync::atomic::Ordering::SeqCst)
        }
        fn is_listening(&self) -> bool {
            self.listening.load(std::sync::atomic::Ordering::SeqCst)
        }
        fn echo_muted(&self) -> bool {
            false
        }
        fn stop_listening(&self) {
            self.listening
                .store(false, std::sync::atomic::Ordering::SeqCst);
        }
        fn stop_audio(&self) {
            self.stop_listening();
            self.flush_playback();
        }
        fn push_playback(&self, samples: &[i16]) {
            self.playback.lock().unwrap().extend(samples);
        }
        fn flush_playback(&self) {
            self.playback.lock().unwrap().clear();
            self.flushes
                .fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        }
    }
    fn test_server() -> SocketServer {
        static NEXT: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
        let id = NEXT.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        let path = PathBuf::from(format!(
            "/tmp/orbis-recovery-{}-{id}.sock",
            std::process::id()
        ));
        let listener = UnixListener::bind(&path).unwrap();
        SocketServer { path, listener }
    }
    async fn health_until(
        rx: &mut mpsc::UnboundedReceiver<AudioStatus>,
        predicate: impl Fn(&AudioStatus) -> bool,
    ) -> AudioStatus {
        tokio::time::timeout(Duration::from_secs(2), async {
            loop {
                let status = rx.recv().await.unwrap();
                if predicate(&status) {
                    return status;
                }
            }
        })
        .await
        .unwrap()
    }
    async fn read_wire(peer: &mut tokio::net::UnixStream) -> (u16, Vec<u8>) {
        tokio::time::timeout(Duration::from_secs(2), async {
            let mut header = [0; HEADER_LEN];
            peer.read_exact(&mut header).await.unwrap();
            let (direction, _, _, samples) = decode_header(&header);
            let mut body = vec![0; samples as usize * 2];
            peer.read_exact(&mut body).await.unwrap();
            (direction, body)
        })
        .await
        .unwrap()
    }
    fn runtime() -> tokio::runtime::Runtime {
        tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap()
    }

    #[test]
    fn reconnect_preserves_receiver_drops_offline_audio_and_resends_mode() {
        runtime().block_on(async {
            let server = test_server();
            let path = server.path.clone();
            let audio = Arc::new(FakeAudio::default());
            let eng = audio.clone();
            let (tx, rx) = mpsc::unbounded_channel();
            let (health_tx, mut health_rx) = mpsc::unbounded_channel();
            let task = tokio::spawn(async move {
                server
                    .serve(
                        eng.as_ref(),
                        rx,
                        None,
                        |s| {
                            let _ = health_tx.send(s);
                        },
                        Duration::from_secs(5),
                    )
                    .await
                    .unwrap();
            });
            let mut peer = tokio::net::UnixStream::connect(&path).await.unwrap();
            health_until(&mut health_rx, |s| s.socket_connected).await;
            tx.send(AudioMsg::MicFrame(vec![0; 320])).unwrap();
            health_until(&mut health_rx, AudioStatus::ready).await;
            assert_eq!(read_wire(&mut peer).await.0, DIR_CONTROL);
            audio
                .listening
                .store(true, std::sync::atomic::Ordering::SeqCst);
            tx.send(AudioMsg::MicFrame(vec![1; 320])).unwrap();
            assert_eq!(read_wire(&mut peer).await.0, DIR_MIC_TO_PYTHON);
            drop(peer);
            health_until(&mut health_rx, |s| !s.socket_connected).await;
            assert!(!audio.is_listening());
            assert!(audio.flushes.load(std::sync::atomic::Ordering::SeqCst) > 0);
            tx.send(AudioMsg::MicFrame(vec![99; 320])).unwrap();
            tokio::task::yield_now().await;
            let mut peer = tokio::net::UnixStream::connect(&path).await.unwrap();
            health_until(&mut health_rx, |s| s.socket_connected).await;
            audio
                .listening
                .store(true, std::sync::atomic::Ordering::SeqCst);
            tx.send(AudioMsg::MicFrame(vec![2; 320])).unwrap();
            let (dir, mode) = read_wire(&mut peer).await;
            assert_eq!(dir, DIR_CONTROL);
            assert_eq!(u16::from_le_bytes([mode[0], mode[1]]), CTRL_AUDIO_MODE);
            let (dir, body) = read_wire(&mut peer).await;
            assert_eq!(dir, DIR_MIC_TO_PYTHON);
            assert_eq!(i16::from_le_bytes([body[0], body[1]]), 2);
            task.abort();
            let _ = task.await;
        });
    }

    #[test]
    fn silent_muted_frames_are_live_but_a_stopped_capture_closes_listening() {
        runtime().block_on(async {
            let server = test_server();
            let path = server.path.clone();
            let audio = Arc::new(FakeAudio::default());
            audio.muted.store(true, std::sync::atomic::Ordering::SeqCst);
            let eng = audio.clone();
            let (tx, rx) = mpsc::unbounded_channel();
            let (health_tx, mut health_rx) = mpsc::unbounded_channel();
            let task = tokio::spawn(async move {
                server
                    .serve(
                        eng.as_ref(),
                        rx,
                        None,
                        |s| {
                            let _ = health_tx.send(s);
                        },
                        Duration::from_millis(80),
                    )
                    .await
                    .unwrap();
            });
            let _peer = tokio::net::UnixStream::connect(&path).await.unwrap();
            health_until(&mut health_rx, |s| s.socket_connected).await;
            tx.send(AudioMsg::MicFrame(vec![0; 320])).unwrap();
            health_until(&mut health_rx, AudioStatus::ready).await;
            audio
                .listening
                .store(true, std::sync::atomic::Ordering::SeqCst);
            let failed = health_until(&mut health_rx, |s| {
                s.detail.contains("Microphone input stopped")
            })
            .await;
            assert!(failed.socket_connected);
            assert!(!failed.capture_alive);
            assert!(!audio.is_listening());
            tx.send(AudioMsg::MicFrame(vec![0; 320])).unwrap();
            health_until(&mut health_rx, AudioStatus::ready).await;
            assert!(
                !audio.is_listening(),
                "recovered callbacks must not reopen a turn"
            );
            task.abort();
            let _ = task.await;
        });
    }

    #[test]
    fn fragmented_playback_header_survives_mic_ticks() {
        runtime().block_on(async {
            let server = test_server();
            let path = server.path.clone();
            let audio = Arc::new(FakeAudio::default());
            let eng = audio.clone();
            let (tx, rx) = mpsc::unbounded_channel();
            let (health_tx, mut health_rx) = mpsc::unbounded_channel();
            let task = tokio::spawn(async move {
                server
                    .serve(
                        eng.as_ref(),
                        rx,
                        None,
                        |s| {
                            let _ = health_tx.send(s);
                        },
                        Duration::from_secs(5),
                    )
                    .await
                    .unwrap();
            });
            let mut peer = tokio::net::UnixStream::connect(&path).await.unwrap();
            health_until(&mut health_rx, |s| s.socket_connected).await;
            let playback = encode_frame(DIR_PYTHON_TO_SPEAKER, 24_000, &[10, 20]);
            peer.write_all(&playback[..3]).await.unwrap();
            tx.send(AudioMsg::MicFrame(vec![0; 320])).unwrap();
            health_until(&mut health_rx, AudioStatus::ready).await;
            peer.write_all(&playback[3..]).await.unwrap();
            for _ in 0..20 {
                tokio::task::yield_now().await;
                if !audio.playback.lock().unwrap().is_empty() {
                    break;
                }
            }
            assert_eq!(*audio.playback.lock().unwrap(), vec![10, 20]);
            task.abort();
            let _ = task.await;
        });
    }

    #[test]
    fn unresponsive_peer_has_a_bounded_write_failure() {
        runtime().block_on(async {
            let (socket, _unread_peer) = tokio::net::UnixStream::pair().unwrap();
            let (_reader, mut writer) = socket.into_split();
            let result = write_frame(&mut writer, &vec![0; 2 * 1024 * 1024]).await;
            assert!(result.unwrap_err().contains("timed out"));
        });
    }

    #[test]
    fn no_first_capture_callback_reports_stalled_instead_of_waiting_forever() {
        runtime().block_on(async {
            let server = test_server();
            let path = server.path.clone();
            let audio = Arc::new(FakeAudio::default());
            let (_tx, rx) = mpsc::unbounded_channel();
            let (health_tx, mut health_rx) = mpsc::unbounded_channel();
            let task = tokio::spawn(async move {
                server
                    .serve(
                        audio.as_ref(),
                        rx,
                        None,
                        |s| {
                            let _ = health_tx.send(s);
                        },
                        Duration::from_millis(80),
                    )
                    .await
                    .unwrap();
            });
            let _peer = tokio::net::UnixStream::connect(&path).await.unwrap();
            let failed = health_until(&mut health_rx, |s| s.relaunch_required).await;
            assert!(!failed.capture_alive);
            assert!(failed.detail.contains("Microphone input stopped"));
            task.abort();
            let _ = task.await;
        });
    }

    #[test]
    fn initial_open_mic_survives_offline_drain_until_first_connection() {
        runtime().block_on(async {
            let server = test_server();
            let path = server.path.clone();
            let audio = Arc::new(FakeAudio::default());
            audio
                .listening
                .store(true, std::sync::atomic::Ordering::SeqCst);
            let eng = audio.clone();
            let (tx, rx) = mpsc::unbounded_channel();
            let (health_tx, mut health_rx) = mpsc::unbounded_channel();
            let task = tokio::spawn(async move {
                server
                    .serve(
                        eng.as_ref(),
                        rx,
                        None,
                        |s| {
                            let _ = health_tx.send(s);
                        },
                        Duration::from_secs(5),
                    )
                    .await
                    .unwrap();
            });
            tx.send(AudioMsg::MicFrame(vec![99; 320])).unwrap();
            health_until(&mut health_rx, |s| s.capture_alive && !s.socket_connected).await;
            assert!(
                audio.is_listening(),
                "first boot preserves explicit open-mic activation"
            );
            let mut peer = tokio::net::UnixStream::connect(&path).await.unwrap();
            health_until(&mut health_rx, AudioStatus::ready).await;
            tx.send(AudioMsg::MicFrame(vec![2; 320])).unwrap();
            assert_eq!(read_wire(&mut peer).await.0, DIR_CONTROL);
            let (direction, body) = read_wire(&mut peer).await;
            assert_eq!(direction, DIR_MIC_TO_PYTHON);
            assert_eq!(i16::from_le_bytes([body[0], body[1]]), 2);
            task.abort();
            let _ = task.await;
        });
    }
}
