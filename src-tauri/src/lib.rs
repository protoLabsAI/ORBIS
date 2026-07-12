//! ORBIS Tauri 2 desktop shell.
//!
//! Thin wrapper around the ORBIS Python backend (the "sidecar").
//!
//! ## Security posture
//!
//! The bundled React SPA (`frontendDist = ../web/dist`) loads from
//! `tauri://localhost` and stays there for the whole app lifetime — we
//! do NOT navigate to the sidecar origin (that dropped the WKWebView's
//! keyboard first-responder on macOS, tao#208 / wry#637, and froze the
//! setup wizard's inputs). Instead, once the sidecar prints `ORBIS_READY`
//! we inject its loopback URL into the page (`window.__ORBIS_BACKEND__`)
//! and the frontend resolves `/api/*` fetches + the SSE stream against it
//! (proxied Rust-side via the `api_request` command and `bridge_sse`).
//! Everything ships from fully-local content on this machine.
//! `shell:allow-execute` in the default capability pins the sidecar args
//! so there's no arbitrary-exec surface.
//!
//! One deliberate exception: fleet agents (protoLabsAI/ORBIS#325) drop
//! a manifest in the user-writable
//! `~/Library/Application Support/protoLabs.studio/agents/` dir, and
//! ORBIS launches the `launch.bin` it names. That's an intentional trust
//! boundary — the same user who installs a fleet agent owns that dir —
//! but it means a manifest there can name any executable. ORBIS only
//! spawns one on an explicit tray click, never automatically.
//!
//! ## Runtime flow
//!
//! On boot:
//!
//!   1. Spawn the `orbis` external binary (`binaries/orbis-<target>`
//!      produced by the PyApp workflow at .github/workflows/
//!      desktop-build.yml).
//!   2. Stream its stdout. The Python entry in `app.py:main()` prints
//!      `ORBIS_READY http://127.0.0.1:<port>` once uvicorn is serving,
//!      plus `ORBIS_BOOT {stage,detail}` markers as models load (forwarded
//!      to the UI loading gate as `orbis-boot` events).
//!   3. Inject that URL into the already-loaded bundled SPA so its
//!      `/api/*` fetches + SSE stream resolve against the sidecar — the
//!      webview itself never leaves `tauri://localhost`.
//!
//! Failure modes worth thinking about explicitly:
//!
//! - **Hardware unsupported** — the sidecar's `detect_device()` (in
//!   agent/hardware.py) hard-exits with code 2 when neither CUDA nor
//!   MPS is available. We catch `CommandEvent::Terminated` with that
//!   exit code and surface a native dialog pointing the user at the
//!   Docker self-host path. No silent CPU fallback.
//!
//! - **Sidecar crashes at boot** — any other non-zero exit before the
//!   ready line appears also gets a dialog, with the last ~80 stderr
//!   lines truncated for display.
//!
//! - **App shutdown** — on `RunEvent::ExitRequested` we kill the
//!   sidecar (SIGKILL on Unix, TerminateProcess on Windows) so the
//!   uvicorn + Pipecat + Whisper + Kokoro process tree doesn't
//!   linger. Tauri's shell plugin handles the cleanup if we drop the
//!   CommandChild — we just make sure the guard drops.

use std::collections::HashMap;
use std::collections::VecDeque;
use std::fs::OpenOptions;
use std::io::Write;
#[cfg(target_os = "macos")]
use std::os::raw::c_int;
use std::path::PathBuf;
#[cfg(feature = "native-audio")]
use std::sync::Arc;
use std::sync::Mutex;

#[cfg(feature = "native-audio")]
mod audio;

use serde::Serialize;
use tauri::path::BaseDirectory;
use tauri::{AppHandle, Emitter, Manager, RunEvent};
use tauri_plugin_dialog::{DialogExt, MessageDialogKind};

#[cfg(target_os = "macos")]
extern "C" {
    fn orbis_macos_microphone_authorization_status() -> c_int;
    fn orbis_request_macos_microphone_access_blocking() -> bool;
    fn orbis_open_macos_microphone_settings();
}
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

/// Max stderr lines retained for error-dialog context if the sidecar
/// exits non-zero before the ready line.
const STDERR_RING_CAPACITY: usize = 80;

/// Exit code the Python hardware probe uses. See agent/hardware.py.
const HARDWARE_EXIT_CODE: i32 = 2;

/// Tauri-managed state: the native audio engine, kept alive for
/// the duration of the app. Only present when `native-audio` feature
/// is compiled in.
#[cfg(feature = "native-audio")]
struct AudioEngineState {
    engine: Mutex<Option<Arc<audio::engine::AudioEngine>>>,
}

#[cfg(feature = "native-audio")]
impl AudioEngineState {
    fn new() -> Self {
        Self {
            engine: Mutex::new(None),
        }
    }
    fn store(&self, engine: Arc<audio::engine::AudioEngine>) {
        if let Ok(mut g) = self.engine.lock() {
            *g = Some(engine);
        }
    }
    fn flush_playback(&self) {
        if let Ok(g) = self.engine.lock() {
            if let Some(e) = g.as_ref() {
                e.flush_playback();
            }
        }
    }
}

/// Bundle captured at boot so the native audio engine can be started either
/// immediately (returning user — mic already authorized) or later, when the
/// setup wizard grants microphone permission on first run. Held in
/// [`PendingAudio`] until [`try_start_native_engine`] consumes it. The bound
/// socket stays listening while pending, so the sidecar's `connect()` blocks on
/// the listen backlog until the accept loop comes online after the grant.
#[cfg(feature = "native-audio")]
struct PendingAudioStart {
    sock_server: audio::socket::SocketServer,
    mic_tx: tokio::sync::mpsc::UnboundedSender<audio::engine::AudioMsg>,
    mic_rx: tokio::sync::mpsc::UnboundedReceiver<audio::engine::AudioMsg>,
}

/// Managed state holding the deferred audio-engine start until mic permission
/// exists. `None` once the engine has started (or before boot binds the socket).
#[cfg(feature = "native-audio")]
#[derive(Default)]
struct PendingAudio(Mutex<Option<PendingAudioStart>>);

// ---------------------------------------------------------------------------
// Tauri IPC commands — native audio
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
#[serde(rename_all = "snake_case")]
enum MicrophonePermissionStatus {
    NotDetermined,
    Restricted,
    Denied,
    Authorized,
    Unsupported,
}

impl MicrophonePermissionStatus {
    #[cfg(target_os = "macos")]
    fn from_macos_raw(status: c_int) -> Self {
        match status {
            0 => Self::NotDetermined,
            1 => Self::Restricted,
            2 => Self::Denied,
            3 => Self::Authorized,
            _ => Self::Unsupported,
        }
    }
}

/// Split a `:`-delimited PATH string and append each new, non-empty dir to
/// `entries`, preserving order and skipping duplicates.
#[cfg(target_os = "macos")]
fn dedup_push_path(entries: &mut Vec<String>, raw: &str) {
    for dir in raw.split(':') {
        if !dir.is_empty() && !entries.iter().any(|e| e == dir) {
            entries.push(dir.to_string());
        }
    }
}

/// Ask the user's interactive login shell for its `PATH`
/// (`$SHELL -ilc 'printf %s "$PATH"'`). `None` if `$SHELL` is unknown, the shell
/// errors, or it returns nothing — callers fall back to a fixed prefix.
#[cfg(target_os = "macos")]
fn login_shell_path() -> Option<String> {
    let shell = std::env::var("SHELL").unwrap_or_else(|_| "/bin/zsh".to_string());
    let output = std::process::Command::new(&shell)
        .args(["-ilc", "printf %s \"$PATH\""])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let path = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if path.is_empty() {
        None
    } else {
        Some(path)
    }
}

/// The PATH to hand the bundled sidecar on macOS. A GUI app launched from
/// Finder/Dock/`launchd` only inherits `launchd`'s minimal PATH
/// (`/usr/bin:/bin:/usr/sbin:/sbin`), so Homebrew (`/opt/homebrew/bin`), nvm,
/// Volta, and asdf bin dirs — where `npx`, `node`, and ACP coding-agent adapters
/// live — are invisible to the sidecar, and an `acp` delegate launch command
/// like `npx` or `claude` fails "binary not on PATH". Since the sidecar's ACP
/// client (`acp/client.py`) spawns delegate subprocesses with this inherited
/// PATH, repair it here once and the whole tree benefits. Compose: the
/// login-shell PATH (covers nvm/Volta/asdf), then the common Homebrew/local dirs
/// (belt-and-suspenders if shell resolution failed), then whatever the process
/// already inherited (never drop a dir that already worked).
#[cfg(target_os = "macos")]
fn augmented_sidecar_path() -> String {
    let mut entries: Vec<String> = Vec::new();
    if let Some(shell_path) = login_shell_path() {
        dedup_push_path(&mut entries, &shell_path);
    }
    dedup_push_path(&mut entries, "/opt/homebrew/bin:/usr/local/bin");
    if let Ok(existing) = std::env::var("PATH") {
        dedup_push_path(&mut entries, &existing);
    }
    entries.join(":")
}

fn microphone_permission_status() -> MicrophonePermissionStatus {
    #[cfg(target_os = "macos")]
    unsafe {
        return MicrophonePermissionStatus::from_macos_raw(
            orbis_macos_microphone_authorization_status(),
        );
    }

    #[cfg(not(target_os = "macos"))]
    {
        MicrophonePermissionStatus::Unsupported
    }
}

fn ensure_microphone_permission() -> MicrophonePermissionStatus {
    #[cfg(target_os = "macos")]
    unsafe {
        if microphone_permission_status() == MicrophonePermissionStatus::Authorized {
            return MicrophonePermissionStatus::Authorized;
        }
        let _ = orbis_request_macos_microphone_access_blocking();
        return microphone_permission_status();
    }

    #[cfg(not(target_os = "macos"))]
    {
        MicrophonePermissionStatus::Unsupported
    }
}

#[tauri::command]
fn get_microphone_permission_status() -> MicrophonePermissionStatus {
    microphone_permission_status()
}

#[tauri::command]
fn request_microphone_permission() -> MicrophonePermissionStatus {
    ensure_microphone_permission()
}

#[tauri::command]
fn open_microphone_settings() -> Result<(), String> {
    #[cfg(target_os = "macos")]
    unsafe {
        orbis_open_macos_microphone_settings();
        return Ok(());
    }

    #[cfg(not(target_os = "macos"))]
    {
        Err("microphone settings are only supported by the macOS app".to_string())
    }
}

/// Return the names of all CPAL input devices on the host.
/// Only compiled when the `native-audio` feature is active.
#[cfg(feature = "native-audio")]
#[tauri::command]
fn list_audio_inputs() -> Vec<String> {
    audio::engine::AudioEngine::list_input_devices()
}

/// File where the user's preferred input-device name is persisted, so the audio
/// engine can pick it up at construction (the engine is built at boot, before
/// the frontend loads). orbis-zj5.
#[cfg(feature = "native-audio")]
fn input_device_pref_path(app: &tauri::AppHandle) -> Option<std::path::PathBuf> {
    use tauri::Manager;
    app.path()
        .app_data_dir()
        .ok()
        .map(|d| d.join("input_device"))
}

#[cfg(feature = "native-audio")]
fn persisted_input_device(app: &tauri::AppHandle) -> Option<String> {
    let p = input_device_pref_path(app)?;
    std::fs::read_to_string(p)
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
}

/// Persist the user's chosen input device. Applied when the audio engine is next
/// (re)built — i.e. on the next launch. orbis-zj5.
#[cfg(feature = "native-audio")]
#[tauri::command]
fn set_input_device(app: tauri::AppHandle, name: String) -> Result<(), String> {
    let p = input_device_pref_path(&app).ok_or("no app data dir")?;
    if let Some(parent) = p.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let name = name.trim();
    std::fs::write(&p, name).map_err(|e| format!("persist input device: {e}"))?;
    log::info!("[audio] preferred input device = '{name}' (applies on next launch)");
    Ok(())
}

/// File where the "discoverable on your network" opt-in is persisted ("1"/"0").
/// Read at sidecar spawn: enabled → bind 0.0.0.0 on a fleet-range port (7866,
/// sidecar falls back within 7860–7910) + mDNS advertise fires in the sidecar's
/// lifespan; disabled (default) → loopback + ephemeral port, invisible to the
/// network. Applies on next launch — a bind address can't change live.
fn discoverable_pref_path(app: &tauri::AppHandle) -> Option<std::path::PathBuf> {
    use tauri::Manager;
    app.path()
        .app_data_dir()
        .ok()
        .map(|d| d.join("discoverable"))
}

fn discoverable_enabled(app: &tauri::AppHandle) -> bool {
    discoverable_pref_path(app)
        .and_then(|p| std::fs::read_to_string(p).ok())
        .map(|s| s.trim() == "1")
        .unwrap_or(false)
}

#[tauri::command]
fn get_discoverable(app: tauri::AppHandle) -> bool {
    discoverable_enabled(&app)
}

/// Persist the discoverability opt-in (applies on next launch). Default off:
/// binding 0.0.0.0 exposes the whole HTTP API to the LAN, not just /a2a, so
/// it has to be a deliberate choice.
#[tauri::command]
fn set_discoverable(app: tauri::AppHandle, enabled: bool) -> Result<(), String> {
    let p = discoverable_pref_path(&app).ok_or("no app data dir")?;
    if let Some(parent) = p.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    std::fs::write(&p, if enabled { "1" } else { "0" })
        .map_err(|e| format!("persist discoverable: {e}"))?;
    log::info!("[discovery] discoverable = {enabled} (applies on next launch)");
    Ok(())
}

/// Bring the native audio engine online if (a) mic permission is granted and
/// (b) it isn't already running. Idempotent — safe to call repeatedly, from boot
/// and from the wizard's grant handler.
///
/// Permission is only *checked* here, never requested: on first run the setup
/// wizard owns the TCC prompt, so the macOS dialog appears with context (the Mic
/// step) instead of over the splash. Boot calls this once; if permission isn't
/// there yet the start bundle stays pending and the wizard re-triggers it after
/// the grant via the `start_audio_engine` IPC. Without this, a first-run user
/// who granted mic access *in the wizard* never got an engine — it only ever
/// started in the boot thread, which had already bailed — so the wizard reported
/// "ready" while voice stayed silently dead until an app relaunch.
#[cfg(feature = "native-audio")]
fn try_start_native_engine(app: &AppHandle) {
    // Already running? Nothing to do.
    if app
        .try_state::<AudioEngineState>()
        .and_then(|s| s.engine.lock().ok().map(|g| g.is_some()))
        .unwrap_or(false)
    {
        return;
    }
    // Check (do NOT request) microphone permission. On macOS the engine can't
    // open the input without it; off-macOS the status is Unsupported and we
    // proceed (CPAL takes whatever the host allows).
    #[cfg(target_os = "macos")]
    if microphone_permission_status() != MicrophonePermissionStatus::Authorized {
        log::info!("[audio] mic permission not granted yet — engine start deferred");
        return;
    }
    // Take the start bundle stashed at boot. Absent => already started, or boot
    // hasn't bound the socket yet.
    let Some(PendingAudioStart {
        sock_server,
        mic_tx,
        mic_rx,
    }) = app
        .try_state::<PendingAudio>()
        .and_then(|s| s.0.lock().ok().and_then(|mut g| g.take()))
    else {
        return;
    };

    let app_handle = app.clone();
    std::thread::spawn(move || {
        let preferred = persisted_input_device(&app_handle);
        if let Some(name) = preferred.as_deref() {
            log::info!("[audio] preferred input device from store: '{name}'");
        }
        let preferred_output = persisted_output_device(&app_handle);
        if let Some(name) = preferred_output.as_deref() {
            log::info!("[audio] preferred output device from store: '{name}'");
        }
        match audio::engine::AudioEngine::new(
            preferred.as_deref(),
            preferred_output.as_deref(),
            mic_tx,
        ) {
            Ok(engine) => {
                let engine = Arc::new(engine);
                if let Some(state) = app_handle.try_state::<AudioEngineState>() {
                    state.store(Arc::clone(&engine));
                }
                log::info!("[audio] native engine online");
                // open-mic activation: mic hot from launch, no auto-close.
                let act = read_activation_config(&app_handle);
                if act.style == "open_mic" {
                    engine.set_listening(true);
                    log::info!("[wake] activation=open_mic → mic hot at launch");
                }
                // Restore the persisted full-duplex (barge-in) preference.
                engine.set_full_duplex(act.full_duplex);
                let wake = wake_config(&app_handle);
                // Accept loop runs in a background task.
                tauri::async_runtime::spawn(async move {
                    if let Err(e) = sock_server.accept_and_run(engine, mic_rx, wake).await {
                        log::error!("[audio/socket] accept_and_run failed: {e}");
                    }
                });
            }
            Err(e) => log::error!("[audio] native engine failed to start: {e}"),
        }
    });
}

/// Start the native audio engine now that mic permission may have just been
/// granted. The setup wizard calls this right after the user allows microphone
/// access (or fixes it in System Settings and rechecks). Idempotent + safe when
/// already running. Returns whether the engine reports running *at call time* —
/// the device open finishes on a background thread, so a freshly-triggered start
/// reports false until it lands; the wizard confirms via the live level meter.
#[cfg(feature = "native-audio")]
#[tauri::command]
fn start_audio_engine(app: tauri::AppHandle) -> bool {
    try_start_native_engine(&app);
    app.try_state::<AudioEngineState>()
        .and_then(|s| s.engine.lock().ok().map(|g| g.is_some()))
        .unwrap_or(false)
}

/// Return the names of all output devices on the host (HAL on macOS, CPAL
/// elsewhere). Only compiled when the `native-audio` feature is active.
#[cfg(feature = "native-audio")]
#[tauri::command]
fn list_audio_outputs() -> Vec<String> {
    audio::engine::AudioEngine::list_output_devices()
}

/// File where the user's preferred output-device name is persisted, picked up at
/// engine construction (built at boot, before the frontend loads) — the parallel
/// of `input_device_pref_path`.
#[cfg(feature = "native-audio")]
fn output_device_pref_path(app: &tauri::AppHandle) -> Option<std::path::PathBuf> {
    use tauri::Manager;
    app.path()
        .app_data_dir()
        .ok()
        .map(|d| d.join("output_device"))
}

#[cfg(feature = "native-audio")]
fn persisted_output_device(app: &tauri::AppHandle) -> Option<String> {
    let p = output_device_pref_path(app)?;
    std::fs::read_to_string(p)
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
}

/// Persist the user's chosen output device. Applied when the audio engine is next
/// (re)built — i.e. on the next launch.
#[cfg(feature = "native-audio")]
#[tauri::command]
fn set_output_device(app: tauri::AppHandle, name: String) -> Result<(), String> {
    let p = output_device_pref_path(&app).ok_or("no app data dir")?;
    if let Some(parent) = p.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let name = name.trim();
    std::fs::write(&p, name).map_err(|e| format!("persist output device: {e}"))?;
    log::info!("[audio] preferred output device = '{name}' (applies on next launch)");
    Ok(())
}

/// Return the active native input path so the frontend can avoid
/// presenting controls that do not apply to the production macOS
/// AVAudioEngine voice-processing build.
#[tauri::command]
fn get_audio_input_mode() -> &'static str {
    #[cfg(all(feature = "voice-processing", target_os = "macos"))]
    {
        "voice_processing"
    }

    #[cfg(all(
        feature = "native-audio",
        not(all(feature = "voice-processing", target_os = "macos"))
    ))]
    {
        "cpal"
    }

    #[cfg(not(feature = "native-audio"))]
    {
        "unsupported"
    }
}

/// Return the current microphone RMS level (0.0–1.0) from the running
/// engine, or 0.0 if the engine hasn't started yet.
#[cfg(feature = "native-audio")]
#[tauri::command]
fn get_audio_level(state: tauri::State<AudioEngineState>) -> f32 {
    state
        .engine
        .lock()
        .ok()
        .and_then(|g| g.as_ref().map(|e| e.current_rms()))
        .unwrap_or(0.0)
}

/// Mic + TTS-playback levels (0..1) for the orb's audio-reactivity.
#[cfg(feature = "native-audio")]
#[derive(Serialize, Default)]
struct AudioLevels {
    mic: f32,
    playback: f32,
}

/// The mic level is gated to 0 while muted or during the half-duplex echo
/// window, so her own voice bleeding into the mic doesn't read as the
/// user talking. `playback` is her live TTS level.
#[cfg(feature = "native-audio")]
#[tauri::command]
fn get_audio_levels(state: tauri::State<AudioEngineState>) -> AudioLevels {
    state
        .engine
        .lock()
        .ok()
        .and_then(|g| {
            g.as_ref().map(|e| AudioLevels {
                mic: if e.is_listening() && !e.echo_guard_active(400) {
                    e.current_rms()
                } else {
                    0.0
                },
                playback: e.playback_level(),
            })
        })
        .unwrap_or_default()
}

/// Push-to-talk toggle (double-click the orb). Mic is muted by default
/// so the sidecar gets no audio until the user opts into a conversation.
#[cfg(feature = "native-audio")]
#[tauri::command]
fn set_mic_listening(on: bool, state: tauri::State<AudioEngineState>) {
    if let Ok(g) = state.engine.lock() {
        if let Some(e) = g.as_ref() {
            e.set_listening(on);
        }
    }
}

#[cfg(feature = "native-audio")]
#[tauri::command]
fn mic_listening(state: tauri::State<AudioEngineState>) -> bool {
    state
        .engine
        .lock()
        .ok()
        .and_then(|g| g.as_ref().map(|e| e.is_listening()))
        .unwrap_or(false)
}

/// Hard mute (the mic button) — the top-level override above push-to-talk and
/// the wake word. When muted the engine drops all mic frames (STT *and* the
/// wake detector), so there's no listening, no "Hey Orbis", no barge-in.
#[cfg(feature = "native-audio")]
#[tauri::command]
fn set_mic_muted(muted: bool, state: tauri::State<AudioEngineState>) {
    if let Ok(g) = state.engine.lock() {
        if let Some(e) = g.as_ref() {
            e.set_muted(muted);
        }
    }
}

#[cfg(feature = "native-audio")]
#[tauri::command]
fn mic_muted(state: tauri::State<AudioEngineState>) -> bool {
    state
        .engine
        .lock()
        .ok()
        .and_then(|g| g.as_ref().map(|e| e.is_muted()))
        .unwrap_or(false)
}

/// Clear the WKWebView's storage (cookies, IndexedDB, localStorage,
/// service-worker registrations, fetch cache). Use when stale frontend
/// state is suspected — typically a rebuilt sidecar is being served by
/// a service worker that's caching the old bundle's API responses, or
/// localStorage carries setupComplete from a previous user.
///
/// Replaces the offline `rm -rf ~/Library/WebKit/<bid>/` step in
/// `scripts/nuke-and-rebuild.sh` for the runtime-clear case (when the
/// app is up but acting weird). The script still does the offline wipe
/// for the rebuild path because it has to handle both bundle IDs and
/// the file system can't be mutated through this IPC after the process
/// exits.
#[tauri::command]
async fn clear_browsing_data(webview: tauri::Webview) -> Result<(), String> {
    webview
        .clear_all_browsing_data()
        .map_err(|e| format!("clear_all_browsing_data: {e}"))
}

/// Reveal the log directory in Finder so a user can grab logs for a bug
/// report without hunting for the `~/Library/Logs/...` path. First slice
/// of in-app diagnostics (#488). The dir holds the Python sidecar log
/// (`sidecar.log`) plus the Rust/tauri-plugin-log output. macOS-only
/// `open` (the app is Apple-Silicon-only), so no extra plugin/capability
/// is needed; transcripts are already kept out of these logs.
#[tauri::command]
fn reveal_logs(app: tauri::AppHandle) -> Result<(), String> {
    let dir = app
        .path()
        .app_log_dir()
        .map_err(|e| format!("resolve log dir: {e}"))?;
    let _ = std::fs::create_dir_all(&dir);
    std::process::Command::new("open")
        .arg(&dir)
        .spawn()
        .map_err(|e| format!("open {}: {e}", dir.display()))?;
    Ok(())
}

/// Tauri-managed state: the currently-spawned sidecar child, guarded
/// so the exit handler can kill it from outside the async task.
struct Sidecar {
    child: Mutex<Option<CommandChild>>,
}

impl Sidecar {
    fn new() -> Self {
        Self {
            child: Mutex::new(None),
        }
    }

    fn store(&self, child: CommandChild) {
        if let Ok(mut guard) = self.child.lock() {
            *guard = Some(child);
        }
    }

    fn kill(&self) {
        if let Ok(mut guard) = self.child.lock() {
            if let Some(child) = guard.take() {
                let _ = child.kill();
            }
        }
    }
}

/// Sidecar loopback base URL, populated once the Python backend prints
/// its `ORBIS_READY` line. Exposed to the bundled UI via `backend_url`.
#[derive(Default)]
struct BackendUrl(Mutex<Option<String>>);

/// Returns the sidecar's loopback base URL once ready (else `None`). The
/// bundled UI (loaded from tauri://localhost) calls this to resolve
/// `/api/*` fetch + SSE targets without navigating the webview.
#[tauri::command]
fn backend_url(state: tauri::State<BackendUrl>) -> Option<String> {
    state.0.lock().ok().and_then(|g| g.clone())
}

/// Latest sidecar boot-progress marker (raw JSON: {stage, detail}).
/// Empty until the first `ORBIS_BOOT` line. The UI loading gate queries
/// this on mount (to catch markers emitted before it subscribed) and
/// then listens for `orbis-boot` events.
#[derive(Default)]
struct BootState(Mutex<String>);

#[tauri::command]
fn boot_status(state: tauri::State<BootState>) -> String {
    state.0.lock().map(|g| g.clone()).unwrap_or_default()
}

/// Inject the backend URL into the already-loaded bundled UI and notify
/// it. Replaces the old navigate-to-sidecar flow, which dropped the
/// WKWebView's keyboard first-responder on macOS (tao#208 / wry#637) and
/// froze the setup wizard's inputs.
fn inject_backend_url(app: &AppHandle, url: &str) {
    if let Some(win) = app.get_webview_window("main") {
        let script = format!(
            "window.__ORBIS_BACKEND__ = {url:?}; \
             window.dispatchEvent(new Event('orbis-backend-ready'));"
        );
        if let Err(e) = win.eval(&script) {
            log::warn!("failed to inject backend url: {e}");
        }
    }
    log::info!("injected backend url {url}");
}

/// Bring the main window back to the foreground — used by the tray (click or
/// "Show ORBIS") since in menu-bar-only mode there's no dock icon to click.
/// Also leaves ambient mode (hides the mini-orb, re-docks widgets).
fn show_main_window(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.unminimize();
        let _ = win.show();
        let _ = win.set_focus();
    }
    exit_ambient_mode(app);
}

/// Experimental "surface" features (command bar, widgets, ambient mini-orb) are
/// behind a flag while they bake — off unless `ORBIS_SURFACE` is 1/true/on.
fn is_surface_enabled() -> bool {
    matches!(
        std::env::var("ORBIS_SURFACE").ok().as_deref(),
        Some("1") | Some("true") | Some("on")
    )
}

/// Lets the frontend gate the widget dock / launcher / ambient bridge on the
/// same flag the Rust side uses.
#[tauri::command]
fn surface_enabled() -> bool {
    is_surface_enabled()
}

/// Frontend-triggerable "bring ORBIS back" — invoked by the mini-orb click.
#[tauri::command]
fn show_main(app: tauri::AppHandle) {
    show_main_window(&app);
}

/// Close a popped-out widget window (used to re-dock on un-hide). The window's
/// own `beforeunload` returns the widget to the dock.
#[tauri::command]
fn close_widget_window(app: tauri::AppHandle, id: String) {
    if let Some(win) = app.get_webview_window(&format!("widget-{id}")) {
        let _ = win.close();
    }
}

/// Enter ambient mode — ORBIS's main window is hidden, so float a small orb on
/// the left edge as a glanceable presence + a handle to click it back, and tell
/// the (still-running) main window to pop its docked widgets out onto the
/// desktop. Mirror of `exit_ambient_mode`.
fn enter_ambient_mode(app: &AppHandle) {
    use tauri::Emitter;
    show_mini_orb(app);
    let _ = app.emit("orbis-main-visibility", false);
}

fn exit_ambient_mode(app: &AppHandle) {
    use tauri::Emitter;
    if let Some(win) = app.get_webview_window("mini-orb") {
        let _ = win.hide();
    }
    let _ = app.emit("orbis-main-visibility", true);
}

/// The mini-orb: a small, borderless, transparent, always-on-top window anchored
/// to the left edge. Created on first hide, then shown/hidden thereafter.
fn show_mini_orb(app: &AppHandle) {
    let label = "mini-orb";
    if let Some(win) = app.get_webview_window(label) {
        let _ = win.show();
        return;
    }
    let size = 72.0;
    // Anchor to the left edge, vertically centered on the primary monitor.
    let y = app
        .primary_monitor()
        .ok()
        .flatten()
        .map(|m| (m.size().height as f64 / m.scale_factor() - size) / 2.0)
        .unwrap_or(360.0);
    match tauri::WebviewWindowBuilder::new(app, label, tauri::WebviewUrl::App("index.html".into()))
        .title("ORBIS")
        .inner_size(size, size)
        .position(12.0, y)
        .decorations(false)
        .transparent(true)
        .always_on_top(true)
        .resizable(false)
        .skip_taskbar(true)
        .build()
    {
        Ok(win) => {
            let _ = win.show();
        }
        Err(e) => log::error!("[mini-orb] couldn't open: {e}"),
    }
}

/// A fleet agent registered for the shared menu bar (protoLabsAI/ORBIS#325).
/// protoAgent-based agents drop a manifest in the fleet-shared dir; ORBIS
/// renders them in the tray dropdown. `launch` present = ORBIS spawns + owns
/// the sidecar; absent (with `api_base`) = connect-only (already running).
/// (Launch + embedded console land in the follow-up PRs; PR1 is registry +
/// dropdown.)
#[derive(serde::Deserialize, Clone, Debug)]
#[serde(rename_all = "camelCase")]
#[allow(dead_code)] // `icon` is consumed by the embedded-console PR (ORBIS#325)
struct AgentManifest {
    id: String,
    name: String,
    #[serde(default)]
    icon: Option<String>,
    #[serde(default)]
    launch: Option<AgentLaunch>,
    #[serde(default = "default_health_path")]
    health: String,
    #[serde(default)]
    api_base: Option<String>,
}

#[derive(serde::Deserialize, Clone, Debug)]
struct AgentLaunch {
    bin: String,
    #[serde(default)]
    args: Vec<String>,
}

fn default_health_path() -> String {
    "/healthz".to_string()
}

/// The fleet-shared agents dir:
/// `~/Library/Application Support/protoLabs.studio/agents`.
fn fleet_agents_dir() -> Option<PathBuf> {
    let home = std::env::var_os("HOME")?;
    Some(PathBuf::from(home).join("Library/Application Support/protoLabs.studio/agents"))
}

/// Scan the fleet agents dir for `*.json` manifests, sorted by name.
/// Missing dir / bad files are skipped (warned), never fatal.
fn scan_agent_manifests() -> Vec<AgentManifest> {
    let dir = match fleet_agents_dir() {
        Some(d) => d,
        None => return Vec::new(),
    };
    let entries = match std::fs::read_dir(&dir) {
        Ok(e) => e,
        Err(_) => return Vec::new(),
    };
    let mut out: Vec<AgentManifest> = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        if path.extension().and_then(|e| e.to_str()) != Some("json") {
            continue;
        }
        match std::fs::read_to_string(&path)
            .ok()
            .and_then(|s| serde_json::from_str::<AgentManifest>(&s).ok())
        {
            Some(m) => out.push(m),
            None => log::warn!("[fleet] unparseable agent manifest: {}", path.display()),
        }
    }
    out.sort_by(|a, b| a.name.to_lowercase().cmp(&b.name.to_lowercase()));
    out
}

/// A launched (or connect-only) fleet agent ORBIS is tracking.
struct FleetChild {
    /// `Some` when ORBIS spawned the process (launch manifest) — killed on
    /// quit. `None` for connect-only entries (a fixed `apiBase`), which ORBIS
    /// reaches but doesn't own, so it leaves them running.
    child: Option<std::process::Child>,
    /// Loopback base (`http://127.0.0.1:<port>`) or the manifest's `apiBase`.
    base_url: String,
    /// Display name, used as the console window title on reuse.
    name: String,
}

/// Tauri-managed registry of running fleet agents, keyed by manifest id, so a
/// second click reuses the live process and the exit handler can reap the ones
/// ORBIS launched.
#[derive(Default)]
struct FleetAgents(Mutex<HashMap<String, FleetChild>>);

impl FleetAgents {
    /// Kill every agent ORBIS launched (connect-only entries are left alone).
    fn reap(&self) {
        if let Ok(mut map) = self.0.lock() {
            for (id, agent) in map.iter_mut() {
                if let Some(child) = agent.child.as_mut() {
                    log::info!("[fleet] reaping {id}");
                    let _ = child.kill();
                }
            }
            map.clear();
        }
    }
}

/// Grab a free loopback TCP port by binding `:0` and reading the assignment.
/// The listener is dropped immediately; there's a benign TOCTOU window before
/// the agent binds it, same as ORBIS's own sidecar (which uses `--port 0`).
fn free_port() -> std::io::Result<u16> {
    let listener = std::net::TcpListener::bind("127.0.0.1:0")?;
    Ok(listener.local_addr()?.port())
}

/// Tee a child's stdout/stderr into the unified ORBIS log so a launched
/// agent's output is greppable alongside the sidecar's.
fn spawn_log_reader<R: std::io::Read + Send + 'static>(reader: R, tag: String) {
    std::thread::spawn(move || {
        use std::io::BufRead;
        let buf = std::io::BufReader::new(reader);
        for line in buf.lines().map_while(Result::ok) {
            log::info!("[{tag}] {line}");
        }
    });
}

/// Open (or focus, if it's already up) the embedded console window for an
/// agent: a Tauri webview pointed at the agent's loopback origin, which serves
/// protoAgent's `--ui console`. The console reads its API location from
/// `localStorage['protoagent.apiBase']`, so we seed that to the agent's base
/// URL via an init script that runs before the page loads — one app, one
/// window per agent, switched from the tray.
fn open_or_focus_console(app: &AppHandle, id: &str, name: &str, base_url: &str) {
    let label = format!("fleet-{id}");
    if let Some(win) = app.get_webview_window(&label) {
        let _ = win.unminimize();
        let _ = win.show();
        let _ = win.set_focus();
        return;
    }
    let url = match base_url.parse() {
        Ok(u) => u,
        Err(e) => {
            log::error!("[fleet] {id} has an unparseable base url {base_url}: {e}");
            return;
        }
    };
    // JSON-encode the URL so it can't break out of the string literal.
    let init = format!(
        "try {{ localStorage.setItem('protoagent.apiBase', {}); }} catch (e) {{}}",
        serde_json::to_string(base_url).unwrap_or_else(|_| "''".to_string())
    );
    match tauri::WebviewWindowBuilder::new(app, &label, tauri::WebviewUrl::External(url))
        .title(name)
        .inner_size(1024.0, 720.0)
        .initialization_script(&init)
        .build()
    {
        Ok(_) => log::info!("[fleet] opened console for {id} → {base_url}"),
        Err(e) => log::error!("[fleet] couldn't open console for {id}: {e}"),
    }
}

/// Pop one of ORBIS's own widgets out into a floating native panel. Unlike the
/// fleet console (which loads an external agent UI), this loads ORBIS's OWN
/// bundle under the label `widget-<id>`; the React entry reads its window label
/// and renders just that widget full-bleed (see WidgetWindowRoot). Borderless +
/// transparent + always-on-top for the ambient, voice-first look. Reuses the
/// window if it already exists.
#[tauri::command]
fn open_widget_window(app: tauri::AppHandle, id: String, title: String) {
    let label = format!("widget-{id}");
    if let Some(win) = app.get_webview_window(&label) {
        let _ = win.unminimize();
        let _ = win.show();
        let _ = win.set_focus();
        return;
    }
    match tauri::WebviewWindowBuilder::new(
        &app,
        &label,
        tauri::WebviewUrl::App("index.html".into()),
    )
    .title(&title)
    .inner_size(248.0, 112.0)
    .min_inner_size(160.0, 84.0)
    .decorations(false)
    .transparent(true)
    .always_on_top(true)
    .resizable(true)
    .build()
    {
        Ok(_) => log::info!("[widget] opened window for {id}"),
        Err(e) => log::error!("[widget] couldn't open widget window for {id}: {e}"),
    }
}

/// Show/hide the command bar — a centered, borderless, transparent palette
/// window summoned by the global hotkey (Raycast-style). Created on first use,
/// then toggled, so the hotkey both opens and dismisses it. Always-on-top +
/// skip-taskbar so it floats over everything even when ORBIS is backgrounded.
fn toggle_command_bar(app: &AppHandle) {
    let label = "command-bar";
    if let Some(win) = app.get_webview_window(label) {
        if win.is_visible().unwrap_or(false) {
            let _ = win.hide();
        } else {
            let _ = win.show();
            let _ = win.set_focus();
        }
        return;
    }
    match tauri::WebviewWindowBuilder::new(app, label, tauri::WebviewUrl::App("index.html".into()))
        .title("ORBIS Command")
        .inner_size(600.0, 380.0)
        .decorations(false)
        .transparent(true)
        .always_on_top(true)
        .center()
        .resizable(false)
        .skip_taskbar(true)
        .build()
    {
        Ok(win) => {
            let _ = win.set_focus();
            log::info!("[cmdbar] opened command bar");
        }
        Err(e) => log::error!("[cmdbar] couldn't open command bar: {e}"),
    }
}

/// Frontend-triggerable command-bar toggle (same effect as the global hotkey).
#[tauri::command]
fn open_command_bar(app: tauri::AppHandle) {
    toggle_command_bar(&app);
}

/// Poll an agent's health endpoint until it answers `200`, then open its
/// console window. `503` is treated as "still compiling" (protoAgent returns it
/// until its graph is built), so we keep waiting. Gives up after ~60s.
async fn await_agent_health(
    app: AppHandle,
    id: String,
    name: String,
    base_url: String,
    health: String,
) {
    let url = format!("{base_url}{health}");
    let client = reqwest::Client::new();
    for _ in 0..120 {
        match client.get(&url).send().await {
            Ok(resp) if resp.status().as_u16() == 200 => {
                log::info!("[fleet] {id} ready at {base_url}");
                open_or_focus_console(&app, &id, &name, &base_url);
                return;
            }
            Ok(resp) => log::debug!("[fleet] {id} health {} — waiting", resp.status()),
            Err(_) => {} // not listening yet
        }
        tokio::time::sleep(std::time::Duration::from_millis(500)).await;
    }
    log::warn!("[fleet] {id} never became healthy at {url} (gave up after ~60s)");
}

/// Tray dropdown handler for a selected fleet agent. Re-scans manifests (so a
/// freshly-installed agent is picked up without a relaunch), then either
/// reuses a live process, launches the `launch` binary on a free port, or
/// records a connect-only `apiBase` entry. Spawning the process is synchronous;
/// the health wait runs on the async runtime. The embedded console window is
/// the next PR — here we just bring the agent up and track it.
fn handle_agent_click(app: AppHandle, agent_id: String) {
    let state = match app.try_state::<FleetAgents>() {
        Some(s) => s,
        None => return,
    };

    // Already running? Reuse the live process — surface (or reopen) its
    // console window instead of launching a second copy.
    let existing = state.0.lock().ok().and_then(|map| {
        map.get(&agent_id)
            .map(|a| (a.base_url.clone(), a.name.clone()))
    });
    if let Some((base_url, name)) = existing {
        log::info!("[fleet] {agent_id} already running at {base_url} — focusing console");
        open_or_focus_console(&app, &agent_id, &name, &base_url);
        return;
    }

    let manifest = match scan_agent_manifests()
        .into_iter()
        .find(|m| m.id == agent_id)
    {
        Some(m) => m,
        None => {
            log::warn!("[fleet] {agent_id} clicked but its manifest is gone");
            return;
        }
    };

    match (&manifest.launch, &manifest.api_base) {
        // ORBIS launches and owns the process.
        (Some(launch), _) => {
            let port = match free_port() {
                Ok(p) => p,
                Err(e) => {
                    log::error!("[fleet] {agent_id} couldn't get a free port: {e}");
                    return;
                }
            };
            let base_url = format!("http://127.0.0.1:{port}");
            let mut child = match std::process::Command::new(&launch.bin)
                .args(&launch.args)
                .arg("--port")
                .arg(port.to_string())
                .stdout(std::process::Stdio::piped())
                .stderr(std::process::Stdio::piped())
                .spawn()
            {
                Ok(c) => c,
                Err(e) => {
                    log::error!("[fleet] {agent_id} launch failed ({}): {e}", launch.bin);
                    return;
                }
            };
            log::info!(
                "[fleet] launched {agent_id} ({}) on port {port}",
                launch.bin
            );
            if let Some(out) = child.stdout.take() {
                spawn_log_reader(out, format!("fleet/{agent_id}"));
            }
            if let Some(err) = child.stderr.take() {
                spawn_log_reader(err, format!("fleet/{agent_id}"));
            }
            if let Ok(mut map) = state.0.lock() {
                map.insert(
                    agent_id.clone(),
                    FleetChild {
                        child: Some(child),
                        base_url: base_url.clone(),
                        name: manifest.name.clone(),
                    },
                );
            }
            tauri::async_runtime::spawn(await_agent_health(
                app,
                agent_id,
                manifest.name,
                base_url,
                manifest.health,
            ));
        }
        // Connect-only: an already-running agent at a fixed base URL.
        (None, Some(api_base)) => {
            log::info!("[fleet] {agent_id} connect-only at {api_base}");
            if let Ok(mut map) = state.0.lock() {
                map.insert(
                    agent_id.clone(),
                    FleetChild {
                        child: None,
                        base_url: api_base.clone(),
                        name: manifest.name.clone(),
                    },
                );
            }
            tauri::async_runtime::spawn(await_agent_health(
                app,
                agent_id,
                manifest.name,
                api_base.clone(),
                manifest.health,
            ));
        }
        (None, None) => {
            log::warn!("[fleet] {agent_id} has neither `launch` nor `apiBase` — nothing to do");
        }
    }
}

/// Build the macOS menu-bar (tray) item: the ORBIS orb (full-color, NOT a
/// template — the lavender orb is our identity, so we don't let the system
/// flatten it to monochrome) with a Show / Quit menu. Each protoLabs.studio app
/// owns its own menu-bar presence (ORBIS = the orb; fleet agents = the robot),
/// managed separately. Left-click surfaces the window; the menu's Quit is the
/// real exit path in menu-bar-only mode. Returns Err if the tray can't be
/// created, so the caller can stay in the dock as a fallback rather than leave
/// the app unreachable.
fn setup_tray(app: &tauri::App) -> tauri::Result<()> {
    use tauri::menu::MenuBuilder;
    use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};

    // ORBIS's own menu: Show, then any registered fleet agents it can
    // launch/embed (ORBIS#325), then Quit. The orb is ORBIS's identity here;
    // fleet agents carry their own (the robot) and run their own menu-bar item.
    let agents = scan_agent_manifests();
    log::info!("[fleet] {} agent manifest(s) registered", agents.len());

    let mut mb = MenuBuilder::new(app).text("show", "Show ORBIS");
    if !agents.is_empty() {
        mb = mb.separator();
        for m in &agents {
            mb = mb.text(format!("agent:{}", m.id), &m.name);
        }
    }
    let menu = mb.separator().text("quit", "Quit ORBIS").build()?;

    let icon = tauri::image::Image::from_bytes(include_bytes!("../icons/tray-orb.png"))?;

    TrayIconBuilder::with_id("orbis-tray")
        .icon(icon)
        .icon_as_template(false)
        .tooltip("ORBIS")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| {
            let id = event.id.as_ref();
            match id {
                "show" => show_main_window(app),
                "quit" => app.exit(0),
                _ if id.starts_with("agent:") => {
                    // Launch (free-port + spawn + /healthz wait) on click. The
                    // embedded console window lands in the next PR — see
                    // ORBIS#325 — so for now this brings the agent up + tracks
                    // it (greppable in the unified log).
                    handle_agent_click(app.clone(), id["agent:".len()..].to_string());
                }
                _ => {}
            }
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                show_main_window(tray.app_handle());
            }
        })
        .build(app)?;

    Ok(())
}

/// Response returned by `api_request` to the bundled UI.
#[derive(Serialize)]
struct ApiResponse {
    status: u16,
    body: String,
}

/// Proxy a frontend API call to the sidecar over reqwest (Rust) instead
/// of WKWebView's fetch. Tahoe's WKWebView drops/hangs HTTP request and
/// response bodies, so the bundled UI routes every /api/* request through
/// this command (tauri-apps/tauri#11854/#13166/#13878 and friends).
#[tauri::command]
async fn api_request(
    method: String,
    path: String,
    body: Option<String>,
    headers: Option<HashMap<String, String>>,
    state: tauri::State<'_, BackendUrl>,
) -> Result<ApiResponse, String> {
    let base = state
        .0
        .lock()
        .ok()
        .and_then(|g| g.clone())
        .ok_or_else(|| "backend not ready".to_string())?;
    let url = format!("{}{}", base.trim_end_matches('/'), path);
    let m = reqwest::Method::from_bytes(method.as_bytes())
        .map_err(|e| format!("bad method {method}: {e}"))?;
    let mut req = reqwest::Client::new().request(m, &url);
    if let Some(h) = headers {
        for (k, v) in h {
            req = req.header(k, v);
        }
    }
    if let Some(b) = body {
        req = req
            .header(reqwest::header::CONTENT_TYPE, "application/json")
            .body(b);
    }
    let resp = req
        .send()
        .await
        .map_err(|e| format!("request {method} {path} failed: {e}"))?;
    let status = resp.status().as_u16();
    let body = resp
        .text()
        .await
        .map_err(|e| format!("read body {path} failed: {e}"))?;
    Ok(ApiResponse { status, body })
}

/// Payload for the `orbis-sse` Tauri event mirroring one SSE message.
#[derive(Clone, Serialize)]
struct SsePayload {
    event: String,
    data: String,
}

/// Parse an SSE block ("event: X\ndata: Y") → (event, data). Returns
/// None for comment/heartbeat-only blocks.
fn parse_sse_block(raw: &str) -> Option<(String, String)> {
    let mut event: Option<String> = None;
    let mut data: Vec<String> = Vec::new();
    for line in raw.lines() {
        if let Some(v) = line.strip_prefix("event:") {
            event = Some(v.trim().to_string());
        } else if let Some(v) = line.strip_prefix("data:") {
            data.push(v.trim().to_string());
        }
    }
    event.map(|e| (e, data.join("\n")))
}

/// Map the SSE events onto the wake detector's conversation-busy signal
/// (audio::wake_word::CONVERSATION_BUSY): bot-state thinking/speaking and
/// in-flight tool calls hold the listening window's auto-close timer, so
/// "ask → it delegates → silence while it works" doesn't end with the
/// window closed and the answer arriving to an armed mic.
#[cfg(feature = "native-audio")]
fn update_conversation_busy(event: &str, data: &str) {
    use audio::wake_word::CONVERSATION_BUSY;
    let parsed: Option<serde_json::Value> = serde_json::from_str(data).ok();
    let field = |key: &str| -> Option<String> {
        parsed
            .as_ref()
            .and_then(|v| v.get(key))
            .and_then(|s| s.as_str())
            .map(str::to_owned)
    };
    match event {
        "bot-state" => match field("state").as_deref() {
            Some("thinking") | Some("speaking") => CONVERSATION_BUSY.set_state_busy(true),
            Some("idle") | Some("listening") => CONVERSATION_BUSY.set_state_busy(false),
            _ => {}
        },
        "tool-call" => match field("event").as_deref() {
            Some("start") => CONVERSATION_BUSY.tool_started(),
            Some("end") => CONVERSATION_BUSY.tool_ended(),
            _ => {}
        },
        _ => {}
    }
}

/// Bridge the sidecar's /api/events SSE stream to the frontend as Tauri
/// `orbis-sse` events. WKWebView won't stream a cross-origin EventSource
/// on Tahoe, so Rust (reqwest) consumes the stream and re-emits each
/// event; the frontend listens instead of opening its own EventSource.
async fn bridge_sse(app: AppHandle, base: String) {
    let url = format!("{}/api/events", base.trim_end_matches('/'));
    let client = reqwest::Client::new();
    loop {
        match client.get(&url).send().await {
            Ok(mut resp) => {
                let _ = app.emit(
                    "orbis-sse",
                    SsePayload {
                        event: "__connected".into(),
                        data: "{}".into(),
                    },
                );
                let mut buf = String::new();
                loop {
                    match resp.chunk().await {
                        Ok(Some(bytes)) => {
                            buf.push_str(&String::from_utf8_lossy(&bytes));
                            while let Some(idx) = buf.find("\n\n") {
                                let block: String = buf.drain(..idx + 2).collect();
                                if let Some((event, data)) = parse_sse_block(&block) {
                                    // Feed the wake auto-close timer's busy signal
                                    // from the stream we already proxy — a long
                                    // delegation or answer must not close the
                                    // listening window mid-exchange.
                                    #[cfg(feature = "native-audio")]
                                    update_conversation_busy(&event, &data);
                                    let _ = app.emit("orbis-sse", SsePayload { event, data });
                                }
                            }
                        }
                        Ok(None) => break,
                        Err(e) => {
                            log::warn!("[sse-bridge] stream error: {e}");
                            break;
                        }
                    }
                }
            }
            Err(e) => log::warn!("[sse-bridge] connect failed: {e}"),
        }
        tokio::time::sleep(std::time::Duration::from_millis(1500)).await;
    }
}

pub fn run() {
    tauri::Builder::default()
        // tauri-plugin-log: unifies Rust + sidecar stdio into a
        // rotating file at the OS log dir. The sidecar log stream
        // tee'd in via log::info!(target:"sidecar", ...) (see
        // supervise_sidecar) lands in the same file as our own
        // log::info! call sites. Replaces env_logger.
        //
        // Webview target deliberately omitted: the plugin's frontend
        // capture injects JS into the WKWebView via an early-init
        // script, which hangs fetch() calls in production builds the
        // *first* time the wizard tries to POST /api/config (the
        // "Saving…" stuck-forever bug). Frontend logs go to the
        // Safari Web Inspector when devtools are enabled; not into
        // orbis.log.
        .plugin(
            tauri_plugin_log::Builder::new()
                .level(log::LevelFilter::Info)
                .targets([
                    tauri_plugin_log::Target::new(tauri_plugin_log::TargetKind::Stdout),
                    tauri_plugin_log::Target::new(tauri_plugin_log::TargetKind::LogDir {
                        file_name: Some("orbis".into()),
                    }),
                ])
                .max_file_size(50_000_000) // 50 MB
                .rotation_strategy(tauri_plugin_log::RotationStrategy::KeepAll)
                .build(),
        )
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        // In-app auto-update (check latest.json → download + install the signed
        // updater artifact) + process (relaunch after applying). Desktop-only.
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        // tauri-plugin-http: routes JS fetch() through Rust + reqwest,
        // bypassing WKWebView's silent POST drop on macOS arm64
        // (tauri-apps/tauri#11854, #13166, #13878). Frontend imports
        // `fetch` from `@tauri-apps/plugin-http`. Origin allow-list
        // is in capabilities/default.json.
        .plugin(tauri_plugin_http::init())
        // Global hotkey for the command bar — registered + handled in setup().
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        // macOS WKWebView drops keyboard first-responder when the
        // webview navigates to a new origin (startup splash -> the
        // sidecar backend URL). Until focus is restored the setup
        // wizard's inputs and buttons receive no clicks or keystrokes.
        // Re-focus once each navigation has *finished* loading — an
        // immediate set_focus right after navigate() races the async
        // load and gets reset to nil when the new document commits.
        .on_page_load(|webview, payload| {
            if payload.event() == tauri::webview::PageLoadEvent::Finished {
                // Focus the webview itself (not just the window): on macOS
                // this makes the WKWebView the first responder, which a
                // window-level set_focus does not reliably do.
                if let Err(e) = webview.set_focus() {
                    log::warn!("webview set_focus on page-load finished failed: {e}");
                }
            }
        })
        .invoke_handler({
            #[cfg(feature = "native-audio")]
            {
                tauri::generate_handler![
                    list_audio_inputs,
                    set_input_device,
                    start_audio_engine,
                    list_audio_outputs,
                    set_output_device,
                    get_audio_level,
                    get_audio_levels,
                    get_audio_input_mode,
                    get_microphone_permission_status,
                    request_microphone_permission,
                    open_microphone_settings,
                    clear_browsing_data,
                    reveal_logs,
                    backend_url,
                    set_mic_listening,
                    mic_listening,
                    set_mic_muted,
                    mic_muted,
                    get_activation_config,
                    set_activation_config,
                    set_full_duplex,
                    get_discoverable,
                    set_discoverable,
                    open_url,
                    api_request,
                    boot_status,
                    open_widget_window,
                    open_command_bar,
                    show_main,
                    close_widget_window,
                    surface_enabled
                ]
            }
            #[cfg(not(feature = "native-audio"))]
            {
                tauri::generate_handler![
                    get_microphone_permission_status,
                    request_microphone_permission,
                    open_microphone_settings,
                    get_audio_input_mode,
                    clear_browsing_data,
                    reveal_logs,
                    backend_url,
                    get_discoverable,
                    set_discoverable,
                    open_url,
                    api_request,
                    boot_status,
                    open_widget_window,
                    open_command_bar,
                    show_main,
                    close_widget_window,
                    surface_enabled
                ]
            }
        })
        .manage(Sidecar::new())
        .manage(BackendUrl::default())
        .manage(BootState::default())
        .manage(FleetAgents::default())
        .on_window_event(|window, event| {
            // Menu-bar agent: closing the MAIN window (red traffic light) hides
            // it instead of quitting — ORBIS keeps listening in the background.
            // Real exit is the tray's "Quit" (RunEvent::ExitRequested). Fleet
            // console windows (`fleet-*`) close normally; their agent process
            // keeps running and the window reopens on the next tray click.
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == "main" {
                    api.prevent_close();
                    let app = window.app_handle().clone();
                    let _ = window.hide();
                    // Ambient presence (mini-orb + pop-out widgets) is gated.
                    if is_surface_enabled() {
                        enter_ambient_mode(&app);
                    }
                }
            }
        })
        .setup(|app| {
            // Register native audio engine state. Must be done in setup()
            // so it's available before supervise_sidecar runs.
            #[cfg(feature = "native-audio")]
            {
                app.manage(AudioEngineState::new());
                // Holds the deferred engine start until mic permission exists
                // (first run grants it via the wizard). Bound by supervise_sidecar.
                app.manage(PendingAudio::default());
            }

            // Menu-bar-only agent mode: build the tray first, and only drop
            // the dock icon (Accessory) if it succeeds — so a tray failure
            // can't leave the app with no way to surface itself.
            match setup_tray(app) {
                Ok(()) => {
                    #[cfg(target_os = "macos")]
                    {
                        let _ = app.set_activation_policy(tauri::ActivationPolicy::Accessory);
                    }
                }
                Err(e) => log::error!("tray setup failed; staying in the dock: {e}"),
            }

            // Global hotkey to summon the command bar (Raycast-style), even when
            // ORBIS is backgrounded. Cmd+Shift+O — distinctive enough to avoid
            // the usual macOS clashes. Registered + handled Rust-side. Gated:
            // the command bar is an experimental surface feature.
            #[cfg(desktop)]
            if is_surface_enabled() {
                use tauri_plugin_global_shortcut::{
                    Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState,
                };
                let cmd_bar = Shortcut::new(Some(Modifiers::SUPER | Modifiers::SHIFT), Code::KeyO);
                let gs_handle = app.handle().clone();
                if let Err(e) =
                    app.global_shortcut()
                        .on_shortcut(cmd_bar, move |_app, _sc, event| {
                            if event.state == ShortcutState::Pressed {
                                toggle_command_bar(&gs_handle);
                            }
                        })
                {
                    log::error!("[cmdbar] failed to register global shortcut: {e}");
                }
            }

            let handle = app.handle().clone();
            // Spawn the sidecar on an async task so setup() returns
            // immediately + the splash window renders while the
            // Python process is booting.
            tauri::async_runtime::spawn(async move {
                if let Err(e) = supervise_sidecar(handle.clone()).await {
                    log::error!("sidecar supervisor exited: {e}");
                    fatal_dialog(
                        &handle,
                        "ORBIS couldn't start",
                        &format!(
                            "The ORBIS backend failed to launch.\n\n{e}\n\n\
                             If this keeps happening, try the Docker self-host path \
                             — see https://github.com/protoLabsAI/ORBIS."
                        ),
                    );
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            if let RunEvent::ExitRequested { .. } = event {
                if let Some(state) = app_handle.try_state::<Sidecar>() {
                    log::info!("app exit requested — killing sidecar");
                    state.kill();
                }
                // Reap any fleet agents ORBIS launched (connect-only stay up).
                if let Some(state) = app_handle.try_state::<FleetAgents>() {
                    state.reap();
                }
                // Flush CPAL playback ring so audio doesn't click on exit.
                #[cfg(feature = "native-audio")]
                if let Some(state) = app_handle.try_state::<AudioEngineState>() {
                    state.flush_playback();
                }
            }
        });
}

/// Spawn the Python sidecar, watch its stdout/stderr, and either
/// navigate the main webview on `ORBIS_READY` or surface an error
/// dialog on a bad exit. Single-shot: one sidecar per app run.
/// The activation layer's persisted settings (engagement-modes Axis 1): how the
/// mic gets hot. Stored at `<app_data>/activation.json`, read by the audio
/// supervisor at launch. Applies on next launch (like the input-device pref) —
/// the detector spawns once in `accept_and_run`.
#[cfg(feature = "native-audio")]
#[derive(serde::Serialize, serde::Deserialize, Clone)]
#[serde(default)]
struct ActivationConfig {
    /// "push_to_talk" (default) | "wake_word" | "open_mic"
    style: String,
    /// wake model id — the picker's `<id>.onnx` (e.g. "hey_orbis")
    model: String,
    /// fire threshold 0..1
    threshold: f32,
    /// auto-close the listening window after this many seconds of silence
    listen_window_s: f32,
    /// keep the mic open during playback so the user can barge in (full-duplex).
    /// default false (half-duplex). safe with headphones or hardware AEC.
    full_duplex: bool,
}

#[cfg(feature = "native-audio")]
impl Default for ActivationConfig {
    fn default() -> Self {
        Self {
            style: "push_to_talk".into(),
            model: "hey_orbis".into(),
            threshold: 0.5,
            listen_window_s: 12.0,
            full_duplex: false,
        }
    }
}

#[cfg(feature = "native-audio")]
fn activation_config_path(app: &AppHandle) -> Option<std::path::PathBuf> {
    use tauri::Manager;
    app.path()
        .app_data_dir()
        .ok()
        .map(|d| d.join("activation.json"))
}

#[cfg(feature = "native-audio")]
fn read_activation_config(app: &AppHandle) -> ActivationConfig {
    activation_config_path(app)
        .and_then(|p| std::fs::read_to_string(p).ok())
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

/// Read the activation config for the Settings UI.
#[cfg(feature = "native-audio")]
#[tauri::command]
fn get_activation_config(app: AppHandle) -> serde_json::Value {
    serde_json::to_value(read_activation_config(&app)).unwrap_or(serde_json::Value::Null)
}

/// Persist the activation config (applies on next launch).
#[cfg(feature = "native-audio")]
#[tauri::command]
fn set_activation_config(
    app: AppHandle,
    style: String,
    model: String,
    threshold: f32,
    listen_window_s: f32,
) -> Result<(), String> {
    // Preserve full_duplex (owned by the separate set_full_duplex toggle).
    let cfg = ActivationConfig {
        style,
        model,
        threshold,
        listen_window_s,
        ..read_activation_config(&app)
    };
    let p = activation_config_path(&app).ok_or("no app data dir")?;
    if let Some(parent) = p.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let json = serde_json::to_string_pretty(&cfg).map_err(|e| e.to_string())?;
    std::fs::write(&p, json).map_err(|e| format!("persist activation config: {e}"))?;
    log::info!(
        "[wake] activation set: style={} model={} threshold={:.2} window={:.0}s (applies next launch)",
        cfg.style,
        cfg.model,
        cfg.threshold,
        cfg.listen_window_s
    );
    Ok(())
}

/// Toggle full-duplex (keep the mic open during playback so the user can barge
/// in). Applies LIVE to the running engine and persists for next launch.
#[cfg(feature = "native-audio")]
#[tauri::command]
fn set_full_duplex(
    on: bool,
    app: AppHandle,
    state: tauri::State<AudioEngineState>,
) -> Result<(), String> {
    // Live — the socket writer reads half_duplex() per frame.
    if let Ok(g) = state.engine.lock() {
        if let Some(e) = g.as_ref() {
            e.set_full_duplex(on);
        }
    }
    // Persist into activation.json, preserving the wake-word fields.
    let cfg = ActivationConfig {
        full_duplex: on,
        ..read_activation_config(&app)
    };
    let p = activation_config_path(&app).ok_or("no app data dir")?;
    if let Some(parent) = p.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let json = serde_json::to_string_pretty(&cfg).map_err(|e| e.to_string())?;
    std::fs::write(&p, json).map_err(|e| format!("persist full_duplex: {e}"))?;
    log::info!("[audio] full_duplex set to {on} (live + persisted)");
    Ok(())
}

/// Open a URL in the user's default browser (docs / help links from the UI).
#[tauri::command]
fn open_url(app: AppHandle, url: String) -> Result<(), String> {
    app.shell().open(url, None).map_err(|e| e.to_string())
}

/// Display phrase for a wake model id — `"hey_orbis"` → `"Hey Orbis"`.
#[cfg(feature = "native-audio")]
fn wake_phrase(model: &str) -> String {
    model
        .split('_')
        .filter(|w| !w.is_empty())
        .map(|w| {
            let mut c = w.chars();
            match c.next() {
                Some(f) => f.to_uppercase().collect::<String>() + c.as_str(),
                None => String::new(),
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
}

/// Build the wake-word detector config + its state-event emitter, or `None` to
/// leave the detector off (push-to-talk / open-mic styles). The emitter is a
/// boxed closure that fans ARMED/LISTENING transitions to the `wake-state`
/// Tauri event (keeping the audio modules Tauri-free). `ORBIS_WAKE_ENABLED=1`
/// forces it on for dev regardless of the persisted style.
#[cfg(feature = "native-audio")]
fn wake_config(
    app: &AppHandle,
) -> Option<(
    audio::wake_word::WakeConfig,
    audio::wake_word::WakeStateEmitter,
)> {
    use tauri::{Emitter, Manager};
    let cfg = read_activation_config(app);
    let env_on = std::env::var("ORBIS_WAKE_ENABLED").ok().as_deref() == Some("1");
    if cfg.style != "wake_word" && !env_on {
        return None;
    }
    let models_dir = app
        .path()
        .app_data_dir()
        .ok()?
        .join("models")
        .join("wakeword");
    let ActivationConfig {
        model,
        threshold,
        listen_window_s,
        ..
    } = cfg;
    let model = std::env::var("ORBIS_WAKE_MODEL").unwrap_or(model);
    let threshold = std::env::var("ORBIS_WAKE_THRESHOLD")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(threshold);
    let listen_window_s = listen_window_s.max(1.0);
    log::info!(
        "[wake] enabled: model={model} threshold={threshold:.2} window={listen_window_s:.0}s"
    );
    // The display phrase travels with the state so the pill can show the chosen
    // wake word (e.g. "Hey Orbis") rather than a generic label.
    let phrase = wake_phrase(&model);
    let app2 = app.clone();
    let emit: audio::wake_word::WakeStateEmitter = Box::new(move |s: &str| {
        if s == "listening" {
            // "Hey Orbis" summons the window — show + focus it even when hidden
            // or in ambient mode. Runs on the detector thread, so hop to the
            // main thread for the UI ops.
            let app3 = app2.clone();
            let _ = app2.run_on_main_thread(move || show_main_window(&app3));
        }
        let _ = app2.emit(
            "wake-state",
            serde_json::json!({ "state": s, "phrase": phrase }),
        );
    });
    Some((
        audio::wake_word::WakeConfig {
            models_dir,
            model,
            threshold,
            listen_window_s,
        },
        emit,
    ))
}

async fn supervise_sidecar(app: AppHandle) -> Result<(), String> {
    let shell = app.shell();

    // Resolve the env vars the sidecar needs at boot:
    //
    // * `ORBIS_CONFIG` — agent/config_store.py defaults to the relative
    //   path `config/orbis.yaml`. The bundled app spawns the sidecar
    //   with cwd = `/`, so that relative path resolves to `/config/...`
    //   which doesn't exist (and isn't writable). Point it at a stable,
    //   writable location under the platform's app-data dir so reads
    //   and the wizard's writes both target the same file across runs.
    //
    // * `START_VLLM` — `voice/lifecycle.py` defaults to spawning a
    //   local vLLM child during FastAPI startup, blocking up to 120s
    //   waiting for it to come up. The bundled python doesn't ship
    //   vLLM (no CUDA on macOS, no NVIDIA drivers assumed), so the
    //   spawn always fails and the app sits on the splash forever.
    //   Disable by default; users running vLLM separately can override
    //   in their shell.
    //
    // Pre-existing values in the parent env win — handy for `cargo
    // tauri dev` where the developer may want to point at the repo's
    // checked-in config or run a real vLLM.
    let config_path = resolve_config_path(&app)?;
    if let Some(parent) = config_path.parent() {
        // Fail fast if we can't create the config dir — without it the
        // sidecar will boot to a broken state (no config, no place for
        // the wizard to write). Propagating the error routes us to the
        // fatal-dialog path with a clear "ORBIS couldn't start" message.
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("couldn't create config dir {}: {e}", parent.display()))?;
    }
    seed_default_config(&app, &config_path);
    let start_vllm = std::env::var("START_VLLM").unwrap_or_else(|_| "0".to_string());
    let starter_orbs_path = resolve_starter_orbs_path(&app);
    let delegates_path = resolve_delegates_path(&app);
    log::info!(
        "sidecar env: ORBIS_CONFIG={} START_VLLM={start_vllm}",
        config_path.display()
    );
    if let Some(p) = starter_orbs_path.as_ref() {
        log::info!("sidecar env: ORBIS_STARTER_ORBS={}", p.display());
    }
    if let Some(p) = delegates_path.as_ref() {
        log::info!("sidecar env: DELEGATES_YAML={}", p.display());
    }

    // --- Native audio engine ---
    // Start the native mic/speaker engine and Unix socket server. The
    // socket path is passed to the sidecar as ORBIS_AUDIO_SOCK so Python
    // can connect to it. Production macOS builds use AVAudioEngine
    // voice-processing input plus CPAL output.
    //
    // Bind the mic/speaker socket synchronously (no permission needed) so the
    // sidecar can be spawned and connect right away, then DEFER the engine
    // start. On a returning user (mic already authorized) it starts now; on
    // first run it stays pending until the wizard grants permission and calls
    // the `start_audio_engine` IPC. We deliberately do NOT request permission
    // here: requesting off the wizard pops the macOS TCC dialog over the splash
    // before any context, and an in-setup-hook request couldn't present at all
    // (no run loop yet). The mic is push-to-talk-muted by default, so nothing
    // needs it until the user opts into a conversation; the socket's listen
    // backlog holds the sidecar's connection until the accept loop comes online.
    #[cfg(feature = "native-audio")]
    let native_audio_sock: Option<std::path::PathBuf> = {
        let (mic_tx, mic_rx) = tokio::sync::mpsc::unbounded_channel::<audio::engine::AudioMsg>();
        let sock_server = match audio::socket::SocketServer::bind() {
            Ok(s) => s,
            Err(e) => return Err(format!("native audio socket bind failed: {e}")),
        };
        let sock_path = sock_server.path().clone();
        log::info!("orbis_audio_sock={}", sock_path.display());
        match app.try_state::<PendingAudio>() {
            Some(state) => {
                if let Ok(mut g) = state.0.lock() {
                    *g = Some(PendingAudioStart {
                        sock_server,
                        mic_tx,
                        mic_rx,
                    });
                }
            }
            // PendingAudio is managed in setup() before this runs; if it's
            // somehow absent the socket would close on drop and the sidecar
            // could never connect — fail loud rather than boot mute.
            None => return Err("PendingAudio state not registered".to_string()),
        }
        // Start now if already authorized (returning user); otherwise the wizard
        // re-triggers via `start_audio_engine` after the first-run grant.
        try_start_native_engine(&app);
        Some(sock_path)
    };

    // `sidecar("orbis")` resolves to `binaries/orbis-<target>` on the
    // bundle or `./binaries/orbis-<target>` during `tauri dev`.
    // Target-suffix resolution is Tauri's job — we just give the base
    // name that matches the externalBin entry in tauri.conf.json.
    // Default: loopback + ephemeral port — invisible to the network. With the
    // "discoverable" opt-in: all interfaces + a fleet-range port so protoAgent
    // discovery (mDNS + port-scan over 7860–7910) finds this ORBIS; the
    // sidecar walks the range itself if 7866 is taken.
    let (bind_host, bind_port) = if discoverable_enabled(&app) {
        log::info!("[discovery] discoverable mode — binding 0.0.0.0:7866 (fleet range)");
        ("0.0.0.0", "7866")
    } else {
        ("127.0.0.1", "0")
    };
    let mut command = shell
        .sidecar("orbis")
        .map_err(|e| format!("couldn't find sidecar binary: {e}"))?
        .args(["--host", bind_host, "--port", bind_port])
        .env("ORBIS_CONFIG", &config_path)
        // Default STT to Parakeet (MLX) on the native build — far fewer
        // silence-hallucinations than Whisper. Only the default: a
        // `stt.backend` in config (Settings → STT) overrides it.
        .env("STT_BACKEND", "parakeet")
        .env("START_VLLM", &start_vllm);
    // A Finder/Dock/launchd launch strips PATH down to launchd's minimal set,
    // hiding Homebrew/nvm/Volta/asdf — so `acp` delegate launch commands (`npx`,
    // `claude`, ACP adapters) that the sidecar's ACP client spawns fail with
    // "binary not on PATH". Hand the sidecar the user's real login PATH so the
    // whole subprocess tree can resolve those binaries.
    #[cfg(target_os = "macos")]
    {
        command = command.env("PATH", augmented_sidecar_path());
    }
    // Point the sidecar at the bundled starter-orbs YAML. Without this,
    // agent/starter_orbs.py falls back to a cwd-relative
    // "config/starter_orbs.yaml" path that doesn't exist in the
    // installed app, and the wizard's "Pick your orb" step renders
    // "No starters configured on the server."
    if let Some(p) = starter_orbs_path.as_ref() {
        command = command.env("ORBIS_STARTER_ORBS", p);
    }
    // Point the sidecar at the bundled delegates YAML. Without this, the
    // DelegateRegistry + /api/delegates fall back to a cwd-relative
    // "config/delegates.yaml" that doesn't exist in the installed app, so
    // `delegate_to` is never registered and there's nothing to delegate to.
    if let Some(p) = delegates_path.as_ref() {
        command = command.env("DELEGATES_YAML", p);
    }
    // Wake-word models: the picker downloads into <app_data>/models/wakeword/;
    // the Rust detector reads the same dir. Tell the sidecar where that is so
    // both sides agree (without it the picker falls back to a cwd-relative
    // "models/" the installed app can't find).
    if let Ok(models) = app.path().app_data_dir() {
        command = command.env("ORBIS_MODELS_DIR", models.join("models"));
    }
    // User-imported `.orbis` orb definitions live in app-data so they
    // survive app updates (same posture as delegates.yaml). Without this
    // the sidecar falls back to a cwd-relative "config/orbs" the
    // installed app can't find. See agent/orb_definitions.py.
    if let Ok(data) = app.path().app_data_dir() {
        let orbs = data.join("orbs");
        if let Err(e) = std::fs::create_dir_all(&orbs) {
            log::warn!("orbs dir create failed: {e}");
        }
        log::info!("sidecar env: ORBIS_ORBS_DIR={}", orbs.display());
        command = command.env("ORBIS_ORBS_DIR", orbs);
    }
    // Personas (epic #611): user-authored persona files live in app-data
    // (writable, survive updates); bundled starters ship as a read-only
    // Resource dir. Without these envs the sidecar falls back to a
    // cwd-relative "config/personas" the installed app can't find.
    // See agent/personas.py.
    if let Ok(data) = app.path().app_data_dir() {
        let personas = data.join("personas");
        if let Err(e) = std::fs::create_dir_all(&personas) {
            log::warn!("personas dir create failed: {e}");
        }
        log::info!("sidecar env: ORBIS_PERSONAS_DIR={}", personas.display());
        command = command.env("ORBIS_PERSONAS_DIR", personas);
    }
    if let Some(p) = resolve_bundled_personas_dir(&app) {
        log::info!("sidecar env: ORBIS_BUNDLED_PERSONAS={}", p.display());
        command = command.env("ORBIS_BUNDLED_PERSONAS", p);
    }
    // Pass AUDIO_TRANSPORT=native + socket path to Python. Python reads
    // both to activate the native socket pipeline and to report the
    // correct transport in /healthz.
    #[cfg(feature = "native-audio")]
    if let Some(ref sock_path) = native_audio_sock {
        command = command.env("AUDIO_TRANSPORT", "native");
        #[cfg(all(feature = "voice-processing", target_os = "macos"))]
        {
            command = command.env("ORBIS_AUDIO_INPUT_MODE", "voice_processing");
        }
        #[cfg(not(all(feature = "voice-processing", target_os = "macos")))]
        {
            command = command.env("ORBIS_AUDIO_INPUT_MODE", "cpal");
        }
        command = command.env("ORBIS_AUDIO_SOCK", sock_path);
    }

    let (mut rx, child) = command
        .spawn()
        .map_err(|e| format!("sidecar spawn failed: {e}"))?;

    if let Some(state) = app.try_state::<Sidecar>() {
        state.store(child);
    }

    // Write all sidecar stdout/stderr to a single rotating log file so
    // developers can `tail -f` it without attaching to the process.
    let log_path = app
        .path()
        .app_log_dir()
        .unwrap_or_else(|_| std::env::temp_dir())
        .join("sidecar.log");
    if let Some(parent) = log_path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let mut log_file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
        .ok();
    log::info!("sidecar log: {}", log_path.display());

    // Retain recent stderr so a crash-before-ready surfaces something
    // actionable in the error dialog instead of "unknown error."
    let mut stderr_ring: VecDeque<String> = VecDeque::with_capacity(STDERR_RING_CAPACITY);
    let mut ready = false;

    while let Some(event) = rx.recv().await {
        match event {
            CommandEvent::Stdout(bytes) => {
                let line = String::from_utf8_lossy(&bytes).trim().to_string();
                if line.is_empty() {
                    continue;
                }
                log::info!("[sidecar/stdout] {line}");
                if let Some(f) = log_file.as_mut() {
                    let _ = writeln!(f, "{line}");
                }

                // Parse `ORBIS_BOOT {json}` progress markers and forward
                // them to the UI loading gate. Read off the pipe here so
                // progress flows even while the sidecar's event loop is
                // GIL-stalled loading models.
                if let Some(rest) = line.strip_prefix("ORBIS_BOOT ") {
                    if let Some(state) = app.try_state::<BootState>() {
                        if let Ok(mut g) = state.0.lock() {
                            *g = rest.to_string();
                        }
                    }
                    let _ = app.emit("orbis-boot", rest.to_string());
                }

                // Parse `ORBIS_READY http://<host>:<port>` — the
                // contract added in #20 (agent/paths.py + app.py main).
                if !ready {
                    if let Some(url) = parse_ready(&line) {
                        // Bundled UI: do NOT navigate (navigating to the
                        // sidecar origin dropped WKWebView first-responder,
                        // tao#208 / wry#637, freezing wizard inputs). Store
                        // + inject the sidecar URL so the frontend resolves
                        // /api/* fetches and the SSE stream against it.
                        if let Some(state) = app.try_state::<BackendUrl>() {
                            if let Ok(mut g) = state.0.lock() {
                                *g = Some(url.clone());
                            }
                        }
                        inject_backend_url(&app, &url);
                        tauri::async_runtime::spawn(bridge_sse(app.clone(), url.clone()));
                        ready = true;
                    }
                }
            }
            CommandEvent::Stderr(bytes) => {
                let line = String::from_utf8_lossy(&bytes).trim().to_string();
                if line.is_empty() {
                    continue;
                }
                log::info!("[sidecar/stderr] {line}");
                if let Some(f) = log_file.as_mut() {
                    let _ = writeln!(f, "{line}");
                }
                if stderr_ring.len() == STDERR_RING_CAPACITY {
                    stderr_ring.pop_front();
                }
                stderr_ring.push_back(line);
            }
            CommandEvent::Terminated(payload) => {
                let code = payload.code.unwrap_or(-1);
                log::warn!("sidecar terminated with code {code}");
                handle_termination(&app, code, &stderr_ring, ready);
                break;
            }
            _ => {}
        }
    }
    Ok(())
}

/// Resolve the bundled `config/starter_orbs.yaml` Resource path so we
/// can point the sidecar's `ORBIS_STARTER_ORBS` env var at it. Returns
/// None on resolution failure or when the resource isn't actually
/// present (CI builds where the resource entry got dropped, dev runs
/// without bundling, etc.) — the loader in agent/starter_orbs.py
/// returns `[]` gracefully when its env-var path doesn't exist, so a
/// missing resource just surfaces as the "no starters configured"
/// wizard message instead of a hard crash.
fn resolve_starter_orbs_path(app: &AppHandle) -> Option<PathBuf> {
    match app
        .path()
        .resolve("config/starter_orbs.yaml", BaseDirectory::Resource)
    {
        Ok(p) if p.exists() => Some(p),
        Ok(p) => {
            log::warn!(
                "starter_orbs resource resolved to {} but doesn't exist",
                p.display()
            );
            None
        }
        Err(e) => {
            log::warn!("starter_orbs resource resolve failed: {e}");
            None
        }
    }
}

/// Resolve the bundled `config/personas/` Resource dir for the
/// sidecar's `ORBIS_BUNDLED_PERSONAS` env var. Same graceful-miss
/// posture as `resolve_starter_orbs_path`: agent/personas.py treats a
/// missing dir as an empty catalog, so an absent resource just means
/// no shipped starters.
fn resolve_bundled_personas_dir(app: &AppHandle) -> Option<PathBuf> {
    match app
        .path()
        .resolve("config/personas", BaseDirectory::Resource)
    {
        Ok(p) if p.is_dir() => Some(p),
        Ok(p) => {
            log::warn!(
                "bundled personas resolved to {} but isn't a directory",
                p.display()
            );
            None
        }
        Err(e) => {
            log::warn!("bundled personas resolve failed: {e}");
            None
        }
    }
}

/// Resolve the sidecar's `DELEGATES_YAML` to a PERSISTENT, writable path —
/// `<app_data_dir>/delegates.yaml` — seeded once from the bundled examples.
///
/// This used to point straight at the read-only app Resource
/// (`ORBIS.app/Contents/Resources/config/delegates.yaml`), which the UI's
/// `/api/delegates` writes mutated in place — so every app update (a fresh
/// `.app`) WIPED the user's delegates, and a properly-signed bundle's Resources
/// dir may not even be writable. Mirrors `resolve_config_path`'s pattern for
/// `orbis.yaml` so delegates survive updates + dev rebuilds. Returns None only
/// if `app_data_dir` can't be resolved (Python then degrades to empty).
fn resolve_delegates_path(app: &AppHandle) -> Option<PathBuf> {
    let dest = match app.path().app_data_dir() {
        Ok(dir) => dir.join("delegates.yaml"),
        Err(e) => {
            log::warn!("delegates: app_data_dir resolve failed: {e}");
            return None;
        }
    };
    if !dest.exists() {
        if let Some(parent) = dest.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        match app
            .path()
            .resolve("config/delegates.yaml", BaseDirectory::Resource)
        {
            Ok(res) if res.exists() => match std::fs::copy(&res, &dest) {
                Ok(_) => log::info!(
                    "first-run seed: delegates {} → {}",
                    res.display(),
                    dest.display()
                ),
                Err(e) => log::warn!("delegates seed copy failed: {e}"),
            },
            // No bundled resource — write an empty registry so the file exists
            // and the UI can write to it.
            _ => {
                let _ = std::fs::write(&dest, "delegates: []\n");
                log::info!("delegates: seeded empty {}", dest.display());
            }
        }
    }
    Some(dest)
}

/// First-run seed: if `config_path` doesn't exist yet, copy the bundled
/// `config/orbis.example.yaml` resource into it. The example file ships
/// with a voice-first persona file, sane TTS / orb defaults, and an
/// empty `user_name` + missing `llm` block so the wizard still triggers
/// for the things that need a human decision.
///
/// Any failure here is non-fatal — the sidecar handles a missing config
/// (the wizard writes one from scratch). We log + move on so a
/// resource-resolution edge case doesn't block boot.
fn seed_default_config(app: &AppHandle, config_path: &PathBuf) {
    if config_path.exists() {
        return;
    }
    let resource = match app
        .path()
        .resolve("config/orbis.example.yaml", BaseDirectory::Resource)
    {
        Ok(p) => p,
        Err(e) => {
            log::warn!("first-run seed: example resource resolve failed: {e}");
            return;
        }
    };
    if !resource.exists() {
        log::warn!(
            "first-run seed: example resource not present at {}",
            resource.display()
        );
        return;
    }
    match std::fs::copy(&resource, config_path) {
        Ok(_) => log::info!(
            "first-run seed: copied {} → {}",
            resource.display(),
            config_path.display()
        ),
        Err(e) => log::warn!(
            "first-run seed: copy {} → {} failed: {e}",
            resource.display(),
            config_path.display()
        ),
    }

    let persona_resource = match app
        .path()
        .resolve("config/persona.md", BaseDirectory::Resource)
    {
        Ok(p) => p,
        Err(e) => {
            log::warn!("first-run seed: persona resource resolve failed: {e}");
            return;
        }
    };
    if !persona_resource.exists() {
        log::warn!(
            "first-run seed: persona resource not present at {}",
            persona_resource.display()
        );
        return;
    }
    let persona_path = config_path
        .parent()
        .map(|dir| dir.join("persona.md"))
        .unwrap_or_else(|| PathBuf::from("persona.md"));
    if persona_path.exists() {
        return;
    }
    match std::fs::copy(&persona_resource, &persona_path) {
        Ok(_) => log::info!(
            "first-run seed: copied {} → {}",
            persona_resource.display(),
            persona_path.display()
        ),
        Err(e) => log::warn!(
            "first-run seed: copy {} → {} failed: {e}",
            persona_resource.display(),
            persona_path.display()
        ),
    }
}

/// Pick the path to use for `ORBIS_CONFIG`. Order:
///
///   1. `$ORBIS_CONFIG` from the parent env, if set + non-empty —
///      lets `cargo tauri dev` point at the repo's checked-in YAML.
///   2. `<app_data_dir>/orbis.yaml`, where `app_data_dir` is Tauri's
///      platform-correct user-writable location (e.g. on macOS
///      `~/Library/Application Support/<bundle-id>/`).
///
/// We deliberately don't fall back to a relative path here — that's
/// the trap this PR exists to fix. The bundled sidecar runs with
/// cwd=`/`, so a relative `./orbis.yaml` would resolve to an
/// unwritable root path and re-introduce the original "wizard has
/// nowhere to write" failure mode. If `app_data_dir()` itself fails
/// (extraordinary on a working install) we propagate the error so
/// `supervise_sidecar` surfaces it via the fatal-dialog rather than
/// silently shipping the app to a broken state.
fn resolve_config_path(app: &AppHandle) -> Result<PathBuf, String> {
    if let Ok(value) = std::env::var("ORBIS_CONFIG") {
        // Trim — a whitespace-only value almost always means "this var
        // got templated out / left blank in a launcher script" rather
        // than "the user intentionally pointed config at ` `", so fall
        // through to app_data_dir instead of breaking the boot on the
        // unwritable path the trimmed string would resolve to.
        let trimmed = value.trim();
        if !trimmed.is_empty() {
            return Ok(PathBuf::from(trimmed));
        }
    }
    app.path()
        .app_data_dir()
        .map(|dir| dir.join("orbis.yaml"))
        .map_err(|e| format!("app_data_dir resolve failed: {e}"))
}

/// Extract the URL from a `ORBIS_READY http://host:port` line. Ignores
/// any stdout line that doesn't start with the exact prefix so
/// incidental logs (if they ever land on stdout) don't fool us.
fn parse_ready(line: &str) -> Option<String> {
    let rest = line.strip_prefix("ORBIS_READY ")?;
    let url = rest.split_whitespace().next()?;
    // Cheap validation — a malformed URL would make Tauri's navigate
    // call blow up later, so reject here for a cleaner error path.
    url::Url::parse(url).ok()?;
    Some(url.to_string())
}

/// Point the main webview at the sidecar's ready URL. During `tauri
/// dev` the splash page at `../splash/index.html` was loaded first;
/// here we swap it for the running backend.
#[allow(dead_code)] // retained for reference; bundled UI no longer navigates
fn navigate_webview(app: &AppHandle, url: &str) -> Result<(), String> {
    let parsed = tauri::Url::parse(url).map_err(|e| format!("invalid url {url}: {e}"))?;
    let window = app
        .get_webview_window("main")
        .ok_or_else(|| "main window missing".to_string())?;
    window
        .navigate(parsed)
        .map_err(|e| format!("navigate failed: {e}"))?;
    log::info!("navigated main webview to {url}");
    // Restore keyboard first-responder after the splash→backend
    // navigation. On macOS, navigating the WKWebView to a new origin
    // drops first-responder, so form inputs (e.g. the setup wizard's
    // name fields) silently receive no keystrokes until the user forces
    // focus some other way. Re-focusing the window re-keys the webview.
    if let Err(e) = window.set_focus() {
        log::warn!("set_focus after navigate failed: {e}");
    }
    Ok(())
}

/// Surface a friendly native dialog when the sidecar exits before the
/// ready line, with a branch for the hardware-unsupported case that
/// points users at the Docker path instead of dumping stderr.
fn handle_termination(
    app: &AppHandle,
    exit_code: i32,
    stderr_ring: &VecDeque<String>,
    ready: bool,
) {
    if ready {
        // The sidecar reached ready state and later exited — the user
        // probably quit; don't nag with a dialog.
        return;
    }

    if exit_code == HARDWARE_EXIT_CODE {
        fatal_dialog(
            app,
            "ORBIS needs a supported GPU",
            "ORBIS's voice pipeline runs in real time, so it needs \
             hardware acceleration:\n\n\
             • macOS — Apple Silicon (M1 or newer)\n\n\
             Neither was detected on this machine. If you want to run \
             ORBIS on a CPU-only box anyway, the Docker self-host path \
             is the supported route:\n\n\
             https://github.com/protoLabsAI/ORBIS#docker--with--without-gpu",
        );
        return;
    }

    // Generic crash — show the tail of stderr so the user has
    // something to paste into an issue.
    let tail: Vec<_> = stderr_ring.iter().cloned().collect();
    let detail = if tail.is_empty() {
        format!("sidecar exited with code {exit_code} before becoming ready")
    } else {
        format!(
            "sidecar exited with code {exit_code}. Last output:\n\n{}",
            tail.join("\n")
        )
    };
    fatal_dialog(app, "ORBIS backend crashed", &detail);
}

/// Blocking modal error dialog + app exit. We don't try to let the
/// user recover — if the sidecar couldn't start, the app has nothing
/// to show.
///
/// Runs synchronously so the caller (the sidecar supervisor) can't
/// return before the dialog has shown + the exit has been requested.
/// Previously this spawned an async task and returned immediately,
/// which meant the supervisor's `Ok(())` return could race the
/// dialog appearance on slow machines. Shows on whichever thread
/// calls us — `blocking_show` is safe from non-main threads in
/// Tauri 2.
fn fatal_dialog(app: &AppHandle, title: &str, body: &str) {
    app.dialog()
        .message(body)
        .title(title)
        .kind(MessageDialogKind::Error)
        .blocking_show();
    app.exit(1);
}

#[cfg(test)]
mod tests {
    use super::{get_audio_input_mode, parse_ready};

    #[test]
    fn parse_ready_happy_path() {
        assert_eq!(
            parse_ready("ORBIS_READY http://127.0.0.1:54321").as_deref(),
            Some("http://127.0.0.1:54321"),
        );
    }

    #[test]
    fn parse_ready_ignores_trailing_content() {
        assert_eq!(
            parse_ready("ORBIS_READY http://127.0.0.1:54321 extra data").as_deref(),
            Some("http://127.0.0.1:54321"),
        );
    }

    #[test]
    fn parse_ready_rejects_non_prefix_lines() {
        assert_eq!(parse_ready("INFO starting up"), None);
        assert_eq!(parse_ready(""), None);
    }

    #[test]
    fn parse_ready_rejects_invalid_url() {
        assert_eq!(parse_ready("ORBIS_READY not-a-url"), None);
    }

    #[test]
    fn parse_ready_rejects_prefix_only() {
        // Catches a sidecar that prints the marker but fails to
        // format the URL — we'd otherwise sit on the splash forever.
        assert_eq!(parse_ready("ORBIS_READY"), None);
        assert_eq!(parse_ready("ORBIS_READY "), None);
        assert_eq!(parse_ready("ORBIS_READY   "), None);
    }

    #[test]
    fn audio_input_mode_matches_compile_target() {
        #[cfg(all(feature = "voice-processing", target_os = "macos"))]
        assert_eq!(get_audio_input_mode(), "voice_processing");

        #[cfg(all(
            feature = "native-audio",
            not(all(feature = "voice-processing", target_os = "macos"))
        ))]
        assert_eq!(get_audio_input_mode(), "cpal");

        #[cfg(not(feature = "native-audio"))]
        assert_eq!(get_audio_input_mode(), "unsupported");
    }
}
