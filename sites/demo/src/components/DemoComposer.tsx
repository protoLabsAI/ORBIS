/**
 * Demo-only composer overlay.
 *
 * The native app is voice-first with no text input; until PR3 brings mic +
 * STT, this slim bottom bar is how you talk to the in-browser Gemma. It's a
 * sibling of <App/> (not part of web/src), so the mirrored app stays
 * untouched. Gates on WebGPU, lazy-loads the model with a progress bar, then
 * exposes a single-line composer.
 */
import { useState, type FormEvent } from 'react';
import { gemmaEngine } from '../engine/gemmaEngine';
import { hasWebGPU } from '../engine/webgpu';
import { useVoiceStateSelector } from '@/voice/hooks';

type Phase = 'idle' | 'loading' | 'ready';

export function DemoComposer() {
  const [supported] = useState(hasWebGPU);
  const [phase, setPhase] = useState<Phase>('idle');
  const [status, setStatus] = useState('');
  const [pct, setPct] = useState<number | null>(null);
  const [text, setText] = useState('');
  const botState = useVoiceStateSelector((s) => s.state);
  const busy = botState === 'thinking' || botState === 'speaking';

  const wrap = 'fixed bottom-16 left-1/2 -translate-x-1/2 z-50 w-[min(34rem,90vw)]';

  if (!supported) {
    return (
      <div className={`${wrap} text-center text-sm text-zinc-400`}>
        <span className="rounded-full border border-white/10 bg-black/60 px-4 py-2 backdrop-blur">
          This browser doesn't support WebGPU —{' '}
          <a href="/download" className="text-indigo-300 underline">
            download ORBIS for Mac →
          </a>
        </span>
      </div>
    );
  }

  const startLoad = async () => {
    setPhase('loading');
    try {
      await gemmaEngine.load((s, p) => {
        setStatus(s);
        setPct(p);
      });
      setPhase('ready');
    } catch {
      // status carries the error text
    }
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const t = text.trim();
    if (!t || phase !== 'ready' || busy) return;
    setText('');
    await gemmaEngine.send(t);
  };

  return (
    <div className={wrap}>
      {phase !== 'ready' ? (
        <button
          onClick={startLoad}
          disabled={phase === 'loading'}
          className="w-full rounded-full border border-indigo-400/40 bg-indigo-400/10 px-5 py-2.5 text-sm font-medium text-indigo-100 backdrop-blur transition-colors hover:border-indigo-300/70 disabled:opacity-80"
        >
          {phase === 'loading'
            ? `${status}${pct != null ? ` ${Math.round(pct)}%` : ''}`
            : 'Load Gemma on-device to chat (~500 MB, one-time)'}
        </button>
      ) : (
        <form onSubmit={submit}>
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            disabled={busy}
            autoFocus
            placeholder={busy ? 'Orbis is responding…' : 'Type to talk to Orbis…'}
            className="w-full rounded-full border border-white/10 bg-black/60 px-5 py-2.5 text-sm text-white placeholder:text-zinc-500 backdrop-blur outline-none focus:border-indigo-400/60 disabled:opacity-60"
          />
        </form>
      )}
    </div>
  );
}
