import { useEffect } from 'react';
import { useOrbAccent } from './orbAccent';

/**
 * Publishes the orb's current accent color to the `--orb-accent` CSS
 * variable on :root. The `orb` design token resolves to it
 * (`--orb: var(--orb-accent, var(--brand))`), so `bg-orb` / `text-orb`
 * re-tint live as the orb's palette changes. The chrome accent itself
 * (`brand`) is the fixed protoLabs lavender; only the orb-tinted
 * reminders dot reads `orb` today. Renders nothing.
 */
export function OrbAccentBridge() {
  const accent = useOrbAccent();
  useEffect(() => {
    document.documentElement.style.setProperty('--orb-accent', accent);
  }, [accent]);
  return null;
}
