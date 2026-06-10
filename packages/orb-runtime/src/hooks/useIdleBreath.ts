import { useRef } from 'react';
import { useFrame } from '@react-three/fiber';
import { BREATH_HZ_1, BREATH_HZ_2 } from '../constants';

/**
 * Idle breath — two non-commensurate low-frequency sines summed for
 * life-without-loops modulation. Output is normalized to ~[-0.75, 0.75].
 * Multiply by BREATH_AMP in the caller for the "fraction of scale" effect.
 */
export function useIdleBreath() {
  const breathNormRef = useRef(0);
  const startMsRef = useRef(performance.now());

  useFrame(() => {
    const tSec = (performance.now() - startMsRef.current) / 1000;
    const breath =
      Math.sin(tSec * Math.PI * 2 * BREATH_HZ_1) +
      0.5 * Math.sin(tSec * Math.PI * 2 * BREATH_HZ_2);
    breathNormRef.current = breath * 0.5;
  });

  return { breathNormRef };
}
