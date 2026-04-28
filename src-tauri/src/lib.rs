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

use std::collections::VecDeque;
use std::fs::OpenOptions;
use std::io::Write;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

#[cfg(feature = "native-audio")]
mod audio;

use tauri::path::BaseDirectory;
use tauri::{AppHandle, Manager, RunEvent};
use tauri_plugin_dialog::{DialogExt, MessageDialogKind};

#[cfg(target_os = "macos")]
extern "C" {
    /// macOS. Defined in `src/mic_permission.m`; without this the
    /// webview's `getUserMedia` call silently auto-grants through
    /// wry's default WKUIDelegate, so Core Audio hands back dead
    /// streams and the app never appears under System Settings →
    /// Privacy & Security → Microphone. We fire this once at boot —
    /// the first call shows the consent dialog; subsequent calls
    /// (across launches) hit the cached TCC decision. See the shim's
    /// header comment for the full rationale.
    fn orbis_request_macos_av_access();

    /// Replace wry's WKWebView UIDelegate's media-capture decision
    /// from `Grant` to `Prompt`, so Core Audio's TCC gate is
    /// actually consulted instead of bypassed. See
    /// `src/media_permission_patch.m` for the full rationale —
    /// short version: wry hardcodes Grant, which is a JS-layer
    /// approval, but the WebContent subprocess then can't actually
    /// open the audio device because the OS-level permission
    /// pathway was short-circuited. Calling Prompt routes through
    /// TCC properly, finds the grant we already established via
    /// `orbis_request_macos_av_access`, and the stream wires up.
    fn orbis_install_media_capture_prompt();
}
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

/// Max stderr lines retained for error-dialog context if the sidecar
/// exits non-zero before the ready line.
const STDERR_RING_CAPACITY: usize = 80;

/// Exit code the Python hardware probe uses. See agent/hardware.py.
const HARDWARE_EXIT_CODE: i32 = 2;

/// Tauri-managed state: the native CPAL audio engine, kept alive for
/// the duration of the app. Only present when `native-audio` feature
/// is compiled in and `AUDIO_TRANSPORT=native` is set at runtime.
#[cfg(feature = "native-audio")]
struct AudioEngineState {
    engine: Mutex<Option<Arc<audio::engine::AudioEngine>>>,
}

#[cfg(feature = "native-audio")]
impl AudioEngineState {
    fn new() -> Self {
        Self { engine: Mutex::new(None) }
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

/// Return the names of all CPAL input devices on the host.
/// Only compiled when the `native-audio` feature is active.
#[cfg(feature = "native-audio")]
#[tauri::command]
fn list_audio_inputs() -> Vec<String> {
    audio::engine::AudioEngine::list_input_devices()
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

pub fn run() {
    // env_logger so `RUST_LOG=debug` surfaces the sidecar stream + our
    // own parsing logs during development.
    let _ = env_logger::builder()
        .filter_level(log::LevelFilter::Info)
        .parse_default_env()
        .try_init();

    // Kick the macOS TCC consent dialog for mic + camera now, before
    // the webview opens. Completion blocks fire async but that's fine
    // — by the time the user interacts with the app the grant has
    // landed and the webview's `getUserMedia` will pick it up. See
    // `src/mic_permission.m` for the full why.
    #[cfg(target_os = "macos")]
    unsafe {
        orbis_request_macos_av_access();
    }

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler({
            #[cfg(feature = "native-audio")]
            { tauri::generate_handler![list_audio_inputs, get_audio_level] }
            #[cfg(not(feature = "native-audio"))]
            { tauri::generate_handler![] }
        })
        .manage(Sidecar::new())
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
    // Schedule the WKWebView UIDelegate swap as soon as we're past
    // setup. The Obj-C side trampolines to the main queue and polls
    // for the webview to exist, so timing here is forgiving.
    #[cfg(target_os = "macos")]
    unsafe {
        orbis_install_media_capture_prompt();
    }

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
        std::fs::create_dir_all(parent).map_err(|e| {
            format!(
                "couldn't create config dir {}: {e}",
                parent.display()
            )
        })?;
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

    // --- Native audio engine (optional) ---
    // Start CPAL mic/speaker engine and Unix socket server when
    // AUDIO_TRANSPORT=native. The socket path is passed to the sidecar
    // as ORBIS_AUDIO_SOCK so Python can connect to it.
    // When compiled with --features native-audio the desktop app always
    // runs in native mode — no env var override required.
    #[cfg(feature = "native-audio")]
    let native_audio_sock: Option<std::path::PathBuf> = {
        if true {
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
                    log::info!(
                        "orbis_audio_sock={}",
                        sock_path.display()
                    );
                    // Accept loop runs in a background task.
                    tauri::async_runtime::spawn(async move {
                        if let Err(e) = sock_server.accept_and_run(engine, mic_rx).await {
                            log::error!("[audio/socket] accept_and_run failed: {e}");
                        }
                    });
                    Some(sock_path)
                }
                Err(e) => {
                    log::warn!("[audio] failed to start native audio engine: {e} — falling back to WebRTC");
                    None
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
        .env("START_VLLM", &start_vllm);
    // Point the sidecar at the bundled starter-orbs YAML. Without this,
    // agent/starter_orbs.py falls back to a cwd-relative
    // "config/starter_orbs.yaml" path that doesn't exist in the
    // installed app, and the wizard's "Pick your orb" step renders
    // "No starters configured on the server."
    if let Some(p) = starter_orbs_path.as_ref() {
        command = command.env("ORBIS_STARTER_ORBS", p);
    }
    // Pass AUDIO_TRANSPORT=native + socket path to Python when built
    // with native-audio. Python reads both to activate the CPAL pipeline
    // and to report the correct transport in /healthz.
    #[cfg(feature = "native-audio")]
    if let Some(ref sock_path) = native_audio_sock {
        command = command
            .env("AUDIO_TRANSPORT", "native")
            .env("ORBIS_AUDIO_SOCK", sock_path);
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

                // Parse `ORBIS_READY http://<host>:<port>` — the
                // contract added in #20 (agent/paths.py + app.py main).
                if !ready {
                    if let Some(url) = parse_ready(&line) {
                        navigate_webview(&app, &url)?;
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
/// with a working baked-in persona, sane TTS / orb defaults, and an
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
fn navigate_webview(app: &AppHandle, url: &str) -> Result<(), String> {
    let parsed = tauri::Url::parse(url).map_err(|e| format!("invalid url {url}: {e}"))?;
    let window = app
        .get_webview_window("main")
        .ok_or_else(|| "main window missing".to_string())?;
    window
        .navigate(parsed)
        .map_err(|e| format!("navigate failed: {e}"))?;
    log::info!("navigated main webview to {url}");
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
             • macOS — Apple Silicon (M1 or newer)\n\
             • Windows / Linux — NVIDIA GPU with driver 570 or newer\n\n\
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
    use super::parse_ready;

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
}
