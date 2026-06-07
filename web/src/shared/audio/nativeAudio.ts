import { invoke } from '@tauri-apps/api/core';

export type AudioInputMode = 'voice_processing' | 'cpal' | 'unsupported';

export async function getAudioInputMode() {
  return invoke<AudioInputMode>('get_audio_input_mode');
}

export function usesSelectableInputDevice(mode: AudioInputMode) {
  // Both native paths support device selection now: CPAL picks by name; the
  // voice-processing path pins the Core Audio device id on its input node
  // (orbis-zj5).
  return mode === 'cpal' || mode === 'voice_processing';
}

/** Persist the chosen input device on the Rust side (applied at engine
 *  construction — i.e. the next launch). */
export async function setInputDevice(name: string) {
  return invoke('set_input_device', { name });
}

/** Bring the native audio engine online after the user grants mic permission.
 *  The engine start is deferred at boot until permission exists (so first run
 *  shows no TCC dialog over the splash), so the wizard / settings panel call
 *  this once the grant lands. Idempotent on the Rust side; best-effort here —
 *  resolves false (never throws) on non-native builds or if the command is
 *  absent, since the live level meter is the real confirmation. */
export async function startAudioEngine(): Promise<boolean> {
  try {
    return await invoke<boolean>('start_audio_engine');
  } catch {
    return false;
  }
}
