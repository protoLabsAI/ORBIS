import { registerPlugin } from '../PluginHost';
import { OrbStage } from './OrbStage';

registerPlugin({
  id: 'orb',
  slots: { stage: OrbStage },
});
