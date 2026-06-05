/**
 * Side-effect imports — every variant module calls registerVariant()
 * at module top-level, so importing this file is what wires built-in
 * variants into the registry.
 *
 * Free vs paid is NOT decided here (every variant registers). Free access is
 * the curated starter pool in config/starter_orbs.yaml, which only draws on
 * fractal / nebula / crystal / particles. Every other variant is marked
 * `premium: true` and reached only through the paid customization editor.
 */

// Free-tier variants — the four the starter pool draws on.
import './fractal';
import './nebula';
import './crystal';
import './particles';

// Premium (paid) variants — `premium: true`, never in the free starter pool,
// gated by the customization paywall in the editor/picker.
import './tetra';
import './lattice';
import './spectrum';
import './galaxy';
import './edison';
import './reactor';
import './flux';
import './geode';
