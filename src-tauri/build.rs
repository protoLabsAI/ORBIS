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
        println!("cargo:rerun-if-changed=src/media_permission_patch.m");
        cc::Build::new()
            .file("src/mic_permission.m")
            .file("src/media_permission_patch.m")
            .flag("-fobjc-arc")
            .compile("orbis_mac_shims");
        println!("cargo:rustc-link-lib=framework=AVFoundation");
        // WebKit for the WKWebView delegate swap, AppKit for NSWindow /
        // NSView traversal. CoreFoundation comes in transitively via
        // those, no explicit link needed.
        println!("cargo:rustc-link-lib=framework=WebKit");
        println!("cargo:rustc-link-lib=framework=AppKit");
    }
}
