/**
 * Extract a (primary, secondary) color pair from a variant's named
 * palette. Used by the setup wizard cards to render a cheap gradient
 * swatch without mounting the full shader.
 *
 * All four shipped variants use the same primaryEnergy / secondaryEnergy
 * field pair on their preset objects; if a future variant diverges,
 * extend the fallback logic here.
 */

import { variantRegistry } from '@/plugins/orb/variants/registry';

export interface PaletteColors {
  primary: string;
  secondary: string;
}

const FALLBACK: PaletteColors = { primary: '#71717a', secondary: '#09090b' };

export function paletteColors(variantId: string, paletteName: string): PaletteColors {
  const variant = variantRegistry.get(variantId);
  if (!variant) return FALLBACK;
  const preset = variant.palettes[paletteName] as
    | { primaryEnergy?: string; secondaryEnergy?: string }
    | undefined;
  if (!preset) return FALLBACK;
  return {
    primary: preset.primaryEnergy ?? FALLBACK.primary,
    secondary: preset.secondaryEnergy ?? FALLBACK.secondary,
  };
}
