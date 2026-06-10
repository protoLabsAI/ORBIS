// Moved to @orbis/orb-runtime (shared with the orb editor). Re-export
// shim so existing variant imports keep working.
export {
  stateSnapshot,
  lerpSnapshot,
  coerceBasePreset,
} from '@orbis/orb-runtime';
export type { StateSnapshot, OrbBasePreset } from '@orbis/orb-runtime';
