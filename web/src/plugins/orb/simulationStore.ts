/**
 * Rendering-only override for authoring previews.
 *
 * When the user is editing `state_overrides.idle` in the Orb panel, we
 * want the live orb to show what idle *will* look like with that
 * delta applied — without waiting for the voice pipeline to actually
 * be idle, and without the real moodStore value bleeding through.
 *
 * This store holds optional overrides for `voiceState` and `mood`.
 * When set, variant renderers use them instead of their usual
 * sources. When null, rendering falls back to live values.
 *
 * Kept out of orbStore because the simulation is UI-ephemeral — it
 * never persists, never saves, never touches /api/config.
 */

import type { VoiceState } from '../../voice/state';

export interface Mood {
  valence: number;
  arousal: number;
  guardedness: number;
}

export interface SimulationSnapshot {
  pinnedState: VoiceState | null;
  pinnedMood: Mood | null;
  epoch: number;
}

const INITIAL: SimulationSnapshot = {
  pinnedState: null,
  pinnedMood: null,
  epoch: 0,
};

type Listener = () => void;

class SimulationStore {
  private snap: SimulationSnapshot = INITIAL;
  private listeners = new Set<Listener>();

  getSnapshot = (): SimulationSnapshot => this.snap;

  subscribe = (l: Listener): (() => void) => {
    this.listeners.add(l);
    return () => { this.listeners.delete(l); };
  };

  /** Replace the pinned state. Pass null to release to live voice. */
  setPinnedState(state: VoiceState | null): void {
    if (this.snap.pinnedState === state) return;
    this.snap = { ...this.snap, pinnedState: state, epoch: this.snap.epoch + 1 };
    this.listeners.forEach((l) => l());
  }

  /** Replace the pinned mood. Pass null to release to live mood.
   * Skips notify when the incoming mood matches the current pin to
   * avoid waking every subscriber on a no-op pin. */
  setPinnedMood(mood: Mood | null): void {
    if (moodEquals(this.snap.pinnedMood, mood)) return;
    this.snap = { ...this.snap, pinnedMood: mood, epoch: this.snap.epoch + 1 };
    this.listeners.forEach((l) => l());
  }

  /** Clear both pins at once — e.g. when the authoring panel unmounts
   * or the user toggles Simulate off. No-op when already cleared, and
   * epoch keeps ticking forward so update-counter consumers don't
   * observe a non-monotonic jump back to 0. */
  clear(): void {
    if (this.snap.pinnedState === null && this.snap.pinnedMood === null) return;
    this.snap = {
      pinnedState: null,
      pinnedMood: null,
      epoch: this.snap.epoch + 1,
    };
    this.listeners.forEach((l) => l());
  }
}

export const simulationStore = new SimulationStore();

function moodEquals(a: Mood | null, b: Mood | null): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  return a.valence === b.valence
      && a.arousal === b.arousal
      && a.guardedness === b.guardedness;
}
