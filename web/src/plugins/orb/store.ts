/**
 * Central orb state store. Holds the active variant id, its current
 * params, and the name of the palette the current params derive from.
 *
 * Replaces the old `orbInstance` singleton class. Variant components
 * subscribe via useSyncExternalStore; the settings panel writes via
 * the broadcast helpers (applyParam / applyPreset / setVariant).
 *
 * Hydration:
 *   - variantId: localStorage `protoVoice.orb.variant`, default 'fractal'
 *   - palette:   localStorage `protoVoice.palette`, default variant.defaultPalette
 *   - params:    localStorage `protoVoice.params` layered on the palette
 */

import { variantRegistry } from './variants/registry';
import {
  loadCustom,
  loadPalette,
  loadParams,
  savePalette,
  saveParams,
} from './storage';

const STORAGE_VARIANT = 'protoVoice.orb.variant';

export interface OrbStateSnapshot {
  variantId: string;
  palette: string;
  params: Record<string, unknown>;
  epoch: number;
}

type Listener = () => void;

function loadVariantId(): string {
  try {
    return localStorage.getItem(STORAGE_VARIANT) ?? 'fractal';
  } catch {
    return 'fractal';
  }
}

function saveVariantId(id: string): void {
  try { localStorage.setItem(STORAGE_VARIANT, id); } catch {}
}

class OrbStore {
  private snap: OrbStateSnapshot;
  private listeners = new Set<Listener>();

  constructor() {
    const variantId = loadVariantId();
    const variant = variantRegistry.get(variantId) ?? variantRegistry.all()[0];
    const saved = loadPalette();
    // Guarantee a non-empty string — Radix Select flips controlled ⇄
    // uncontrolled when value swaps undefined/non-empty. Fall back to the
    // variant's default, then a hardcoded 'Aurora' if the registry is empty.
    const palette =
      (saved && saved.length > 0 ? saved : variant?.defaultPalette) || 'Aurora';
    const paletteParams = (variant?.palettes?.[palette] ?? {}) as Record<string, unknown>;
    const savedParams = loadParams() ?? {};
    this.snap = {
      variantId: variant?.id ?? variantId,
      palette,
      params: { ...paletteParams, ...savedParams },
      epoch: 0,
    };
  }

  getSnapshot = (): OrbStateSnapshot => this.snap;

  subscribe = (l: Listener): (() => void) => {
    this.listeners.add(l);
    return () => {
      this.listeners.delete(l);
    };
  };

  /** Set a single param. Fires listeners and persists to localStorage. */
  setParam(key: string, value: unknown): void {
    const nextParams = { ...this.snap.params, [key]: value };
    this.snap = { ...this.snap, params: nextParams, epoch: this.snap.epoch + 1 };
    this.listeners.forEach((l) => l());
    saveParams(nextParams as Record<string, unknown>);
  }

  /** Switch palette — overwrites params with the palette's defaults. */
  setPreset(paletteName: string): void {
    const variant = variantRegistry.get(this.snap.variantId);
    const paletteParams = (variant?.palettes?.[paletteName] ?? {}) as Record<string, unknown>;
    this.snap = {
      ...this.snap,
      palette: paletteName,
      params: { ...paletteParams },
      epoch: this.snap.epoch + 1,
    };
    this.listeners.forEach((l) => l());
    savePalette(paletteName);
    saveParams(this.snap.params);
  }

  /** Switch variant. Loads the variant's default palette + any saved params. */
  setVariant(id: string): void {
    const variant = variantRegistry.get(id);
    if (!variant) return;
    const palette = variant.defaultPalette;
    const paletteParams = (variant.palettes[palette] ?? {}) as Record<string, unknown>;
    this.snap = {
      variantId: id,
      palette,
      params: { ...paletteParams },
      epoch: this.snap.epoch + 1,
    };
    this.listeners.forEach((l) => l());
    saveVariantId(id);
    savePalette(palette);
    saveParams(this.snap.params);
  }

  /** Restore a saved custom preset (palette + params snapshot). */
  loadCustomPreset(name: string): void {
    const customs = loadCustom();
    const payload = customs[name];
    if (!payload) return;
    const nextPalette = payload.palette || this.snap.palette;
    this.snap = {
      ...this.snap,
      palette: nextPalette,
      params: { ...(payload.params as Record<string, unknown>) },
      epoch: this.snap.epoch + 1,
    };
    this.listeners.forEach((l) => l());
    if (nextPalette) savePalette(nextPalette);
    saveParams(this.snap.params);
  }
}

/**
 * Lazy singleton — instantiated on first access so variant modules
 * can finish registering before we try to read the default variant.
 */
let _store: OrbStore | null = null;
export const orbStore = {
  get: (): OrbStore => {
    if (!_store) _store = new OrbStore();
    return _store;
  },
  getSnapshot: (): OrbStateSnapshot => {
    if (!_store) _store = new OrbStore();
    return _store.getSnapshot();
  },
  subscribe: (l: Listener): (() => void) => {
    if (!_store) _store = new OrbStore();
    return _store.subscribe(l);
  },
};
