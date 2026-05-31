import { useOrbState } from '@/plugins/orb/useOrbState';

/**
 * Derive a single representative accent color from the orb's current
 * params so chrome (e.g. the reminders notification dot) can echo the
 * orb. Most variants expose `primaryEnergy` as a hex string; a few
 * (spectrum) drive color procedurally with no single hex, so we scan
 * a preference list, then any hex-looking param, then fall back.
 */

const HEX = /^#([0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})$/i;
const PREFERRED = ['primaryEnergy', 'colorBright', 'colorMid', 'secondaryEnergy'];
const FALLBACK = '#c084fc';

export function orbAccentFromParams(params: Record<string, unknown>): string {
  for (const key of PREFERRED) {
    const v = params[key];
    if (typeof v === 'string' && HEX.test(v)) return v;
  }
  for (const v of Object.values(params)) {
    if (typeof v === 'string' && HEX.test(v)) return v;
  }
  return FALLBACK;
}

/** The orb's current accent color, reactive to palette/variant changes. */
export function useOrbAccent(): string {
  const { params } = useOrbState();
  return orbAccentFromParams(params);
}
