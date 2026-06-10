/**
 * Core shared types for the orb runtime. Self-contained — the package
 * never imports from `web/src`, so hosts (app, editor) interop via
 * structural typing. `VoiceState` here is intentionally identical to
 * `web/src/voice/state.ts`'s union.
 */

export type VoiceState = 'idle' | 'listening' | 'thinking' | 'speaking';

export interface Mood {
  valence: number;      // [-1, +1]
  arousal: number;      // [-1, +1]
  guardedness: number;  // [ 0, +1] in the server contract; we still scale by it
}

export type ParamMap = Record<string, number | string | boolean | undefined>;

export type StateOverrides = Partial<Record<VoiceState, ParamMap>>;
export type MoodOverrides = Partial<
  Record<'valence' | 'arousal' | 'guardedness', ParamMap>
>;

/**
 * Settings-panel field schema. Shared by hand-written variant `schema.ts`
 * files, `.orbis` definitions, and the generic settings form.
 */
export type SectionId = 'color' | 'energy' | 'motion' | 'fractal' | 'perf';

export type ColorField = {
  kind: 'color';
  key: string;
  label: string;
  section: SectionId;
};

export type SliderField = {
  kind: 'slider';
  key: string;
  label: string;
  section: SectionId;
  min: number;
  max: number;
  step: number;
};

export type FieldSpec = ColorField | SliderField;
