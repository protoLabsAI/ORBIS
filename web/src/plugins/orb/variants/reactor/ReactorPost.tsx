import { Bloom } from '@react-three/postprocessing';
import { useOrbState } from '../../useOrbState';
import type { ReactorPreset } from './presets';

/** Per-variant bloom for the reactor fractal (rendered in OrbStage's composer). */
export function ReactorPost() {
  const { params } = useOrbState();
  const base = params as unknown as ReactorPreset;
  return (
    <Bloom
      intensity={base.bloom ?? 1.0}
      luminanceThreshold={0.15}
      luminanceSmoothing={0.7}
      mipmapBlur
    />
  );
}
