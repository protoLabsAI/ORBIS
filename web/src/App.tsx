import { useRef } from 'react';
import type { PipecatClient } from '@pipecat-ai/client-js';
import {
  PipecatClientProvider,
  PipecatClientAudio,
} from '@pipecat-ai/client-react';
import { Drawer } from '@/components/Drawer';
import { buildClient } from './voice/client';
import { ConnectionBanner } from './voice/ConnectionBanner';
import { DelegateHealthBanner } from './voice/DelegateHealthBanner';
import { VoiceStateBridge } from './voice/VoiceStateBridge';
import { Slot } from './plugins/PluginHost';
import { LogsCollector } from './plugins/logs-panel';
// Side-effect imports — each plugin registers at module load.
import './plugins/orb';
import './plugins/status-pill';
import './plugins/orb-settings';
import './plugins/settings-panel';
import './plugins/setup-wizard';
import './plugins/mood';
import './plugins/dev-panel';
// logs-panel doesn't register a slot — it's imported for the
// LogsCollector side-effect mount and the LogsPanel re-export used
// by DevPanel.
import './plugins/logs-panel';

function App() {
  const clientRef = useRef<PipecatClient | null>(null);
  if (!clientRef.current) clientRef.current = buildClient();

  return (
    <PipecatClientProvider client={clientRef.current}>
      <VoiceStateBridge />
      {/* Mounted unconditionally — the log buffer captures events even
          when the Dev mode is off so flipping it on shows recent
          history. Cheap (event subscriptions only). */}
      <LogsCollector />
      <div className="fixed inset-0 overflow-hidden bg-[#0a0a0a]">
        <Slot name="stage" />
        <Slot name="overlay-top" />
        <Slot name="overlay-bottom" />
        <Drawer />
        {/* Surfaces mic-permission and connection-error states the
            user otherwise sees as silent stalls. Mounted after the
            drawer so it sits above other overlays in z-stack. */}
        <ConnectionBanner />
        {/* Lower-priority sibling — flags configured delegates whose
            background probes have failed repeatedly so the user
            knows before they try to delegate. Sits below the
            connection banner in z-stack. */}
        <DelegateHealthBanner />
      </div>
      <PipecatClientAudio />
    </PipecatClientProvider>
  );
}

export default App;
