/**
 * localStorage wrappers for orb presets. Keys match the vanilla
 * static/index.html contract so existing user data carries over.
 */

export const STORAGE_PARAMS  = 'protoVoice.params';
export const STORAGE_PALETTE = 'protoVoice.palette';
export const STORAGE_CUSTOM  = 'protoVoice.customPresets';

export type CustomPresetPayload = {
  palette: string;
  params: Record<string, unknown>;
};

export type CustomPresetMap = Record<string, CustomPresetPayload>;

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

export function loadCustom(): CustomPresetMap {
  try { return safeJSON(localStorage.getItem(STORAGE_CUSTOM), {} as CustomPresetMap); }
  catch { return {}; }
}
export function saveCustom(m: CustomPresetMap): void {
  try { localStorage.setItem(STORAGE_CUSTOM, JSON.stringify(m)); } catch {}
}
