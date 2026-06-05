import { Bloom } from '@react-three/postprocessing';
import { useOrbState } from '../../useOrbState';
import type { DiscoPreset } from './presets';

/**
 * Per-variant bloom standing in for the prototype's god-rays. A higher
 * luminance threshold so only the bright neon glints on the facets bloom, not
 * the whole solid ball.
 */
export function DiscoPost() {
  const { params } = useOrbState();
  const base = params as unknown as DiscoPreset;
  return (
    <Bloom
      intensity={base.bloom ?? 1.0}
      luminanceThreshold={0.5}
      luminanceSmoothing={0.6}
      mipmapBlur
    />
  );
}
