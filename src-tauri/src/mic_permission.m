// Minimal Obj-C shim that triggers macOS's mic + camera TCC prompts
// from the Rust app-launch path, before the webview ever opens.
//
// Without this, WKWebView's default `WKUIDelegate` (as installed by
// wry) silently auto-grants `getUserMedia` requests without ever
// asking TCC. The resulting MediaStream tracks are "alive" but Core
// Audio hands back silence forever, so our voice pipeline errors out
// ~48s in reading the dead stream. That path also means the app
// never shows up in System Settings → Privacy & Security → Microphone,
// so the user has no UI to grant permission from.
//
// `requestAccessForMediaType:` registers the request with TCC (the
// app *does* then appear in System Settings), surfaces the native
// consent prompt on first call, and caches the grant/deny outcome
// for subsequent launches. We fire-and-forget; the async completion
// block's boolean is useful only for logging — by the time it runs,
// TCC already has an entry for our bundle id either way.

#import <AVFoundation/AVFoundation.h>

void orbis_request_macos_av_access(void) {
    [AVCaptureDevice requestAccessForMediaType:AVMediaTypeAudio
                             completionHandler:^(BOOL granted) {
        // Intentionally empty. See header comment — by the time this
        // fires the user has already answered the dialog or TCC has
        // returned a cached decision; the webview's later
        // `getUserMedia` inherits that result. Logging the bool would
        // be nice but we don't have log state wired into this TU.
        (void)granted;
    }];
    [AVCaptureDevice requestAccessForMediaType:AVMediaTypeVideo
                             completionHandler:^(BOOL granted) {
        (void)granted;
    }];
}
