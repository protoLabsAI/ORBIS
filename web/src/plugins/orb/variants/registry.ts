import type { ComponentType } from 'react';
import type { FieldSpec } from '../shared/field-types';
import type { VoiceState } from '../../../voice/state';
import type { MoodOverrides } from '../compose';
import { createRegistry } from '@/lib/createRegistry';

/** Shared props every variant receives from the OrbStage / OrbPreview. */
export interface VariantProps {
  voiceState: VoiceState;
  botStream?: MediaStream | null;
  localStream?: MediaStream | null;
}

/**
 * Variant registry. Each orb variant registers itself at module import
 * with a unique id, a React component, its own field schema (consumed
 * by the settings panel) and default params.
 *
 * Adding a variant:
 *   1. create plugins/orb/variants/<id>/index.tsx
 *   2. call registerVariant({...}) at module top-level
 * That's it — variants/index.ts auto-discovers the folder (Vite eager glob).
 */

export interface VariantSpec {
  id: string;
  name: string;
  description?: string;
  Component: ComponentType<VariantProps>;
  /** Per-variant palettes keyed by name. */
  palettes: Record<string, Record<string, unknown>>;
  /** The canonical settings schema for this variant. */
  fields: FieldSpec[];
  /** Default palette to use when a user first picks this variant. */
  defaultPalette: string;
  /**
   * Per-dimension mood→uniform deltas baked into the variant. Fed
   * through composeBase as the fallback when the user hasn't authored
   * their own mood overrides via the Customize panel; user-authored
   * deltas merge over these per-key (user wins). Optional — variants
   * without this still react via the user-authored override path.
   */
  moodDefaults?: MoodOverrides;
  /** Paid/premium variant — gated behind the customization unlock + beta flag. */
  premium?: boolean;
  /**
   * Optional postprocessing rendered inside OrbStage's EffectComposer while
   * this variant is active (e.g. a bloom pass). Other variants are untouched.
   */
  PostEffects?: ComponentType;
}

export const variantRegistry = createRegistry<VariantSpec>();

export const registerVariant = (spec: VariantSpec): void => {
  variantRegistry.register(spec);
};
