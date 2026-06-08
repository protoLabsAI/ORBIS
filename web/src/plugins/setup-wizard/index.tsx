import { registerPlugin } from '../PluginHost';
import { SetupWizard } from './SetupWizard';

// The wizard lives in its own top-level overlay slot rather than a
// drawer panel — it needs to own the whole viewport on first run.
registerPlugin({
  id: 'setup-wizard',
  order: 30,
  slots: { 'overlay-top': SetupWizard },
});
