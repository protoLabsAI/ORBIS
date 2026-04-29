fn main() {
    tauri_build::build();

    // On macOS compile the AV-permission Obj-C shim and link
    // AVFoundation so `orbis_request_macos_av_access()` (see
    // src/mic_permission.m) is callable from Rust. The shim
    // registers a real TCC mic record at app boot — needed even
    // though we no longer use getUserMedia, because the CPAL audio
    // engine still needs the bundle to appear in System Settings →
    // Privacy & Security → Microphone.
    #[cfg(target_os = "macos")]
    {
        println!("cargo:rerun-if-changed=src/mic_permission.m");
        cc::Build::new()
            .file("src/mic_permission.m")
            .flag("-fobjc-arc")
            .compile("orbis_mac_shims");
        println!("cargo:rustc-link-lib=framework=AVFoundation");
    }
}
