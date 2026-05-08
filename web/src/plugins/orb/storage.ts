/**
 * localStorage wrappers for orb presets.
 *
 * Custom presets are keyed per-variant — saving "MyFavorite" while on
 * Tetra used to make it appear in the dropdown after switching to
 * Nebula even though the params don't apply to Nebula's schema.
 * ``loadCustomByVariant`` / ``saveCustomByVariant`` are the entry
 * points; the on-disk shape under ``orbis.customPresets.v2`` is
 * ``Record<variantId, Record<presetName, payload>>``.
 *
 * (The pre-launch ``orbis.customPresets`` flat-map shape was dropped
 * outright since ORBIS hasn't shipped to anyone with custom presets
 * worth migrating; if you have a stale entry from local development
 * sitting in localStorage, it's now ignored.)
 */

export const STORAGE_PARAMS    = 'orbis.params';
export const STORAGE_PALETTE   = 'orbis.palette';
export const STORAGE_CUSTOM_V2 = 'orbis.customPresets.v2';

export type CustomPresetPayload = {
  palette: string;
  params: Record<string, unknown>;
};

export type CustomPresetMap = Record<string, CustomPresetPayload>;
export type CustomPresetMapByVariant = Record<string, CustomPresetMap>;

function safeJSON<T>(raw: string | null, fallback: T): T {
  if (!raw) return fallback;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

export function loadPalette(): string | null {
  try { return localStorage.getItem(STORAGE_PALETTE); } catch { return null; }
}
export function savePalette(name: string): void {
  try { localStorage.setItem(STORAGE_PALETTE, name); } catch {}
}
export function clearParams(): void {
  try { localStorage.removeItem(STORAGE_PARAMS); } catch {}
}

export function loadParams(): Record<string, unknown> | null {
  try { return safeJSON(localStorage.getItem(STORAGE_PARAMS), null as Record<string, unknown> | null); }
  catch { return null; }
}
export function saveParams(p: Record<string, unknown>): void {
  try { localStorage.setItem(STORAGE_PARAMS, JSON.stringify(p)); } catch {}
}

/** Load the saved-preset map for a single variant. Returns an empty
 * map if the variant has no entries yet. */
export function loadCustomByVariant(variantId: string): CustomPresetMap {
  return loadAllByVariant()[variantId] ?? {};
}

/** Save the per-variant preset map. Other variants' entries are
 * preserved — the on-disk shape is the full variant-keyed map. */
export function saveCustomByVariant(variantId: string, map: CustomPresetMap): void {
  const all = loadAllByVariant();
  all[variantId] = map;
  try { localStorage.setItem(STORAGE_CUSTOM_V2, JSON.stringify(all)); } catch {}
}

function loadAllByVariant(): CustomPresetMapByVariant {
  try {
    return safeJSON(
      localStorage.getItem(STORAGE_CUSTOM_V2),
      {} as CustomPresetMapByVariant,
    );
  } catch { return {}; }
}
