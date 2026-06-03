import { registerPlugin } from '../PluginHost';
import { MicToggle } from './MicToggle';

registerPlugin({
  id: 'mic-toggle',
  slots: { 'overlay-top': MicToggle },
});
