// Runtime patch for wry's WKWebView media-capture UIDelegate.
//
// wry 0.54 hardcodes `WKPermissionDecision::Grant` in
// `webView:requestMediaCapturePermissionForOrigin:initiatedByFrame:type:decisionHandler:`,
// see https://github.com/tauri-apps/wry — that's a JavaScript-layer
// grant: WebKit accepts the `getUserMedia()` call, but the resulting
// MediaStream tracks never get real audio data because the underlying
// Core Audio call from the WebContent subprocess was never properly
// gated through TCC. Net symptom: stream is "live" but the level
// meter reads zero forever, voice pipeline errors out reading
// silence ~3-48s after WebRTC connects.
//
// The fix is to swap in a delegate that calls
// `WKPermissionDecisionPrompt` instead. With Prompt, WebKit consults
// macOS TCC properly, finds the cached grant for our bundle id
// (registered via the `AVCaptureDevice.requestAccessForMediaType`
// shim in mic_permission.m + the entitlements / Info.plist /
// Developer ID signing the rest of the desktop pipeline arranges),
// and Core Audio actually wires up the WebContent subprocess to the
// real microphone. From the user's perspective: the prompt flashes
// the first time, then subsequent voice sessions Just Work.
//
// We install the wrapper after the WKWebView is created. wry sets
// its UIDelegate during webview construction; we don't have a clean
// hook into that lifecycle, so the patch polls the NSApplication
// windows on the main queue and retries every 100ms until it
// finds the webview, then attaches once. Idempotent — keyed on an
// associated-object marker so a second install call is a no-op.
//
// Forwarding: only the media-capture method is overridden. All other
// WKUIDelegate calls (alert panels, target=_blank navigation, etc.)
// fall through to wry's original delegate via
// `forwardingTargetForSelector:`. That keeps every Tauri/wry feature
// working unchanged.

#import <AppKit/AppKit.h>
#import <WebKit/WebKit.h>
#import <objc/runtime.h>

// MARK: - Wrapper delegate

@interface OrbisMediaCaptureDelegate : NSObject <WKUIDelegate>
@property (nonatomic, strong, readwrite) id<WKUIDelegate> wrapped;
@end

@implementation OrbisMediaCaptureDelegate

- (instancetype)initWithWrapped:(id<WKUIDelegate>)wrapped {
    if ((self = [super init])) {
        _wrapped = wrapped;
    }
    return self;
}

// The one method we actually override — flip Grant → Prompt so TCC
// gets consulted instead of bypassed.
- (void)webView:(WKWebView *)webView
    requestMediaCapturePermissionForOrigin:(WKSecurityOrigin *)origin
                          initiatedByFrame:(WKFrameInfo *)frame
                                      type:(WKMediaCaptureType)type
                           decisionHandler:(void (^)(WKPermissionDecision))decisionHandler {
    (void)webView;
    (void)origin;
    (void)frame;
    (void)type;
    decisionHandler(WKPermissionDecisionPrompt);
}

// Pretend to respond to anything wry's delegate responds to so
// WebKit dispatches everything through us. Selectors we implement
// directly (the media-capture one above) take precedence; all others
// fall through to `wrapped` via the forwarding target.
- (BOOL)respondsToSelector:(SEL)aSelector {
    if ([super respondsToSelector:aSelector]) return YES;
    return [self.wrapped respondsToSelector:aSelector];
}

- (id)forwardingTargetForSelector:(SEL)aSelector {
    (void)aSelector;
    return self.wrapped;
}

@end

// MARK: - View-tree walk

static WKWebView *orbis_find_wkwebview(NSView *view) {
    if (!view) return nil;
    if ([view isKindOfClass:[WKWebView class]]) return (WKWebView *)view;
    for (NSView *subview in view.subviews) {
        WKWebView *hit = orbis_find_wkwebview(subview);
        if (hit) return hit;
    }
    return nil;
}

// MARK: - Install + retry loop

// Static address used as the key for `objc_setAssociatedObject` so
// repeated install attempts on the same WKWebView are no-ops. The
// associated object also keeps the wrapper alive (WKWebView's
// UIDelegate property is `weak`).
static char kOrbisMediaDelegateKey;

static BOOL orbis_install_on_window(NSWindow *window) {
    WKWebView *wkv = orbis_find_wkwebview(window.contentView);
    if (!wkv) return NO;
    if (objc_getAssociatedObject(wkv, &kOrbisMediaDelegateKey)) return YES; // already done
    id<WKUIDelegate> original = wkv.UIDelegate;
    OrbisMediaCaptureDelegate *wrapper =
        [[OrbisMediaCaptureDelegate alloc] initWithWrapped:original];
    wkv.UIDelegate = wrapper;
    objc_setAssociatedObject(wkv, &kOrbisMediaDelegateKey, wrapper,
                             OBJC_ASSOCIATION_RETAIN_NONATOMIC);
    return YES;
}

// Heartbeat install loop. We can't reliably hook into WKWebView
// creation (wry doesn't expose it; subclassing or NSNotificationCenter
// observation are both fragile), so we re-scan every second instead.
// `orbis_install_on_window` is keyed on an associated-object marker
// so installs are idempotent — once a webview is wrapped it stays
// wrapped, and the loop is a no-op for that webview. New WKWebViews
// (reload, navigate-replacement, future-window) get caught within
// the next tick, which keeps the media-capture Prompt behavior
// surviving full-page reloads.
static void orbis_install_tick(void);

static void orbis_install_tick(void) {
    for (NSWindow *window in [NSApplication sharedApplication].windows) {
        (void)orbis_install_on_window(window);
    }
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(NSEC_PER_SEC)),
                   dispatch_get_main_queue(), ^{
        orbis_install_tick();
    });
}

// MARK: - C entry point

void orbis_install_media_capture_prompt(void) {
    // Always trampoline to the main thread — NSWindow/WKWebView
    // mutations are main-queue-only on AppKit.
    dispatch_async(dispatch_get_main_queue(), ^{
        orbis_install_tick();
    });
}
