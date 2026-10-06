export type VoiceLifecycleState = 'warming' | 'starting' | 'running' | 'failed';
export type VoiceRecoveryAction = 'retry' | 'relaunch_required';

export interface VoiceLifecycle {
  state: VoiceLifecycleState;
  detail: string;
  code?: string;
  action?: VoiceRecoveryAction;
}

const STATES = new Set<VoiceLifecycleState>([
  'warming',
  'starting',
  'running',
  'failed',
]);

export function parseVoiceLifecycle(value: unknown): VoiceLifecycle | null {
  if (!value || typeof value !== 'object') return null;
  const candidate = value as {
    state?: unknown;
    detail?: unknown;
    code?: unknown;
    action?: unknown;
  };
  if (typeof candidate.state !== 'string'
      || !STATES.has(candidate.state as VoiceLifecycleState)) return null;
  const parsed: VoiceLifecycle = {
    state: candidate.state as VoiceLifecycleState,
    detail: typeof candidate.detail === 'string' ? candidate.detail.trim() : '',
  };
  if (typeof candidate.code === 'string') {
    parsed.code = candidate.code;
  }
  if (candidate.action === 'retry' || candidate.action === 'relaunch_required') {
    parsed.action = candidate.action;
  }
  return parsed;
}

export function voiceIsReady(lifecycle: VoiceLifecycle | null): boolean {
  return lifecycle?.state === 'running';
}

export function voiceLifecycleText(lifecycle: VoiceLifecycle | null): string {
  if (!lifecycle) return 'voice starting…';
  if (lifecycle.state === 'running') return '';
  if (lifecycle.detail) return lifecycle.detail;
  if (lifecycle.state === 'warming') return 'warming voice…';
  if (lifecycle.state === 'failed') return 'voice unavailable';
  return 'starting voice…';
}


/** Pipecat readiness and native callback/IPC health are separate contracts. */
export function effectiveVoiceLifecycle(
  lifecycle: VoiceLifecycle | null,
  audio: { socket_connected: boolean; capture_alive: boolean; detail: string; relaunch_required: boolean } | null,
): VoiceLifecycle | null {
  if (!audio) return lifecycle?.state === 'running'
    ? { state: 'starting', detail: 'Starting native audio…' } : lifecycle;
  if (audio.socket_connected && audio.capture_alive) return lifecycle;
  if (audio.relaunch_required) {
    return { state: 'failed', detail: audio.detail,
      code: 'native_audio_unavailable', action: 'relaunch_required' };
  }
  if (lifecycle?.state !== 'running') return lifecycle;
  return { state: 'starting', detail: audio.detail, code: 'native_audio_unavailable' };
}
