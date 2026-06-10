// Moved to @orbis/orb-runtime (shared with the orb editor). Re-export
// shim so existing imports keep working. The composition math itself
// now lives in packages/orb-runtime/src/compose.ts.
export { composeBase } from '@orbis/orb-runtime';
export type { Mood, ParamMap, StateOverrides, MoodOverrides } from '@orbis/orb-runtime';
