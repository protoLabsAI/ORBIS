/**
 * Voice-first demo control (a sibling of <App/>, so web/src stays untouched).
 *
 * One affordance: tap to talk. First tap primes mic permission, downloads
 * the on-device models (brain + voice) with a progress label, then listens;
 * after that it's just tap → talk → it replies out loud. Double-clicking the
 * orb does the same (via the app's set_mic_listening). Typing is a thin
 * fallback that runs the identical pipeline and speaks the reply too.
 */
import { useEffect, useState, type FormEvent } from 'react';
import { Mic, Square, Loader2 } from 'lucide-react';
import { voiceEngine } from '../engine/voiceEngine';
import { hasWebGPU } from '../engine/webgpu';
import { useVoiceStateSelector } from '@/voice/hooks';

export function DemoComposer() {
  const [supported] = useState(hasWebGPU);
  const [progress, setProgress] = useState<{ label: string; pct: number | null } | null>(null);
  const [preparing, setPreparing] = useState(false);
  const [text, setText] = useState('');
  const state = useVoiceStateSelector((s) => s.state);
  const listening = state === 'listening';
  const busy = state === 'thinking' || state === 'speaking';

  useEffect(() => {
    voiceEngine.onProgress = (label, pct) => setProgress(pct === 100 ? null : { label, pct });
    return () => {
      voiceEngine.onProgress = null;
    };
  }, []);

  const wrap =
    'fixed bottom-14 left-1/2 -translate-x-1/2 z-50 flex flex-col items-center gap-2 w-[min(34rem,92vw)]';

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

  const tap = async () => {
    if (busy) return;
    setPreparing(true);
    try {
      await voiceEngine.activate();
    } finally {
      setPreparing(false);
      setProgress(null);
    }
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const t = text.trim();
    if (!t || busy || listening) return;
    setText('');
    setPreparing(true);
    try {
      await voiceEngine.submitText(t);
    } finally {
      setPreparing(false);
      setProgress(null);
    }
  };

  const loading = !!progress || (preparing && !listening && !busy);
  const label = progress
    ? `${progress.label}${progress.pct != null ? ` ${Math.round(progress.pct)}%` : ''}`
    : listening
      ? 'Listening — tap to stop'
      : state === 'thinking'
        ? 'Thinking…'
        : state === 'speaking'
          ? 'Speaking…'
          : preparing
            ? 'Preparing…'
            : 'Tap to talk';

  const Icon = loading || busy ? Loader2 : listening ? Square : Mic;

  return (
    <div className={wrap}>
      <button
        onClick={tap}
        disabled={busy}
        aria-label={label}
        className={[
          'flex items-center gap-2 rounded-full px-5 py-2.5 text-sm font-medium backdrop-blur transition-colors',
          listening
            ? 'border border-rose-400/50 bg-rose-500/15 text-rose-100'
            : 'border border-indigo-400/40 bg-indigo-400/10 text-indigo-100 hover:border-indigo-300/70',
          busy ? 'opacity-80' : '',
        ].join(' ')}
      >
        <Icon className={`h-4 w-4 ${loading || busy ? 'animate-spin' : ''}`} />
        {label}
      </button>

      <form onSubmit={submit} className="w-full">
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={busy || listening}
          placeholder="…or type to Orbis"
          className="w-full rounded-full border border-white/10 bg-black/50 px-4 py-2 text-center text-xs text-white placeholder:text-zinc-600 backdrop-blur outline-none focus:border-indigo-400/50 disabled:opacity-50"
        />
      </form>
      <p className="text-[11px] text-zinc-600">
        {loading
          ? 'one-time download — cached after this, then instant'
          : 'on-device · nothing leaves your machine · or double-click the orb'}
      </p>
    </div>
  );
}
