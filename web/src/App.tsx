import { useRef } from 'react';
import type { PipecatClient } from '@pipecat-ai/client-js';
import {
  PipecatClientProvider,
  PipecatClientAudio,
} from '@pipecat-ai/client-react';
import { Drawer } from '@/components/Drawer';
import { buildClient } from './voice/client';
import { VoiceStateBridge } from './voice/VoiceStateBridge';
import { Slot } from './plugins/PluginHost';
// Side-effect imports — each plugin registers at module load.
import './plugins/orb';
import './plugins/status-pill';
import './plugins/orb-settings';
import './plugins/voice-panel';
import './plugins/setup-wizard';
import './plugins/mood';

function App() {
  const clientRef = useRef<PipecatClient | null>(null);
  if (!clientRef.current) clientRef.current = buildClient();

  return (
    <PipecatClientProvider client={clientRef.current}>
      <VoiceStateBridge />
      <div className="fixed inset-0 overflow-hidden bg-[#0a0a0a]">
        <Slot name="stage" />
        <Slot name="overlay-top" />
        <Slot name="overlay-bottom" />
        <Drawer />
      </div>
      <PipecatClientAudio />
    </PipecatClientProvider>
  );
}

export default App;
