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
use std::path::PathBuf;
use std::sync::Mutex;

use tauri::path::BaseDirectory;
use tauri::{AppHandle, Manager, RunEvent};
use tauri_plugin_dialog::{DialogExt, MessageDialogKind};

#[cfg(target_os = "macos")]
extern "C" {
    /// Trigger TCC prompts for mic + camera via `AVCaptureDevice` on
    /// macOS. Defined in `src/mic_permission.m`; without this the
    /// webview's `getUserMedia` call silently auto-grants through
    /// wry's default WKUIDelegate, so Core Audio hands back dead
    /// streams and the app never appears under System Settings →
    /// Privacy & Security → Microphone. We fire this once at boot —
    /// the first call shows the consent dialog; subsequent calls
    /// (across launches) hit the cached TCC decision. See the shim's
    /// header comment for the full rationale.
    fn orbis_request_macos_av_access();
}
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

/// Max stderr lines retained for error-dialog context if the sidecar
/// exits non-zero before the ready line.
const STDERR_RING_CAPACITY: usize = 80;

/// Exit code the Python hardware probe uses. See agent/hardware.py.
const HARDWARE_EXIT_CODE: i32 = 2;

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
        .manage(Sidecar::new())
        .setup(|app| {
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
    let config_path = resolve_config_path(&app);
    if let Some(parent) = config_path.parent() {
        if let Err(e) = std::fs::create_dir_all(parent) {
            log::warn!("couldn't create config dir {}: {e}", parent.display());
        }
    }
    seed_default_config(&app, &config_path);
    let start_vllm = std::env::var("START_VLLM").unwrap_or_else(|_| "0".to_string());
    log::info!(
        "sidecar env: ORBIS_CONFIG={} START_VLLM={start_vllm}",
        config_path.display()
    );

    // `sidecar("orbis")` resolves to `binaries/orbis-<target>` on the
    // bundle or `./binaries/orbis-<target>` during `tauri dev`.
    // Target-suffix resolution is Tauri's job — we just give the base
    // name that matches the externalBin entry in tauri.conf.json.
    let command = shell
        .sidecar("orbis")
        .map_err(|e| format!("couldn't find sidecar binary: {e}"))?
        .args(["--host", "127.0.0.1", "--port", "0"])
        .env("ORBIS_CONFIG", &config_path)
        .env("START_VLLM", &start_vllm);

    let (mut rx, child) = command
        .spawn()
        .map_err(|e| format!("sidecar spawn failed: {e}"))?;

    if let Some(state) = app.try_state::<Sidecar>() {
        state.store(child);
    }

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
///   3. Last-ditch fallback: `./orbis.yaml` next to the binary —
///      only reached if Tauri's path resolver itself errors, which
///      shouldn't happen in practice. Logged loudly so we notice.
fn resolve_config_path(app: &AppHandle) -> PathBuf {
    if let Ok(value) = std::env::var("ORBIS_CONFIG") {
        if !value.is_empty() {
            return PathBuf::from(value);
        }
    }
    match app.path().app_data_dir() {
        Ok(dir) => dir.join("orbis.yaml"),
        Err(e) => {
            log::error!("app_data_dir resolve failed ({e}); falling back to ./orbis.yaml");
            PathBuf::from("orbis.yaml")
        }
    }
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
