import { invoke } from '@tauri-apps/api/core';

export type MicrophonePermissionStatus =
  | 'not_determined'
  | 'restricted'
  | 'denied'
  | 'authorized'
  | 'unsupported';

export function isMicrophoneAuthorized(status: MicrophonePermissionStatus) {
  return status === 'authorized';
}

export function canRequestMicrophone(status: MicrophonePermissionStatus) {
  return status === 'not_determined';
}

export function needsMicrophoneSettings(status: MicrophonePermissionStatus) {
  return status === 'denied' || status === 'restricted';
}

export async function getMicrophonePermissionStatus() {
  return invoke<MicrophonePermissionStatus>('get_microphone_permission_status');
}

export async function requestMicrophonePermission() {
  return invoke<MicrophonePermissionStatus>('request_microphone_permission');
}

export async function openMicrophoneSettings() {
  return invoke<void>('open_microphone_settings');
}
