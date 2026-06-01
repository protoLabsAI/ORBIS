import { useEffect } from 'react';
import { useOrbAccent } from './orbAccent';

/**
 * Publishes the orb's current accent color to the `--orb-accent` CSS
 * variable on :root. The `brand` design token resolves to it
 * (`--brand: var(--orb-accent, #f59e0b)`), so every `text-brand` /
 * `bg-brand` / `ring-brand` in the chrome re-tints live as the orb's
 * palette changes — focus rings, active tabs, the reminders dot, etc.
 * Renders nothing.
 */
export function OrbAccentBridge() {
  const accent = useOrbAccent();
  useEffect(() => {
    document.documentElement.style.setProperty('--orb-accent', accent);
  }, [accent]);
  return null;
}
