import { applyParam, applyPreset, setVariant } from './broadcast';
import { variantRegistry } from './variants/registry';
import type { SkillViz } from '@/lib/api';

/**
 * Apply a skill's dedicated orb viz. Called on skill change (POST
 * /api/skills succeeds) and on initial page load for the active skill.
 *
 * Order matters:
 *   1. variant — changing variant wipes params to that variant's palette defaults
 *   2. palette — re-seeds params from the palette within the chosen variant
 *   3. params  — individual overrides on top
 *
 * When ``viz`` is empty / null / undefined, no-op — the user's current
 * orb state stays intact. When ``viz.variant`` names a variant the
 * client hasn't registered (typo or skill referencing a plugin that
 * isn't loaded), we log a warning and skip. When ``viz.palette`` is
 * not known for the chosen variant, same deal — skip the preset set
 * but still apply the params.
 */
export function applySkillViz(viz: SkillViz | null | undefined): void {
  if (!viz || (!viz.variant && !viz.palette && !viz.params)) return;

  if (viz.variant) {
    const spec = variantRegistry.get(viz.variant);
    if (!spec) {
      console.warn(
        `[skill-viz] unknown variant "${viz.variant}"; skipping variant + palette changes`,
      );
    } else {
      setVariant(viz.variant);
    }
  }

  if (viz.palette) {
    // The variant lookup above ensures the active variant is the one
    // this skill targets, so the palette lookup resolves correctly.
    const activeVariant = viz.variant
      ? variantRegistry.get(viz.variant)
      : null;
    const palettes = activeVariant?.palettes ?? {};
    if (viz.variant && !palettes[viz.palette]) {
      console.warn(
        `[skill-viz] variant ${viz.variant} has no palette ${viz.palette}; skipping palette change`,
      );
    } else {
      applyPreset(viz.palette);
    }
  }

  if (viz.params) {
    for (const [k, v] of Object.entries(viz.params)) {
      applyParam(k, v);
    }
  }
}
