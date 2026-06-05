import { Drawer } from '@/components/Drawer';
import { BootStatus } from '@/components/BootStatus';
import { IntroSplash } from '@/components/IntroSplash';
import { TitleBar } from '@/components/TitleBar';
import { VoiceStateBridge } from './voice/VoiceStateBridge';
import { Slot } from './plugins/PluginHost';
import { LogsCollector } from './plugins/logs-panel';
import { OrbAccentBridge } from './plugins/orb/OrbAccentBridge';
import { WidgetDock } from './widgets/WidgetDock';
import { AmbientBridge } from './widgets/AmbientBridge';
// Side-effect imports — each plugin registers at module load.
import './plugins/orb';
import './plugins/status-pill';
import './plugins/orb-settings';
import './plugins/quick-panel';
import './plugins/settings-panel';
import './plugins/reminders-bell';
import './plugins/mic-toggle';
import './plugins/setup-wizard';
import './plugins/mood';
import './plugins/dev-panel';
// Widget runtime — registers the built-in widgets + the launcher.
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
      <BootStatus />
      <IntroSplash />
    </>
  );
}

export default App;
