import { useEffect, useRef, useState } from 'react';
import { Mic, MicOff, X } from 'lucide-react';
import { usePipecatClient, usePipecatClientMediaTrack } from '@pipecat-ai/client-react';
import { useVoiceStateSelector } from './hooks';
import type { VoiceState } from './state';

/**
 * Compact UI rendered inside the floating Document PiP window so
 * users can keep the orb visible while another window is focused.
 *
 * Lives inside the same React tree as the main app via createPortal
 * (see PiPHost), so it reads the same voiceStore + uses the same
 * pipecat client hooks. The PiP window has its own document but
 * shares JS context — the Provider above the portal is the same one
 * App.tsx mounts.
 *
 * Surfaces:
 *   - orb mood ring (color tracks voice state)
 *   - the active tool name when delegating ("asking ava…")
 *   - live VU meter off the local audio track via WebAudio AnalyserNode
 *   - a single disconnect button — the most useful thing to do from a
 *     floating window is ending the session without alt-tabbing back
 */
export function PiPOverlay() {
  const voiceState = useVoiceStateSelector((s) => s.state);
  const activeToolCall = useVoiceStateSelector((s) => s.activeToolCall);
  const client = usePipecatClient();
  const localTrack = usePipecatClientMediaTrack('audio', 'local');
  const level = useMicLevel(localTrack);

  return (
    <div className="h-full w-full flex flex-col items-center justify-center bg-[#0a0a0a] text-zinc-200 p-4 gap-3">
      <MoodRing voiceState={voiceState} level={level} />

      <div className="text-[11px] uppercase tracking-wider text-zinc-500 text-center">
        {labelForState(voiceState, activeToolCall)}
      </div>

      <div className="w-full">
        <LevelBar value={level} state={voiceState} />
      </div>

      <button
        type="button"
        onClick={() => client?.disconnect()}
        className="mt-1 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-zinc-900 hover:bg-zinc-800 border border-zinc-800 text-xs text-zinc-300"
      >
        <X className="h-3 w-3" />
        Disconnect
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Mood ring — circular indicator whose color tracks voice state
// ---------------------------------------------------------------------------

const STATE_COLOR: Record<VoiceState, string> = {
  idle: '#71717a',          // zinc-500
  listening: '#f59e0b',     // amber-500
  thinking: '#a855f7',      // purple-500
  speaking: '#10b981',      // emerald-500
};

function MoodRing({ voiceState, level }: { voiceState: VoiceState; level: number }) {
  // Mic level → ring scale. Caps at ~1.15× so loud transients don't
  // shove the ring off-screen at small PiP sizes.
  const scale = 1 + Math.min(0.15, level * 1.5);
  const color = STATE_COLOR[voiceState];
  const isMuted = voiceState === 'idle';
  return (
    <div
      className="rounded-full grid place-items-center transition-transform duration-75"
      style={{
        width: 96,
        height: 96,
        background: `radial-gradient(circle, ${color}22 0%, ${color}05 60%, transparent 100%)`,
        boxShadow: `0 0 32px 4px ${color}40`,
        transform: `scale(${scale})`,
      }}
    >
      <div
        className="rounded-full grid place-items-center"
        style={{
          width: 56,
          height: 56,
          background: '#0a0a0a',
          border: `1px solid ${color}80`,
        }}
      >
        {isMuted ? (
          <MicOff className="h-5 w-5" style={{ color }} />
        ) : (
          <Mic className="h-5 w-5" style={{ color }} />
        )}
      </div>
    </div>
  );
}

function LevelBar({ value, state }: { value: number; state: VoiceState }) {
  // Same gamma curve as MicTest so the meter looks consistent
  // wherever it surfaces in the app.
  const pct = Math.min(100, Math.round(Math.sqrt(Math.min(1, value * 6)) * 100));
  const color = STATE_COLOR[state];
  return (
    <div className="h-1 rounded-full bg-zinc-900 overflow-hidden">
      <div
        className="h-full transition-[width] duration-75"
        style={{ width: `${pct}%`, background: color }}
      />
    </div>
  );
}

function labelForState(
  state: VoiceState,
  activeToolCall: { name: string; args: unknown } | null,
): string {
  if (activeToolCall?.name === 'delegate_to') {
    const target = readTarget(activeToolCall.args);
    return target ? `asking ${target}…` : 'delegating…';
  }
  return state;
}

function readTarget(args: unknown): string | null {
  if (typeof args !== 'object' || args === null) return null;
  const t = (args as { target?: unknown }).target;
  return typeof t === 'string' && t.length > 0 ? t : null;
}

// ---------------------------------------------------------------------------
// Mic-level analyser — same shape as MicTest's, scoped here for reuse
// ---------------------------------------------------------------------------

/**
 * Subscribe to the local audio track's RMS level. Returns a value
 * between 0 and ~0.5 in normal speech. ``null`` track = 0.
 *
 * Lifecycle care: AudioContext + AnalyserNode are torn down on track
 * change so we don't leak audio graph nodes when the user reconnects
 * with a different mic mid-session.
 */
function useMicLevel(track: MediaStreamTrack | null): number {
  const [level, setLevel] = useState(0);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    if (!track) {
      setLevel(0);
      return;
    }
    const stream = new MediaStream([track]);
    const ctx = new AudioContext();
    const source = ctx.createMediaStreamSource(stream);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    source.connect(analyser);
    const samples = new Uint8Array(analyser.fftSize);

    const tick = () => {
      analyser.getByteTimeDomainData(samples);
      let sum = 0;
      for (let i = 0; i < samples.length; i += 1) {
        const v = (samples[i] - 128) / 128;
        sum += v * v;
      }
      setLevel(Math.sqrt(sum / samples.length));
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);

    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
      source.disconnect();
      void ctx.close();
    };
  }, [track]);

  return level;
}
