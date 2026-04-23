/**
 * Mood plugin — polls server-side personality state into moodStore.
 *
 * No UI slot contributions. Side-effect import from App.tsx starts
 * the poller; variants subscribe via ``useMood()`` if they want to
 * reflect mood in their shader uniforms.
 *
 * The full state + mood authoring editor (where creators wire moods
 * to uniform deltas per variant) is a follow-up — see DECISIONS.md
 * amendment.
 */

import { registerPlugin } from '../PluginHost';
import { startMoodDriver } from './moodDriver';

startMoodDriver();

registerPlugin({
  id: 'mood',
  slots: {},
});

export { useMood } from './useMood';
export { moodStore } from './moodStore';
export type { Mood } from './moodStore';
