fn main() {
    tauri_build::build();

    // On macOS compile the AV-permission Obj-C shim and link
    // AVFoundation so `orbis_request_macos_av_access()` (see
    // src/mic_permission.m) is callable from Rust. We need this
    // because wry's default WKUIDelegate silently auto-grants
    // media-capture without asking TCC — the Rust-side call on
    // boot registers a real TCC record + surfaces the consent
    // prompt; from that point the webview's `getUserMedia` works.
    #[cfg(target_os = "macos")]
    {
        println!("cargo:rerun-if-changed=src/mic_permission.m");
        cc::Build::new()
            .file("src/mic_permission.m")
            .flag("-fobjc-arc")
            .compile("orbis_mic_permission");
        println!("cargo:rustc-link-lib=framework=AVFoundation");
    }
}
