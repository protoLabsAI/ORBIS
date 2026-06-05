/**
 * Side-effect imports — every variant module calls registerVariant()
 * at module top-level, so importing this file is what wires built-in
 * variants into the registry.
 */
import './fractal';
import './nebula';
import './crystal';
import './particles';
import './tetra';
import './lattice';
import './spectrum';
import './galaxy';
// Premium / beta-gated variants (register behind BETA_ORBS + the paywall).
import './edison';
import './reactor';
import './disco';
import './flux';
