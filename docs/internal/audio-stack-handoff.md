# Audio stack handoff — VPIO / output / speaker-AEC (2026-06-06)

Three threads, all tied to **one root cause** found this session. Read this first,
then the linked PRs/branches.

## The root cause (internalize this)

macOS voice processing (`inputNode.setVoiceProcessingEnabled(true)`) builds a
hidden CoreAudio **aggregate** fusing the input + output devices so the AEC has a
playback reference. That aggregate **only forms with built-in / aggregatable
output**. With the built-in mic + a **USB audio interface** (e.g. Focusrite
Scarlett) as the default output, the aggregate's playback side comes up but the
**capture side never renders** → the input tap never fires → the mic is
**silently dead** (engine `isRunning`, output works, permission authorized, no
error). Fingerprint: VPIO input format = 9ch on built-in/HDMI output (works), 5ch
on USB output (dead) — it tracks the output device. Apple DTS confirms VPIO needs
aggregatable devices (dev forums thread 52979). Full story: memory
`reference_vpio_usb_output_aec`.

Consequences: (a) native **speaker-AEC is built-in-output-only**; (b) the shipped
VPIO build had a **latent dead-mic bug** for any USB-output user.

## 1. Dead-mic fix — SHIPPED ✅ (PR #433, `fix/mic-alive-on-usb-output`)

Smart fallback: try VPIO, watchdog the input tap ~4s; if zero callbacks, tear
down VPIO and fall back to **CPAL input + software AEC** (`aec.rs`) so the mic
**always works** (half-duplex, 16× Rust-side gain). Detection is the watchdog,
NOT a transport whitelist (HDMI works, USB doesn't — latency-based).
Verified both paths on-device. **Do not delete `aec.rs`** — it is the USB
fallback. Key: `InputSource` enum, `vpio_active` flag drives runtime
`half_duplex()`.

## 2. Output-device selector — BACKEND DONE, FRONTEND TODO ⬜ (`feat/output-device-selector`)

Lets ORBIS play TTS through a chosen device instead of blindly following the
system default — so a user can point ORBIS at built-in output (for AEC) while
keeping their interface as the system default.

- **Done (backend):** `coreaudio_devices::list_output_device_names` (refactored
  the input enumeration into `devices_with_scope`), `AudioEngine::list_output_devices`,
  `new()` takes `output_device_name`, `list_audio_outputs` + `set_output_device`
  Tauri commands, file-backed `persisted_output_device`, handler registration.
- **TODO (frontend):** pure mirror of the mic picker —
  - `web/src/shared/audio/nativeAudio.ts`: add `listAudioOutputs()` + `setOutputDevice()`.
  - `OutputSettings.tsx` ← mirror `plugins/settings-panel/MicSettings.tsx`; drop it
    into `plugins/settings-panel/VoicePanel.tsx`.
  - A wizard step in `plugins/setup-wizard/SetupWizard.tsx`.

## 3. Unified VPIO output / real speaker-AEC — PARKED 🅿️ (`wip/unified-vpio-output-aec`)

Routes TTS through the SAME AVAudioEngine as the VPIO input (an
`AVAudioSourceNode`) so the AEC gets a real reference → barge-in over SPEAKERS
(today TTS goes out a separate CPAL stream, so the reference is empty and ORBIS
runs half-duplex). Engine starts + TTS routes through it, but it was **never
acoustically validated** — the dead-mic bug (thread 1) masked everything.
Includes the `-10875` fix (conform both devices to 44.1 kHz), a muted-sink
experiment, and `[vp-diag]`/`[vp-rate]` diagnostics to strip. Full "what's left"
is in that branch's commit message.

## Recommended order for the next team

1. Merge **#433** (the mic-robustness floor).
2. Finish **#2's frontend** (mirror the mic picker) → merge. Now users can target
   built-in output.
3. Resume **#3** on built-in output: pin the VPIO aggregate's *output* device to
   the chosen device (`kAudioOutputUnitProperty_CurrentDevice`, element 0) so AEC
   works on built-in regardless of the system default; gate it to
   built-in/aggregatable output only (USB stays on the #433 CPAL fallback);
   acoustic-test (talk over the orb on speakers, confirm it doesn't hear itself);
   strip the diagnostics + the muted-sink experiment.

## Gotcha that cost this session ~2 hours

A fast-rebuild loop (`cargo tauri build` + `pkill orbis-tauri` only) **leaks the
Python sidecars** (pyapp grandchildren) and an aborted device-rate conform left
the mic forced to 44.1 kHz — both masqueraded as "my code broke the mic." When
the mic dies after audio changes, check **device topology + zombie sidecars**
(`pkill -9 -f pyapp/orbis`) BEFORE blaming code. Use the nuke script's full
cleanup.
