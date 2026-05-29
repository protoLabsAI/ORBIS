import { invoke } from '@tauri-apps/api/core';

export type AudioInputMode = 'voice_processing' | 'cpal' | 'unsupported';

export async function getAudioInputMode() {
  return invoke<AudioInputMode>('get_audio_input_mode');
}

export function usesSelectableInputDevice(mode: AudioInputMode) {
  return mode === 'cpal';
}
