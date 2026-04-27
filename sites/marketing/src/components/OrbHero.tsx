import { useState, useEffect } from 'react';
import { OrbStandaloneCanvas } from '../orb/OrbStandaloneCanvas';
import type { VoiceState } from '../orb/shared/stateSnapshot';

const STATES: VoiceState[] = ['idle', 'listening', 'thinking', 'speaking'];

export function OrbHero() {
  const [voiceState, setVoiceState] = useState<VoiceState>('idle');
  const [size, setSize] = useState(480);

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

  useEffect(() => {
    const update = () => setSize(window.innerWidth < 640 ? 320 : 480);
    update();
    window.addEventListener('resize', update);
    return () => window.removeEventListener('resize', update);
  }, []);

  // Sync active state back to the static pills rendered in Astro
  useEffect(() => {
    document.querySelectorAll<HTMLButtonElement>('[data-state-pill]').forEach((btn) => {
      const active = btn.dataset.statePill === voiceState;
      btn.setAttribute('data-active', String(active));
    });
  }, [voiceState]);

  return <OrbStandaloneCanvas voiceState={voiceState} size={size} />;
}
