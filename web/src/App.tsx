import { Drawer } from '@/components/Drawer';
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

/**
 * Pre-2026-04-28 this was wrapped in PipecatClientProvider +
 * PipecatClientAudio for the WebRTC client. The web/PWA path was
 * dropped (DECISIONS.md amendment of that date) — voice state now
 * arrives via the SSE bridge in VoiceStateBridge, audio I/O happens
 * in the Rust CPAL engine, the React tree just renders.
 */
function App() {
  return (
    <>
      <VoiceStateBridge />
      <LogsCollector />
      <div className="fixed inset-0 overflow-hidden bg-[#0a0a0a]">
        <Slot name="stage" />
        <Slot name="overlay-top" />
        <Slot name="overlay-bottom" />
        <Drawer />
      </div>
    </>
  );
}

export default App;
