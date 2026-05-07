/**
 * Derived voice state store — the single source of truth for what the
 * orb, status chip, and any plugin UI reads. Updated by VoiceStateBridge
 * as RTVI events flow through the client.
 *
 * Intentionally tiny: useSyncExternalStore gives us identity-stable
 * selectors with no external dep.
 */

export type VoiceState = 'idle' | 'listening' | 'thinking' | 'speaking';

// Mirrors pipecat's DeviceErrorType. Listed here as a string-literal
// union so VoiceSnapshot stays import-free and the banner can render
// type-specific copy without pulling the full pipecat error class.
export type DeviceErrorType =
  | 'permissions'
  | 'not-found'
  | 'in-use'
  | 'constraints'
  | 'undefined-mediadevices'
  | 'unknown';

export interface DeviceErrorInfo {
  type: DeviceErrorType;
  message?: string;
}

export interface VoiceSnapshot {
  state: VoiceState;
  connected: boolean;
  transportState: string;
  lastUserTranscript: string | null;
  lastBotText: string | null;
  activeToolCall: { name: string; args: unknown } | null;
  sessionId: string | null;
  // Latest device-level error from pipecat (mic permission denied, no
  // mic detected, etc.). Persists until the user dismisses the banner
  // or the underlying condition is fixed and a fresh connect succeeds.
  deviceError: DeviceErrorInfo | null;
  // True when the transport is in pipecat's `error` state — typically
  // the WebRTC handshake failed or the data channel dropped. Distinct
  // from a clean user-initiated disconnect (which goes to
  // `disconnected` without flipping this flag).
  connectionError: boolean;
  // Rolling counters — handy for plugins to notice "something happened"
  // without holding full event lists.
  epoch: number;
}

const INITIAL: VoiceSnapshot = {
  state: 'idle',
  connected: false,
  transportState: 'disconnected',
  lastUserTranscript: null,
  lastBotText: null,
  activeToolCall: null,
  sessionId: null,
  deviceError: null,
  connectionError: false,
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
