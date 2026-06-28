/**
 * One-per-session intro. Explains what the demo is and — importantly — that
 * the first tap downloads the on-device models (so the ~730 MB pull isn't a
 * surprise). Gated on sessionStorage so it shows once per tab session.
 */
import { useState } from 'react';
import { Brain, Ear, Volume2 } from 'lucide-react';

const SESSION_KEY = 'orbis-demo-intro-seen';

const MODELS = [
  { Icon: Brain, label: 'Brain', sub: 'Gemma · language model', size: '~500 MB' },
  { Icon: Ear, label: 'Ears', sub: 'Moonshine · speech-to-text', size: '~60 MB' },
  { Icon: Volume2, label: 'Voice', sub: 'Kokoro · text-to-speech', size: '~80 MB' },
];

export function IntroDialog() {
  const [open, setOpen] = useState(() => {
    try {
      return sessionStorage.getItem(SESSION_KEY) !== '1';
    } catch {
      return true;
    }
  });

  if (!open) return null;

  const dismiss = () => {
    try {
      sessionStorage.setItem(SESSION_KEY, '1');
    } catch {
      /* private mode — just close for this view */
    }
    setOpen(false);
  };

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/75 p-6 backdrop-blur-sm"
      onClick={dismiss}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-[min(30rem,92vw)] rounded-2xl border border-white/10 bg-zinc-950/95 p-6 text-zinc-200 shadow-2xl"
      >
        <div className="mb-1 font-mono text-xs uppercase tracking-widest text-indigo-300">
          Browser preview
        </div>
        <h2 className="mb-3 text-xl font-semibold text-white">Meet your first orb</h2>
        <p className="mb-4 text-sm leading-relaxed text-zinc-400">
          A taste of ORBIS running <span className="text-zinc-200">100% on your device</span> — you
          talk, a local model thinks, and it answers out loud. Nothing leaves your machine.
        </p>

        <div className="mb-4 rounded-xl border border-white/5 bg-white/[0.02] p-3">
          <p className="mb-2 text-xs text-zinc-500">
            The first time you tap to talk, three on-device models download once, then cache for
            instant loads after:
          </p>
          <ul className="space-y-2">
            {MODELS.map(({ Icon, label, sub, size }) => (
              <li key={label} className="flex items-center gap-3">
                <span className="grid h-8 w-8 place-items-center rounded-lg bg-indigo-400/10 text-indigo-300">
                  <Icon className="h-4 w-4" />
                </span>
                <span className="flex-1 text-sm">
                  <span className="text-zinc-200">{label}</span>{' '}
                  <span className="text-xs text-zinc-500">· {sub}</span>
                </span>
                <span className="text-xs tabular-nums text-zinc-500">{size}</span>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-[11px] text-zinc-600">
            ~640 MB total · requires WebGPU (Chrome, Edge, or Safari 26+)
          </p>
        </div>

        <button
          onClick={dismiss}
          className="w-full rounded-lg bg-indigo-500/90 px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-indigo-500"
        >
          Got it — let's talk
        </button>
        <a
          href="https://protolabs.studio"
          target="_blank"
          rel="noopener noreferrer"
          className="mt-3 block text-center text-[11px] text-zinc-600 transition-colors hover:text-zinc-400"
        >
          Built by protoLabs.studio ↗
        </a>
      </div>
    </div>
  );
}
