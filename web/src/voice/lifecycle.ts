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
