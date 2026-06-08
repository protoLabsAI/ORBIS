import { Drawer } from '@/components/Drawer';
import { BootStatus } from '@/components/BootStatus';
import { IntroSplash } from '@/components/IntroSplash';
import { UpdateNotice } from '@/components/UpdateNotice';
import { TitleBar } from '@/components/TitleBar';
import { VoiceStateBridge } from './voice/VoiceStateBridge';
import { Slot } from './plugins/PluginHost';
import { LogsCollector } from './plugins/logs-panel';
import { OrbAccentBridge } from './plugins/orb/OrbAccentBridge';
import { WidgetDock } from './widgets/WidgetDock';
import { AmbientBridge } from './widgets/AmbientBridge';
// Auto-discovery (Vite eager glob): every plugins/<name>/index.tsx and
// widgets/<name>/index.tsx self-registers on import. Drop a folder in — no
// edit here, no central import list to maintain.
import './plugins';
import './widgets';

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
      <OrbAccentBridge />
      <AmbientBridge />
      <LogsCollector />
      <div className="fixed inset-0 overflow-hidden bg-surface">
        <Slot name="stage" />
        <Slot name="overlay-top" />
        <Slot name="overlay-bottom" />
        <WidgetDock />
        <Drawer />
        <TitleBar />
      </div>
      <UpdateNotice />
      <BootStatus />
      <IntroSplash />
    </>
  );
}

export default App;
