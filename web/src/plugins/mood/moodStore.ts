/**
 * Mood state store — mirror of the server's ``/api/personality.mood``.
 *
 * Polled every 30 seconds by ``moodDriver`` so the orb can visually
 * reflect valence / arousal / guardedness without needing the
 * personality panel to be mounted.
 *
 * Variants subscribe to this store and translate the three dimensions
 * to shader uniforms however fits their aesthetic — there's no single
 * universal mapping across fractal / nebula / crystal / particles.
 * The authoring editor (task follow-up to this one) will let creators
 * author those mappings visually.
 */

export interface Mood {
  valence: number;        // -1 (low)       +1 (bright)
  arousal: number;        // -1 (sleepy)    +1 (energetic)
  guardedness: number;    //  0 (open)      +1 (guarded)
  updatedAt: string;
}

const NEUTRAL: Mood = {
  valence: 0, arousal: 0, guardedness: 0, updatedAt: '',
};

type Listener = () => void;

let _mood: Mood = NEUTRAL;
const _listeners = new Set<Listener>();

export const moodStore = {
  get: (): Mood => _mood,

  set: (next: Mood) => {
    // Skip notify when nothing meaningfully changed (moves <0.02).
    const prev = _mood;
    if (
      Math.abs(prev.valence - next.valence) < 0.02 &&
      Math.abs(prev.arousal - next.arousal) < 0.02 &&
      Math.abs(prev.guardedness - next.guardedness) < 0.02
    ) {
      return;
    }
    _mood = next;
    _listeners.forEach((l) => l());
  },

  subscribe: (l: Listener): (() => void) => {
    _listeners.add(l);
    return () => {
      _listeners.delete(l);
    };
  },
};
