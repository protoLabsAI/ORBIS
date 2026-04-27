import { useState, useEffect } from 'react';
import { OrbStandaloneCanvas } from '../orb/OrbStandaloneCanvas';
import type { VoiceState } from '../orb/shared/stateSnapshot';

const STATES: VoiceState[] = ['idle', 'listening', 'thinking', 'speaking'];

export function OrbHero() {
  const [voiceState, setVoiceState] = useState<VoiceState>('idle');
  const [size, setSize] = useState(480);

  // Respond to demo pill button events dispatched from Astro page script
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<{ state: VoiceState }>).detail;
      if (detail?.state && STATES.includes(detail.state)) {
        setVoiceState(detail.state);
      }
    };
    window.addEventListener('orbis:voiceState', handler);
    return () => window.removeEventListener('orbis:voiceState', handler);
  }, []);

  // Responsive size
  useEffect(() => {
    const update = () => setSize(window.innerWidth < 640 ? 320 : 480);
    update();
    window.addEventListener('resize', update);
    return () => window.removeEventListener('resize', update);
  }, []);

  return (
    <div className="flex flex-col items-center gap-6">
      <OrbStandaloneCanvas voiceState={voiceState} size={size} />

      {/* Demo state toggles */}
      <div className="flex gap-2 flex-wrap justify-center">
        {STATES.map((s) => (
          <button
            key={s}
            onClick={() => setVoiceState(s)}
            className={[
              'px-4 py-1.5 rounded-full text-sm font-medium border transition-all duration-200',
              voiceState === s
                ? 'border-sky-500/60 bg-sky-500/15 text-sky-400'
                : 'border-white/10 bg-white/[0.03] text-zinc-400 hover:border-white/20 hover:text-zinc-300',
            ].join(' ')}
          >
            {s.charAt(0).toUpperCase() + s.slice(1)}
          </button>
        ))}
      </div>
    </div>
  );
}
