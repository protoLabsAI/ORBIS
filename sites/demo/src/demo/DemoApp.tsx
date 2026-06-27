/**
 * Lightweight "my first orb" shell — the demo's own chrome instead of the
 * full app Drawer (delegates / wake-word / brain / personality settings are
 * gutted; they don't belong in a browser taste). Still the REAL orb engine
 * + voice bridge, so the orb looks and reacts exactly like the app — just
 * wrapped in a minimal surface: orb, a transcript line, the tap-to-talk
 * control, and one small settings panel.
 */
import { useEffect } from 'react';
import { useVoiceBridge } from '@/voice/useVoiceBridge';
import { OrbStage } from '@/plugins/orb/OrbStage';
import { loadOrbOverrides } from '@/plugins/orb/configDriver';
import { useVoiceStateSelector } from '@/voice/hooks';
import { DemoComposer } from '../components/DemoComposer';
import { DemoPanel } from '../components/DemoPanel';

function Transcript() {
  const state = useVoiceStateSelector((s) => s.state);
  const userText = useVoiceStateSelector((s) => s.lastUserTranscript);
  const botText = useVoiceStateSelector((s) => s.lastBotText);
  return (
    <div className="pointer-events-none fixed inset-x-0 top-[15%] z-40 flex flex-col items-center gap-2 px-6 text-center">
      {userText && <p className="text-sm text-zinc-500">“{userText}”</p>}
      {botText && (state === 'speaking' || state === 'idle') && (
        <p className="max-w-xl text-lg font-medium leading-snug text-zinc-100">{botText}</p>
      )}
    </div>
  );
}

export function DemoApp() {
  useVoiceBridge(); // orb + transcript react to the in-browser engine's events
  useEffect(() => {
    void loadOrbOverrides(); // seed orb variant/palette from the canned config
  }, []);

  return (
    <div className="fixed inset-0 overflow-hidden" style={{ background: '#000' }}>
      <OrbStage />
      <Transcript />
      <DemoComposer />
      <DemoPanel />
    </div>
  );
}
