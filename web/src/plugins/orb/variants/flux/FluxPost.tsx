import { Bloom } from '@react-three/postprocessing';
import { useOrbState } from '../../useOrbState';
import type { FluxPreset } from './presets';

/** Per-variant bloom for the flux glow (rendered in OrbStage's composer). */
export function FluxPost() {
  const { params } = useOrbState();
  const base = params as unknown as FluxPreset;
  return (
    <Bloom
      intensity={base.bloom ?? 1.0}
      luminanceThreshold={0.15}
      luminanceSmoothing={0.7}
      mipmapBlur
    />
  );
}
