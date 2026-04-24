import { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';

type Status = 'idle' | 'requesting' | 'listening' | 'denied' | 'error';

// The level-meter RMS cutoff that counts as "yes, actual voice reached the
// mic." Tuned against silent-room noise floor (~0.01) vs. normal speaking
// volume (0.1+). Once the meter crosses this once we flag the mic verified
// so the parent can enable its Continue button.
const VERIFIED_RMS = 0.04;

export interface MicTestProps {
  /** Fired the first time a voice-level sample clears VERIFIED_RMS. */
  onVerified?: () => void;
  /** Optional device id from enumerateDevices — used by the settings panel. */
  deviceId?: string;
}

/**
 * Inline microphone test: requests input permission, renders a live
 * level meter off an AnalyserNode, and reports when real voice has been
 * observed. Used by the setup wizard and the settings-panel audio tab.
 *
 * Designed as a user-gesture gate for `getUserMedia()` — Safari/WKWebView
 * only prompt for mic access from a direct click handler, so the wizard
 * must render this before any auto-started WebRTC session.
 */
export function MicTest({ onVerified, deviceId }: MicTestProps) {
  const [status, setStatus] = useState<Status>('idle');
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [level, setLevel] = useState(0);
  const [verified, setVerified] = useState(false);

  // Refs for teardown — we never re-render on these; they're side-effect
  // handles whose identity matters for cleanup, not for UI.
  const streamRef = useRef<MediaStream | null>(null);
  const ctxRef = useRef<AudioContext | null>(null);
  const rafRef = useRef<number | null>(null);
  const verifiedRef = useRef(false);

  const stop = useCallback(() => {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    if (streamRef.current) {
      for (const track of streamRef.current.getTracks()) track.stop();
      streamRef.current = null;
    }
    if (ctxRef.current && ctxRef.current.state !== 'closed') {
      void ctxRef.current.close();
    }
    ctxRef.current = null;
  }, []);

  useEffect(() => () => stop(), [stop]);

  const start = useCallback(async () => {
    setStatus('requesting');
    setErrorMessage(null);
    setLevel(0);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: deviceId ? { deviceId: { exact: deviceId } } : true,
      });
      streamRef.current = stream;

      const ctx = new AudioContext();
      ctxRef.current = ctx;
      const source = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      source.connect(analyser);

      const samples = new Uint8Array(analyser.fftSize);
      const tick = () => {
        analyser.getByteTimeDomainData(samples);
        // RMS over the centered waveform (bytes are 0-255 around 128).
        let sum = 0;
        for (let i = 0; i < samples.length; i += 1) {
          const v = (samples[i] - 128) / 128;
          sum += v * v;
        }
        const rms = Math.sqrt(sum / samples.length);
        setLevel(rms);
        if (!verifiedRef.current && rms >= VERIFIED_RMS) {
          verifiedRef.current = true;
          setVerified(true);
          onVerified?.();
        }
        rafRef.current = requestAnimationFrame(tick);
      };
      rafRef.current = requestAnimationFrame(tick);
      setStatus('listening');
    } catch (e) {
      const err = e as DOMException;
      if (err?.name === 'NotAllowedError' || err?.name === 'SecurityError') {
        setStatus('denied');
      } else {
        setStatus('error');
        setErrorMessage(err?.message || String(e));
      }
    }
  }, [deviceId, onVerified]);

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-4 space-y-3">
      {status === 'idle' && (
        <div className="flex items-center justify-between gap-3">
          <div className="text-sm text-zinc-400">
            Click to request microphone access.
          </div>
          <Button onClick={start}>Test microphone</Button>
        </div>
      )}

      {status === 'requesting' && (
        <div className="text-sm text-zinc-400">
          Waiting for permission — look for the system prompt.
        </div>
      )}

      {status === 'listening' && (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <div className="text-xs uppercase tracking-wider text-zinc-500">
              {verified ? 'Verified' : 'Say something…'}
            </div>
            <div className="text-[10px] text-zinc-600 tabular-nums">
              {(level * 100).toFixed(0)}%
            </div>
          </div>
          <LevelMeter value={level} verified={verified} />
          <div className="flex justify-end">
            <Button variant="ghost" onClick={stop}>Stop</Button>
          </div>
        </div>
      )}

      {status === 'denied' && (
        <div className="space-y-3">
          <div className="text-sm text-rose-400">
            Microphone access was denied.
          </div>
          <div className="text-xs text-zinc-500 leading-relaxed">
            Open <span className="text-zinc-300">System Settings → Privacy
            &amp; Security → Microphone</span> and enable ORBIS, then retry.
            On first-run macOS may not prompt at all if the app hasn't yet
            been granted TCC access.
          </div>
          <Button onClick={start}>Retry</Button>
        </div>
      )}

      {status === 'error' && (
        <div className="space-y-3">
          <div className="text-sm text-amber-400">
            Couldn't access the microphone.
          </div>
          {errorMessage && (
            <div className="text-xs text-zinc-500 font-mono break-all">
              {errorMessage}
            </div>
          )}
          <Button onClick={start}>Retry</Button>
        </div>
      )}
    </div>
  );
}

function LevelMeter({ value, verified }: { value: number; verified: boolean }) {
  // Clamp + gamma-ish curve so speech-level RMS (~0.1) fills a visible
  // portion without capping instantly on loud transients.
  const pct = Math.min(100, Math.round(Math.sqrt(Math.min(1, value * 6)) * 100));
  return (
    <div className="h-2 rounded-full bg-zinc-800 overflow-hidden">
      <div
        className={
          'h-full transition-[width] duration-75 ' +
          (verified ? 'bg-emerald-500/80' : 'bg-amber-500/80')
        }
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}
