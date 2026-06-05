fn main() {
    tauri_build::build();

    // Rebuild when the baked-in license public key changes, so flipping the
    // paid-unlock gate on/off (option_env!("ORBIS_LICENSE_PUBKEY") in lib.rs)
    // is never masked by a stale incremental cache.
    println!("cargo:rerun-if-env-changed=ORBIS_LICENSE_PUBKEY");

    // For macOS targets, compile the microphone permission shim and link
    // the frameworks it uses. Native audio is Rust/AVAudioEngine-owned;
    // the shim is only responsible for TCC status/request/settings plumbing.
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("macos") {
        println!("cargo:rerun-if-changed=src/mic_permission.m");
        cc::Build::new()
            .file("src/mic_permission.m")
            .flag("-fobjc-arc")
            .compile("orbis_mac_shims");
        println!("cargo:rustc-link-lib=framework=AVFoundation");
        println!("cargo:rustc-link-lib=framework=AppKit");
    }
}
