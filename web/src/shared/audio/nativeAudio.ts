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
