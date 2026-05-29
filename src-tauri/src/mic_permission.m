// Minimal Obj-C shim for macOS microphone TCC.
//
// ORBIS owns audio natively in Rust/AVAudioEngine. This file does not
// grant WebView media access and intentionally never touches camera
// permissions. It exposes a synchronous request helper so Rust can gate
// native audio startup on an explicit TCC grant instead of racing the
// prompt or starting the sidecar with a dead microphone.

#import <AVFoundation/AVFoundation.h>
#import <AppKit/AppKit.h>
#import <dispatch/dispatch.h>
#import <stdbool.h>

int orbis_macos_microphone_authorization_status(void) {
    return (int)[AVCaptureDevice authorizationStatusForMediaType:AVMediaTypeAudio];
}

bool orbis_request_macos_microphone_access_blocking(void) {
    AVAuthorizationStatus status =
        [AVCaptureDevice authorizationStatusForMediaType:AVMediaTypeAudio];

    if (status == AVAuthorizationStatusAuthorized) {
        return true;
    }
    if (status == AVAuthorizationStatusDenied ||
        status == AVAuthorizationStatusRestricted) {
        return false;
    }

    __block BOOL granted = NO;
    dispatch_semaphore_t sema = dispatch_semaphore_create(0);
    [AVCaptureDevice requestAccessForMediaType:AVMediaTypeAudio
                             completionHandler:^(BOOL didGrant) {
        granted = didGrant;
        dispatch_semaphore_signal(sema);
    }];
    dispatch_semaphore_wait(sema, DISPATCH_TIME_FOREVER);
    return granted;
}

void orbis_open_macos_microphone_settings(void) {
    NSURL *url = [NSURL URLWithString:
        @"x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"];
    [[NSWorkspace sharedWorkspace] openURL:url];
}
