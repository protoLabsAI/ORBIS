/**
 * localStorage wrappers for orb presets. Keys match the vanilla
 * static/index.html contract so existing user data carries over.
 *
 * Custom-preset storage moved to a per-variant shape in v2 — saving
 * "MyFavorite" while on Tetra used to make it appear in the dropdown
 * after switching to Nebula even though the params don't apply to
 * Nebula's schema. ``loadCustomByVariant`` / ``saveCustomByVariant``
 * are the new entry points; ``loadCustom`` / ``saveCustom`` stay for
 * read-only backward compat (returns the legacy flat map) but new
 * code should not write through them.
 */

export const STORAGE_PARAMS         = 'orbis.params';
export const STORAGE_PALETTE        = 'orbis.palette';
export const STORAGE_CUSTOM         = 'orbis.customPresets';        // legacy v1 (flat)
export const STORAGE_CUSTOM_V2      = 'orbis.customPresets.v2';     // {variantId: {name: payload}}
export const STORAGE_CUSTOM_V1_BAK  = 'orbis.customPresets.v1.bak'; // backup after migration

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

/** @deprecated v1 flat map. Use loadCustomByVariant. Kept for tests
 * + backward-read compat — returns the legacy entries that haven't
 * been migrated. */
export function loadCustom(): CustomPresetMap {
  try { return safeJSON(localStorage.getItem(STORAGE_CUSTOM), {} as CustomPresetMap); }
  catch { return {}; }
}
/** @deprecated v1 flat map writer. Use saveCustomByVariant. */
export function saveCustom(m: CustomPresetMap): void {
  try { localStorage.setItem(STORAGE_CUSTOM, JSON.stringify(m)); } catch {}
}

/** Load the saved-preset map for a single variant. Performs a
 * one-time migration from the legacy v1 flat map: the first time
 * this runs (no v2 key in storage), any v1 entries are moved under
 * the requested variant id and v1 is backed up to a separate key.
 *
 * Migrating under "the variant that called loadCustomByVariant first"
 * is imperfect — the user might have saved them while on a different
 * variant — but since the names are user-chosen and the params get
 * filtered through ``Object.hasOwn`` at compose time anyway, the
 * worst case is that the user renames or deletes a few entries. The
 * v1 backup at ``orbis.customPresets.v1.bak`` lets a power user
 * recover entries by variant if they care to. */
export function loadCustomByVariant(variantId: string): CustomPresetMap {
  const all = loadAllCustomByVariant(variantId);
  return all[variantId] ?? {};
}

/** Save the full per-variant map for a single variant. Other
 * variants' entries are preserved (the on-disk shape is the
 * variant-keyed top-level map). */
export function saveCustomByVariant(variantId: string, map: CustomPresetMap): void {
  const all = loadAllCustomByVariantRaw();
  all[variantId] = map;
  try { localStorage.setItem(STORAGE_CUSTOM_V2, JSON.stringify(all)); } catch {}
}

function loadAllCustomByVariantRaw(): CustomPresetMapByVariant {
  try {
    return safeJSON(
      localStorage.getItem(STORAGE_CUSTOM_V2),
      {} as CustomPresetMapByVariant,
    );
  } catch { return {}; }
}

function loadAllCustomByVariant(activeVariantId: string): CustomPresetMapByVariant {
  // Fast path: v2 already exists.
  let raw: string | null = null;
  try { raw = localStorage.getItem(STORAGE_CUSTOM_V2); } catch { raw = null; }
  if (raw !== null) {
    return safeJSON(raw, {} as CustomPresetMapByVariant);
  }
  // Migration path: lift v1 entries (if any) under the active variant.
  const v1: CustomPresetMap = loadCustom();
  const migrated: CustomPresetMapByVariant = {};
  if (Object.keys(v1).length > 0) {
    migrated[activeVariantId] = v1;
    try {
      localStorage.setItem(STORAGE_CUSTOM_V2, JSON.stringify(migrated));
      // Move v1 to a backup key — preserves data without keeping
      // duplicate writes alive at the legacy location.
      localStorage.setItem(STORAGE_CUSTOM_V1_BAK, localStorage.getItem(STORAGE_CUSTOM) ?? '{}');
      localStorage.removeItem(STORAGE_CUSTOM);
    } catch {}
  } else {
    // Empty v1 → seed an empty v2 so we don't re-run migration.
    try { localStorage.setItem(STORAGE_CUSTOM_V2, JSON.stringify(migrated)); } catch {}
  }
  return migrated;
}
