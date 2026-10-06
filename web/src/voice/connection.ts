import { voiceStore, type VoiceSnapshot } from './state';

/** Connection signals are Rust-owned; listener installation is not readiness. */
export function applyConnectionSignal(event: string): boolean {
  if (event === '__connected') {
    voiceStore.update({ connected: true });
    return true;
  }
  if (event !== '__disconnected' && event !== '__backend_lost') return false;
  // SSE carries presentation state; its failure alone does not stop native
  // capture or close the Rust listening gate. Preserve those native truths.
  voiceStore.update({ connected: false, state: 'idle',
    activeToolCall: null, delegationProgress: null });
  if (event === '__backend_lost') {
    voiceStore.update({ micListening: false, activation: null, sessionId: null, voiceLifecycle: { state: 'failed',
      detail: 'Voice service stopped; relaunch ORBIS', code: 'backend_lost', action: 'relaunch_required' } });
  }
  return true;
}


export function applyNativeAudioStatus(status: NonNullable<VoiceSnapshot['nativeAudio']>): void {
  voiceStore.update({ nativeAudio: status });
  if (!status.socket_connected || !status.capture_alive) {
    voiceStore.update({ micListening: false, activation: null, state: 'idle' });
  }
}


/** Reconcile independently retained detector and native transport snapshots. */
export function createNativeWakeCoordinator() {
  let retainedWake: { state?: string; phrase?: string } | null = null;
  let nativeLossSeen = false;
  const applyWake = (payload: { state?: string; phrase?: string }, source: 'event' | 'snapshot' = 'event') => {
    if (source === 'snapshot' && nativeLossSeen && payload.state === 'listening') return;
    retainedWake = payload;
    const s = payload?.state;
    const audio = voiceStore.getSnapshot().nativeAudio;
    const ready = audio?.socket_connected && audio?.capture_alive;
    const activation = s === 'starting' || s === 'failed'
      ? s : ready && (s === 'armed' || s === 'listening') ? s : null;
    voiceStore.update({ activation, wakePhrase: payload?.phrase ?? null });
  };
  return {
    applyWake,
    applyRetainedWake: (payload: { state?: string; phrase?: string }) => applyWake(payload, 'snapshot'),
    applyAudio(status: NonNullable<VoiceSnapshot['nativeAudio']>) {
      applyNativeAudioStatus(status);
      if (!status.socket_connected || !status.capture_alive) {
        nativeLossSeen = true;
      } else if (retainedWake && (retainedWake.state !== 'listening' || !nativeLossSeen)) {
        applyWake(retainedWake);
      }
    },
  };
}
