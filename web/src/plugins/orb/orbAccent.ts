import { useOrbState } from '@/plugins/orb/useOrbState';

/**
 * Derive a single representative accent color from the orb's current
 * params. Most variants expose `primaryEnergy` as a hex string; a few
 * (spectrum) drive color procedurally with no single hex, so we scan a
 * preference list, then any hex-looking param, then fall back.
 *
 * This is the single source of truth for the app's accent: the reminders
 * dot reads it directly, and `OrbAccentBridge` publishes it to the
 * `--orb-accent` CSS variable so all `brand`-tokened chrome echoes the orb.
 */

const HEX = /^#([0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})$/i;
const PREFERRED = ['primaryEnergy', 'colorBright', 'colorMid', 'secondaryEnergy'];
const FALLBACK = '#f59e0b'; // amber-500 — matches --orb-accent default

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
