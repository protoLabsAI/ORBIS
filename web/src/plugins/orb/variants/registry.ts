import type { ComponentType } from 'react';
import type { FieldSpec } from '../shared/field-types';
import type { VoiceState } from '../../../voice/state';
import type { MoodOverrides } from '../compose';

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

type Listener = () => void;

class VariantRegistry {
  private byId = new Map<string, VariantSpec>();
  private cachedAll: ReadonlyArray<VariantSpec> = [];
  private listeners = new Set<Listener>();

  register(spec: VariantSpec): void {
    this.byId.set(spec.id, spec);
    this.cachedAll = Array.from(this.byId.values());
    this.listeners.forEach((l) => l());
  }

  get(id: string): VariantSpec | undefined {
    return this.byId.get(id);
  }

  /**
   * Cached — returns the same reference across calls until register() is
   * next invoked. useSyncExternalStore compares by Object.is; returning a
   * fresh `Array.from(...)` every call was triggering a render loop.
   */
  all = (): ReadonlyArray<VariantSpec> => this.cachedAll;

  subscribe = (l: Listener): (() => void) => {
    this.listeners.add(l);
    return () => {
      this.listeners.delete(l);
    };
  };
}

export const variantRegistry = new VariantRegistry();
export const registerVariant = (spec: VariantSpec) => variantRegistry.register(spec);
