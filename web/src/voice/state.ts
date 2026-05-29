/**
 * Derived voice state store — the single source of truth for what the
 * orb, status chip, and any plugin UI reads. Updated by VoiceStateBridge
 * via the SSE event stream from the Python sidecar.
 *
 * Intentionally tiny: useSyncExternalStore gives us identity-stable
 * selectors with no external dep.
 */

export type VoiceState = 'idle' | 'listening' | 'thinking' | 'speaking';

export interface VoiceSnapshot {
  state: VoiceState;
  /** True once the SSE bridge has received its first event from the sidecar. */
  connected: boolean;
  lastUserTranscript: string | null;
  lastBotText: string | null;
  activeToolCall: { name: string; args: unknown } | null;
  delegationProgress: string | null;
  delegationOutcome: 'success' | 'error' | null;
  sessionId: string | null;
  // Rolling counters — handy for plugins to notice "something happened"
  // without holding full event lists.
  epoch: number;
}

const INITIAL: VoiceSnapshot = {
  state: 'idle',
  connected: false,
  lastUserTranscript: null,
  lastBotText: null,
  activeToolCall: null,
  delegationProgress: null,
  delegationOutcome: null,
  sessionId: null,
  epoch: 0,
};

type Listener = () => void;

class VoiceStore {
  private snap: VoiceSnapshot = INITIAL;
  private listeners = new Set<Listener>();

  getSnapshot = (): VoiceSnapshot => this.snap;

  subscribe = (l: Listener): (() => void) => {
    this.listeners.add(l);
    return () => {
      this.listeners.delete(l);
    };
  };

  update(patch: Partial<VoiceSnapshot>): void {
    this.snap = { ...this.snap, ...patch, epoch: this.snap.epoch + 1 };
    this.listeners.forEach((l) => l());
  }

  reset(): void {
    this.snap = { ...INITIAL };
    this.listeners.forEach((l) => l());
  }
}

export const voiceStore = new VoiceStore();
