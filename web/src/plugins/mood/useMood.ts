import { useSyncExternalStore } from 'react';
import { moodStore, type Mood } from './moodStore';

/** React hook subscribing to the polled mood store. */
export function useMood(): Mood {
  return useSyncExternalStore(
    moodStore.subscribe,
    moodStore.get,
    moodStore.get,
  );
}
