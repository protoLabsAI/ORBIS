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
// VPIO clock-unification (speaker AEC): force the output device's nominal rate
// to match the mic so the single VoiceProcessingIO unit has one sample rate.
#[cfg(feature = "voice-processing")]
const K_AUDIO_HARDWARE_PROPERTY_DEFAULT_INPUT_DEVICE: u32 = fourcc(b"dIn ");
#[cfg(feature = "voice-processing")]
const K_AUDIO_HARDWARE_PROPERTY_DEFAULT_OUTPUT_DEVICE: u32 = fourcc(b"dOut");
#[cfg(feature = "voice-processing")]
const K_AUDIO_DEVICE_PROPERTY_NOMINAL_SAMPLE_RATE: u32 = fourcc(b"nsrt");
#[cfg(feature = "voice-processing")]
const K_AUDIO_DEVICE_PROPERTY_AVAILABLE_NOMINAL_SAMPLE_RATES: u32 = fourcc(b"nsr#");
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

#[cfg(feature = "voice-processing")]
#[repr(C)]
struct AudioValueRange {
    minimum: f64,
    maximum: f64,
}

#[cfg(feature = "voice-processing")]
#[link(name = "CoreAudio", kind = "framework")]
extern "C" {
    fn AudioObjectSetPropertyData(
        object: AudioObjectID,
        address: *const AudioObjectPropertyAddress,
        qualifier_data_size: u32,
        qualifier_data: *const c_void,
        data_size: u32,
        data: *const c_void,
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

/// Human-readable name for any device id (input or output).
#[cfg(feature = "voice-processing")]
pub fn device_name_for_id(id: AudioDeviceID) -> Option<String> {
    unsafe { device_name(id) }
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

/// The system default output/input device's AudioDeviceID (0 → None).
#[cfg(feature = "voice-processing")]
fn default_device_id(selector: u32) -> Option<AudioDeviceID> {
    unsafe {
        let a = addr(selector, K_AUDIO_OBJECT_PROPERTY_SCOPE_GLOBAL);
        let mut id: AudioDeviceID = 0;
        let mut io = std::mem::size_of::<AudioDeviceID>() as u32;
        if AudioObjectGetPropertyData(
            K_AUDIO_OBJECT_SYSTEM_OBJECT,
            &a,
            0,
            std::ptr::null(),
            &mut io,
            &mut id as *mut _ as *mut c_void,
        ) != 0
            || id == 0
        {
            None
        } else {
            Some(id)
        }
    }
}

/// System default output device.
#[cfg(feature = "voice-processing")]
pub fn default_output_device_id() -> Option<AudioDeviceID> {
    default_device_id(K_AUDIO_HARDWARE_PROPERTY_DEFAULT_OUTPUT_DEVICE)
}

/// System default input device.
#[cfg(feature = "voice-processing")]
pub fn default_input_device_id() -> Option<AudioDeviceID> {
    default_device_id(K_AUDIO_HARDWARE_PROPERTY_DEFAULT_INPUT_DEVICE)
}

/// A device's current nominal sample rate in Hz.
#[cfg(feature = "voice-processing")]
pub fn device_nominal_sample_rate(id: AudioDeviceID) -> Option<f64> {
    unsafe {
        let a = addr(
            K_AUDIO_DEVICE_PROPERTY_NOMINAL_SAMPLE_RATE,
            K_AUDIO_OBJECT_PROPERTY_SCOPE_GLOBAL,
        );
        let mut rate: f64 = 0.0;
        let mut io = std::mem::size_of::<f64>() as u32;
        if AudioObjectGetPropertyData(
            id,
            &a,
            0,
            std::ptr::null(),
            &mut io,
            &mut rate as *mut _ as *mut c_void,
        ) != 0
            || rate <= 0.0
        {
            None
        } else {
            Some(rate)
        }
    }
}

/// Whether a device can run at `rate` (reads AvailableNominalSampleRates).
#[cfg(feature = "voice-processing")]
pub fn device_supports_sample_rate(id: AudioDeviceID, rate: f64) -> bool {
    unsafe {
        let a = addr(
            K_AUDIO_DEVICE_PROPERTY_AVAILABLE_NOMINAL_SAMPLE_RATES,
            K_AUDIO_OBJECT_PROPERTY_SCOPE_GLOBAL,
        );
        let mut size: u32 = 0;
        if AudioObjectGetPropertyDataSize(id, &a, 0, std::ptr::null(), &mut size) != 0 || size == 0 {
            return false;
        }
        let n = size as usize / std::mem::size_of::<AudioValueRange>();
        let mut ranges: Vec<AudioValueRange> = (0..n)
            .map(|_| AudioValueRange {
                minimum: 0.0,
                maximum: 0.0,
            })
            .collect();
        let mut io = size;
        if AudioObjectGetPropertyData(
            id,
            &a,
            0,
            std::ptr::null(),
            &mut io,
            ranges.as_mut_ptr() as *mut c_void,
        ) != 0
        {
            return false;
        }
        // Ranges are usually discrete (min == max); 1 Hz tolerance for safety.
        ranges
            .iter()
            .any(|r| rate >= r.minimum - 1.0 && rate <= r.maximum + 1.0)
    }
}

/// Set a device's nominal sample rate and block until the change actually
/// takes effect. `AudioObjectSetPropertyData` is asynchronous — starting an
/// AVAudioEngine before the HAL has switched races straight into -10875 — so we
/// poll the read-back (up to ~500 ms). Returns the PREVIOUS rate so the caller
/// can restore it on teardown (the change is system-global).
#[cfg(feature = "voice-processing")]
pub fn set_device_nominal_sample_rate(id: AudioDeviceID, rate: f64) -> Result<f64, String> {
    let prev = device_nominal_sample_rate(id)
        .ok_or_else(|| "couldn't read current nominal rate".to_string())?;
    if (prev - rate).abs() < 1.0 {
        return Ok(prev); // already there
    }
    let status = unsafe {
        let a = addr(
            K_AUDIO_DEVICE_PROPERTY_NOMINAL_SAMPLE_RATE,
            K_AUDIO_OBJECT_PROPERTY_SCOPE_GLOBAL,
        );
        AudioObjectSetPropertyData(
            id,
            &a,
            0,
            std::ptr::null(),
            std::mem::size_of::<f64>() as u32,
            &rate as *const f64 as *const c_void,
        )
    };
    if status != 0 {
        return Err(format!(
            "AudioObjectSetPropertyData(NominalSampleRate={rate}) failed: {status}"
        ));
    }
    for _ in 0..20 {
        if let Some(cur) = device_nominal_sample_rate(id) {
            if (cur - rate).abs() < 1.0 {
                return Ok(prev);
            }
        }
        std::thread::sleep(std::time::Duration::from_millis(25));
    }
    Err(format!("device {id} did not switch to {rate} Hz within 500 ms"))
}
