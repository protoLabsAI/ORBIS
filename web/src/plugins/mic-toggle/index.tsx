import { registerPlugin } from '../PluginHost';
import { MicToggle } from './MicToggle';

registerPlugin({
  id: 'mic-toggle',
  order: 20,
  slots: { 'overlay-top': MicToggle },
});
