import { Bloom } from '@react-three/postprocessing';
import { useOrbState } from '../../useOrbState';
import type { EdisonPreset } from './presets';

/**
 * Per-variant bloom for the Edison filaments. Rendered inside OrbStage's
 * EffectComposer (after the shared CA) only while Edison is the active variant,
 * so other variants are untouched. The low luminance threshold lets the bright
 * additive filaments + particles bloom while the dark glass stays crisp.
 */
export function EdisonPost() {
  const { params } = useOrbState();
  const base = params as unknown as EdisonPreset;
  return (
    <Bloom
      intensity={base.bloom ?? 0.9}
      luminanceThreshold={0.2}
      luminanceSmoothing={0.7}
      mipmapBlur
    />
  );
}
