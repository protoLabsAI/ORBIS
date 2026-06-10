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
 * Free vs paid is NOT decided here (every variant registers). Free access is
 * the curated starter pool in config/starter_orbs.yaml — which only draws on
 * fractal / nebula / crystal / particles. Every other variant sets
 * `premium: true` and is reached only through the paid customization editor.
 */
import.meta.glob('./*/index.tsx', { eager: true });

// Data-driven `.orbis` definitions register through the same registry —
// bundled ones load here; user-imported ones arrive via the orbs catalog.
import '../definitions';
