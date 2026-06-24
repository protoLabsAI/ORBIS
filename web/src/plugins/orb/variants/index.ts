/**
 * Variant auto-discovery.
 *
 * Every `variants/<id>/index.tsx` calls registerVariant() at module
 * top-level. This eager glob compiles to static side-effect imports, so
 * importing this file registers every built-in variant.
 *
 * Adding a variant is: drop a `variants/<id>/index.tsx` that calls
 * registerVariant(...). No edit here.
 *
 * Nothing is gated here — every variant registers unconditionally. The first-run
 * starter pool is curated in config/starter_orbs.yaml (it draws on fractal /
 * nebula / crystal / particles); the other variants carry `premium: true` purely
 * as "not a default starter" metadata. All of them are free and fully usable via
 * the orb editor + `.orbis` import.
 */
import.meta.glob('./*/index.tsx', { eager: true });

// Data-driven `.orbis` definitions register through the same registry —
// bundled ones load here; user-imported ones arrive via the orbs catalog.
import '../definitions';
