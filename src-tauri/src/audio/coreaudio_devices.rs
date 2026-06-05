//! Minimal Core Audio FFI for input-device selection in the AVAudioEngine
//! voice-processing path (Phase 2). CPAL picks an input device by name on its
//! own; AVAudioEngine's input node defaults to the system default and only
//! honors a *specific* device when we set `kAudioOutputUnitProperty_CurrentDevice`
//! (an AudioDeviceID) on its underlying AUHAL audio unit. CPAL doesn't expose
//! the AudioDeviceID, so we enumerate Core Audio ourselves and match by name
//! (CPAL uses the same Core Audio device names on macOS).
//!
//! Hand-rolled FFI (no bindgen dep) — the surface is tiny: enumerate input
//! devices, and set the current device on an audio unit.
//!
//! The *enumeration* half (`input_devices` / `list_input_device_names`) is pure
//! `AudioObjectGetPropertyData` property reads — no stream/IOProc is ever
//! opened, so it never lights the macOS mic indicator. That's why
//! `AudioEngine::list_input_devices` routes through here on macOS instead of
//! CPAL's `input_devices()`, which probes each device (opening it) and so
//! flickered the mic indicator + stalled the Voice tab. The AudioUnit *setter*
//! half stays behind `voice-processing` (it needs the AVAudioEngine input
//! node's AUHAL).
#![cfg(target_os = "macos")]

use std::os::raw::c_void;

type OSStatus = i32;
type AudioObjectID = u32;
type AudioDeviceID = u32;

const K_AUDIO_OBJECT_SYSTEM_OBJECT: AudioObjectID = 1;
const K_AUDIO_OBJECT_PROPERTY_ELEMENT_MAIN: u32 = 0;
// fourcc helpers
const fn fourcc(s: &[u8; 4]) -> u32 {
    ((s[0] as u32) << 24) | ((s[1] as u32) << 16) | ((s[2] as u32) << 8) | (s[3] as u32)
}
const K_AUDIO_HARDWARE_PROPERTY_DEVICES: u32 = fourcc(b"dev#");
const K_AUDIO_OBJECT_PROPERTY_SCOPE_GLOBAL: u32 = fourcc(b"glob");
const K_AUDIO_OBJECT_PROPERTY_SCOPE_INPUT: u32 = fourcc(b"inpt");
const K_AUDIO_OBJECT_PROPERTY_NAME: u32 = fourcc(b"lnam");
const K_AUDIO_DEVICE_PROPERTY_STREAM_CONFIGURATION: u32 = fourcc(b"slay");
#[cfg(feature = "voice-processing")]
const K_AUDIO_OUTPUT_UNIT_PROPERTY_CURRENT_DEVICE: u32 = 2000;
#[cfg(feature = "voice-processing")]
const K_AUDIO_UNIT_SCOPE_GLOBAL: u32 = 0;
const K_CFSTRING_ENCODING_UTF8: u32 = 0x0800_0100;

#[repr(C)]
struct AudioObjectPropertyAddress {
    selector: u32,
    scope: u32,
    element: u32,
}

#[repr(C)]
struct AudioBuffer {
    number_channels: u32,
    data_byte_size: u32,
    data: *mut c_void,
}

#[repr(C)]
struct AudioBufferList {
    number_buffers: u32,
    buffers: [AudioBuffer; 1],
}

#[link(name = "CoreAudio", kind = "framework")]
extern "C" {
    fn AudioObjectGetPropertyDataSize(
        object: AudioObjectID,
        address: *const AudioObjectPropertyAddress,
        qualifier_data_size: u32,
        qualifier_data: *const c_void,
        out_data_size: *mut u32,
    ) -> OSStatus;
    fn AudioObjectGetPropertyData(
        object: AudioObjectID,
        address: *const AudioObjectPropertyAddress,
        qualifier_data_size: u32,
        qualifier_data: *const c_void,
        io_data_size: *mut u32,
        out_data: *mut c_void,
    ) -> OSStatus;
}

#[cfg(feature = "voice-processing")]
#[link(name = "AudioToolbox", kind = "framework")]
extern "C" {
    fn AudioUnitSetProperty(
        unit: *mut c_void,
        prop_id: u32,
        scope: u32,
        element: u32,
        data: *const c_void,
        data_size: u32,
    ) -> OSStatus;
}

#[link(name = "CoreFoundation", kind = "framework")]
extern "C" {
    fn CFStringGetCString(
        s: *const c_void,
        buffer: *mut u8,
        buffer_size: isize,
        encoding: u32,
    ) -> u8;
    fn CFRelease(cf: *const c_void);
}

fn addr(selector: u32, scope: u32) -> AudioObjectPropertyAddress {
    AudioObjectPropertyAddress {
        selector,
        scope,
        element: K_AUDIO_OBJECT_PROPERTY_ELEMENT_MAIN,
    }
}

/// All Core Audio input devices as (AudioDeviceID, name).
fn input_devices() -> Vec<(AudioDeviceID, String)> {
    let mut out = Vec::new();
    unsafe {
        let devices_addr = addr(
            K_AUDIO_HARDWARE_PROPERTY_DEVICES,
            K_AUDIO_OBJECT_PROPERTY_SCOPE_GLOBAL,
        );
        let mut size: u32 = 0;
        if AudioObjectGetPropertyDataSize(
            K_AUDIO_OBJECT_SYSTEM_OBJECT,
            &devices_addr,
            0,
            std::ptr::null(),
            &mut size,
        ) != 0
            || size == 0
        {
            return out;
        }
        let count = size as usize / std::mem::size_of::<AudioDeviceID>();
        let mut ids: Vec<AudioDeviceID> = vec![0; count];
        let mut io = size;
        if AudioObjectGetPropertyData(
            K_AUDIO_OBJECT_SYSTEM_OBJECT,
            &devices_addr,
            0,
            std::ptr::null(),
            &mut io,
            ids.as_mut_ptr() as *mut c_void,
        ) != 0
        {
            return out;
        }
        for id in ids {
            if device_input_channel_count(id) == 0 {
                continue; // output-only device — skip
            }
            if let Some(name) = device_name(id) {
                out.push((id, name));
            }
        }
    }
    out
}

/// Input device names only — for the mic picker. Pure HAL property reads; no
/// audio stream is opened, so the macOS mic indicator stays dark (unlike CPAL's
/// `input_devices()` probing, which opens each device).
pub fn list_input_device_names() -> Vec<String> {
    input_devices().into_iter().map(|(_, name)| name).collect()
}

/// Input channel count for a device (0 = not an input device).
unsafe fn device_input_channel_count(id: AudioDeviceID) -> u32 {
    let cfg_addr = addr(
        K_AUDIO_DEVICE_PROPERTY_STREAM_CONFIGURATION,
        K_AUDIO_OBJECT_PROPERTY_SCOPE_INPUT,
    );
    let mut size: u32 = 0;
    if AudioObjectGetPropertyDataSize(id, &cfg_addr, 0, std::ptr::null(), &mut size) != 0
        || size == 0
    {
        return 0;
    }
    let mut buf = vec![0u8; size as usize];
    let mut io = size;
    if AudioObjectGetPropertyData(
        id,
        &cfg_addr,
        0,
        std::ptr::null(),
        &mut io,
        buf.as_mut_ptr() as *mut c_void,
    ) != 0
    {
        return 0;
    }
    let list = &*(buf.as_ptr() as *const AudioBufferList);
    let n = list.number_buffers as usize;
    let buffers = std::slice::from_raw_parts(list.buffers.as_ptr(), n);
    buffers.iter().map(|b| b.number_channels).sum()
}

/// Human-readable device name.
unsafe fn device_name(id: AudioDeviceID) -> Option<String> {
    let name_addr = addr(
        K_AUDIO_OBJECT_PROPERTY_NAME,
        K_AUDIO_OBJECT_PROPERTY_SCOPE_GLOBAL,
    );
    let mut cfstr: *const c_void = std::ptr::null();
    let mut io = std::mem::size_of::<*const c_void>() as u32;
    if AudioObjectGetPropertyData(
        id,
        &name_addr,
        0,
        std::ptr::null(),
        &mut io,
        &mut cfstr as *mut _ as *mut c_void,
    ) != 0
        || cfstr.is_null()
    {
        return None;
    }
    let mut buf = [0u8; 256];
    let ok = CFStringGetCString(
        cfstr,
        buf.as_mut_ptr(),
        buf.len() as isize,
        K_CFSTRING_ENCODING_UTF8,
    );
    CFRelease(cfstr);
    if ok == 0 {
        return None;
    }
    let end = buf.iter().position(|&b| b == 0).unwrap_or(buf.len());
    String::from_utf8(buf[..end].to_vec()).ok()
}

/// Resolve a device name (as listed to the user) to its AudioDeviceID.
#[cfg(feature = "voice-processing")]
pub fn input_device_id_for_name(name: &str) -> Option<AudioDeviceID> {
    input_devices()
        .into_iter()
        .find(|(_, n)| n == name)
        .map(|(id, _)| id)
}

/// Set the current input device on an audio unit (the AVAudioEngine input
/// node's AUHAL). `unit` is the raw `AudioUnit` from `-[AVAudioIONode audioUnit]`.
#[cfg(feature = "voice-processing")]
pub fn set_current_input_device(unit: *mut c_void, device_id: AudioDeviceID) -> Result<(), String> {
    let status = unsafe {
        AudioUnitSetProperty(
            unit,
            K_AUDIO_OUTPUT_UNIT_PROPERTY_CURRENT_DEVICE,
            K_AUDIO_UNIT_SCOPE_GLOBAL,
            0,
            &device_id as *const AudioDeviceID as *const c_void,
            std::mem::size_of::<AudioDeviceID>() as u32,
        )
    };
    if status == 0 {
        Ok(())
    } else {
        Err(format!(
            "AudioUnitSetProperty(CurrentDevice) failed: {status}"
        ))
    }
}
