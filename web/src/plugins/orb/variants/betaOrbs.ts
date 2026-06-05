/**
 * Premium / experimental orb variants register only when this flag is on, so
 * they don't ship in regular beta builds. On in dev; opt in elsewhere with
 * `VITE_BETA_ORBS=1`. The customization paywall still gates them in the picker
 * (`VariantSpec.premium` → entitlement) once they're registered.
 */
export const BETA_ORBS: boolean =
  import.meta.env.DEV ||
  (import.meta.env as Record<string, string | undefined>).VITE_BETA_ORBS === '1';
