/**
 * Bundled `.orbis` definitions — data-driven orbs that ship with the
 * app and render through @orbis/orb-runtime's raymarch-v1 engine
 * instead of a hand-written component. Drop a `<name>.orbis.json`
 * in this folder; no edit here (same auto-discovery contract as
 * variants/index.ts).
 *
 * Runtime-imported user orbs follow the same path via
 * registerDefinition() — these bundled files are also the living
 * examples of the format (docs/internal/orb-format-and-editor.md).
 */
import { validateOrbDefinition } from '@orbis/orb-runtime';
import { registerDefinition } from './host';

const bundled = import.meta.glob('./*.orbis.json', { eager: true, import: 'default' });

for (const [path, raw] of Object.entries(bundled)) {
  const res = validateOrbDefinition(raw);
  if (res.ok) {
    registerDefinition(res.def);
  } else {
    // A broken bundled definition is a build bug, not a user error —
    // surface loudly in dev, skip in prod rather than blanking the stage.
    console.error(`[orb] bundled definition ${path} rejected:`, res.errors);
  }
}
