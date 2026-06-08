import { registerPlugin } from '@/sdk';
import { MicToggle } from './MicToggle';

registerPlugin({
  id: 'mic-toggle',
  order: 20,
  slots: { 'overlay-top': MicToggle },
});
