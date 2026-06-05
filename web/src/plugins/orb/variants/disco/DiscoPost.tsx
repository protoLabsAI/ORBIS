import { Bloom } from '@react-three/postprocessing';
import { useOrbState } from '../../useOrbState';
import type { DiscoPreset } from './presets';

/** Per-variant bloom for the disco fractal (rendered in OrbStage's composer). */
export function DiscoPost() {
  const { params } = useOrbState();
  const base = params as unknown as DiscoPreset;
  return (
    <Bloom
      intensity={base.bloom ?? 1.0}
      luminanceThreshold={0.15}
      luminanceSmoothing={0.7}
      mipmapBlur
    />
  );
}
