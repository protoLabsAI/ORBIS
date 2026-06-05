import { Bloom } from '@react-three/postprocessing';
import { useOrbState } from '../../useOrbState';
import type { GeodePreset } from './presets';

/** Per-variant bloom for the geode plasma (rendered in OrbStage's composer). */
export function GeodePost() {
  const { params } = useOrbState();
  const base = params as unknown as GeodePreset;
  return (
    <Bloom
      intensity={base.bloom ?? 1.0}
      luminanceThreshold={0.2}
      luminanceSmoothing={0.7}
      mipmapBlur
    />
  );
}
