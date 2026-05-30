//! ORBIS Tauri 2 desktop shell.
//!
//! Thin wrapper around the ORBIS Python backend (the "sidecar").
//!
//! ## Security posture
//!
//! The CSP in `tauri.conf.json#app.security.csp` applies to the
//! splash page only (frontendDist = `../splash`). Once the sidecar
//! is ready we `window.navigate()` to `http://127.0.0.1:<ephemeral>`,
//! which is a different origin and governed by whatever headers the
//! Python backend serves (currently none specific). Both splash and
//! SPA ship from fully-local content on this machine, so the policy
//! is permissive within the app's origin but blocks unexpected
//! network reach. `shell:allow-execute` in the default capability
//! pins the sidecar args so there's no arbitrary-exec surface.
//!
//! ## Runtime flow
//!
//! On boot:
//!
//!   1. Spawn the `orbis` external binary (`binaries/orbis-<target>`
//!      produced by the PyApp workflow at .github/workflows/
//!      desktop-build.yml).
//!   2. Stream its stdout. The Python entry in `app.py:main()` prints
//!      `ORBIS_READY http://127.0.0.1:<port>` once uvicorn is serving.
//!   3. Navigate the main webview at that URL. The Python backend
//!      serves its React SPA from /web/dist/ and the /api/* JSON
//!      endpoints on the same port, so a single webview origin covers
//!      the whole app.
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
        // tauri-plugin-http: routes JS fetch() through Rust + reqwest,
        // bypassing WKWebView's silent POST drop on macOS arm64
        // (tauri-apps/tauri#11854, #13166, #13878). Frontend imports
        // `fetch` from `@tauri-apps/plugin-http`. Origin allow-list
        // is in capabilities/default.json.
        .plugin(tauri_plugin_http::init())
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
                    get_audio_level,
                    get_audio_levels,
                    get_audio_input_mode,
                    get_microphone_permission_status,
                    request_microphone_permission,
                    open_microphone_settings,
                    clear_browsing_data,
                    backend_url,
                    set_mic_listening,
                    mic_listening,
                    api_request,
                    boot_status
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
                    backend_url,
                    api_request,
                    boot_status
                ]
            }
        })
        .manage(Sidecar::new())
        .manage(BackendUrl::default())
        .manage(BootState::default())
        .setup(|app| {
            // Register native audio engine state. Must be done in setup()
            // so it's available before supervise_sidecar runs.
            #[cfg(feature = "native-audio")]
            app.manage(AudioEngineState::new());

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
    log::info!(
        "sidecar env: ORBIS_CONFIG={} START_VLLM={start_vllm}",
        config_path.display()
    );
    if let Some(p) = starter_orbs_path.as_ref() {
        log::info!("sidecar env: ORBIS_STARTER_ORBS={}", p.display());
    }

    // --- Native audio engine ---
    // Start the native mic/speaker engine and Unix socket server. The
    // socket path is passed to the sidecar as ORBIS_AUDIO_SOCK so Python
    // can connect to it. Production macOS builds use AVAudioEngine
    // voice-processing input plus CPAL output.
    #[cfg(feature = "native-audio")]
    let native_audio_sock: Option<std::path::PathBuf> = {
        if true {
            #[cfg(target_os = "macos")]
            {
                let status = ensure_microphone_permission();
                if status != MicrophonePermissionStatus::Authorized {
                    return Err(format!(
                        "microphone permission is {status:?}; ORBIS needs microphone access before native audio can start. Enable it in System Settings → Privacy & Security → Microphone, then reopen ORBIS."
                    ));
                }
            }

            let (mic_tx, mic_rx) =
                tokio::sync::mpsc::unbounded_channel::<audio::engine::AudioMsg>();
            match audio::engine::AudioEngine::new(None, mic_tx) {
                Ok(engine) => {
                    let engine = Arc::new(engine);
                    if let Some(state) = app.try_state::<AudioEngineState>() {
                        state.store(Arc::clone(&engine));
                    }
                    let sock_server = match audio::socket::SocketServer::bind() {
                        Ok(s) => s,
                        Err(e) => {
                            return Err(format!("native audio socket bind failed: {e}"));
                        }
                    };
                    let sock_path = sock_server.path().clone();
                    log::info!("orbis_audio_sock={}", sock_path.display());
                    // Accept loop runs in a background task.
                    tauri::async_runtime::spawn(async move {
                        if let Err(e) = sock_server.accept_and_run(engine, mic_rx).await {
                            log::error!("[audio/socket] accept_and_run failed: {e}");
                        }
                    });
                    Some(sock_path)
                }
                Err(e) => {
                    return Err(format!("native audio engine failed to start: {e}"));
                }
            }
        } else {
            None
        }
    };

    // `sidecar("orbis")` resolves to `binaries/orbis-<target>` on the
    // bundle or `./binaries/orbis-<target>` during `tauri dev`.
    // Target-suffix resolution is Tauri's job — we just give the base
    // name that matches the externalBin entry in tauri.conf.json.
    let mut command = shell
        .sidecar("orbis")
        .map_err(|e| format!("couldn't find sidecar binary: {e}"))?
        .args(["--host", "127.0.0.1", "--port", "0"])
        .env("ORBIS_CONFIG", &config_path)
        // Default STT to Parakeet (MLX) on the native build — far fewer
        // silence-hallucinations than Whisper. Only the default: a
        // `stt.backend` in config (Settings → STT) overrides it.
        .env("STT_BACKEND", "parakeet")
        .env("START_VLLM", &start_vllm);
    // Point the sidecar at the bundled starter-orbs YAML. Without this,
    // agent/starter_orbs.py falls back to a cwd-relative
    // "config/starter_orbs.yaml" path that doesn't exist in the
    // installed app, and the wizard's "Pick your orb" step renders
    // "No starters configured on the server."
    if let Some(p) = starter_orbs_path.as_ref() {
        command = command.env("ORBIS_STARTER_ORBS", p);
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
