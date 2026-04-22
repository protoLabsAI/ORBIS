import { useEffect, useState } from 'react';

/**
 * Reactive `(pointer: coarse)` matcher. True on touch-first devices
 * (phones, tablets, touch laptops). Drives:
 *   - single-tap-to-connect on the orb (vs double-click on mouse)
 *   - copy for the status-pill hint
 *   - anywhere else the UX should change for touch
 */
export function useCoarsePointer(): boolean {
  const [coarse, setCoarse] = useState(() => {
    if (typeof window === 'undefined') return false;
    return window.matchMedia('(pointer: coarse)').matches;
  });

  useEffect(() => {
    const mq = window.matchMedia('(pointer: coarse)');
    const handler = (e: MediaQueryListEvent) => setCoarse(e.matches);
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  }, []);

  return coarse;
}
