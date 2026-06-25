/**
 * Central orb state store. Holds the active variant id, its current
 * params, and the name of the palette the current params derive from.
 *
 * Replaces the old `orbInstance` singleton class. Variant components
 * subscribe via useSyncExternalStore; the settings panel writes via
 * the broadcast helpers (applyParam / applyPreset / setVariant).
 *
 * Hydration:
 *   - variantId: localStorage `orbis.orb.variant`, default 'fractal'
 *   - palette:   localStorage `orbis.palette`, default variant.defaultPalette
 *   - params:    localStorage `orbis.params` layered on the palette
 */

import { variantRegistry } from './variants/registry';
import {
  loadCustomByVariant,
  loadPalette,
  loadParams,
  savePalette,
  saveParams,
} from './storage';
import type { MoodOverrides, StateOverrides } from './compose';

const STORAGE_VARIANT = 'orbis.orb.variant';

export interface OrbStateSnapshot {
  variantId: string;
  palette: string;
  params: Record<string, unknown>;
  // Authoring deltas applied on top of `params` by the composition
  // layer at uniform-set time. Empty maps compose to no-op.
  stateOverrides: StateOverrides;
  moodOverrides: MoodOverrides;
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
  /** A selection whose variant isn't registered (yet). Runtime-imported
   * orbs land async (sidecar catalog fetch), so both boot hydration and
   * configDriver's server push can name an id the registry doesn't have
   * at that instant. Remember the intent; apply on registration. */
  private pending: { variantId: string; palette?: string } | null = null;

  constructor() {
    const requestedId = loadVariantId();
    const variant = variantRegistry.get(requestedId) ?? variantRegistry.all()[0];
    const saved = loadPalette();
    // Guarantee a non-empty string — Radix Select flips controlled ⇄
    // uncontrolled when value swaps undefined/non-empty. Fall back to the
    // variant's default, then a hardcoded 'Aurora' if the registry is empty.
    const palette =
      (saved && saved.length > 0 ? saved : variant?.defaultPalette) || 'Aurora';
    const paletteParams = (variant?.palettes?.[palette] ?? {}) as Record<string, unknown>;
    const savedParams = loadParams() ?? {};
    this.snap = {
      variantId: variant?.id ?? requestedId,
      palette,
      params: { ...paletteParams, ...savedParams },
      stateOverrides: {},
      moodOverrides: {},
      epoch: 0,
    };
    // The persisted pick may be an imported orb that registers later —
    // render the fallback for now, restore when the catalog lands.
    if (!variantRegistry.get(requestedId)) {
      this.pending = { variantId: requestedId, palette: saved ?? undefined };
    }
    variantRegistry.subscribe(() => this.tryApplyPending());
  }

  /** Apply a remembered selection once its variant registers. */
  private tryApplyPending(): void {
    const pending = this.pending;
    if (!pending) return;
    const variant = variantRegistry.get(pending.variantId);
    if (!variant) return;
    this.pending = null;
    const palette =
      pending.palette && variant.palettes[pending.palette]
        ? pending.palette
        : variant.defaultPalette;
    const paletteParams = (variant.palettes[palette] ?? {}) as Record<string, unknown>;
    this.snap = {
      ...this.snap,
      variantId: variant.id,
      palette,
      params: { ...paletteParams },
      epoch: this.snap.epoch + 1,
    };
    this.listeners.forEach((l) => l());
    saveVariantId(variant.id);
    savePalette(palette);
    saveParams(this.snap.params);
  }

  /** Replace the authoring override maps — called by the drawer or by
   * the hydrate-from-server step on first mount. Fires listeners. */
  setOverrides(stateOverrides: StateOverrides, moodOverrides: MoodOverrides): void {
    this.snap = {
      ...this.snap,
      stateOverrides,
      moodOverrides,
      epoch: this.snap.epoch + 1,
    };
    this.listeners.forEach((l) => l());
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

  /** Layer persisted params over the current snapshot. Called by the
   * config-hydration step, which runs AFTER setVariant/setPreset have
   * already reset params to the palette defaults — so this restores the
   * user's saved edits on top, mirroring the constructor's
   * `{ ...paletteParams, ...savedParams }` precedence (a saved value
   * wins over the palette default for the same key). Does NOT round-trip
   * to the server (the caller is the server hydration); writes through
   * to localStorage for parity. */
  setParams(saved: Record<string, unknown>): void {
    const nextParams = { ...this.snap.params, ...saved };
    this.snap = { ...this.snap, params: nextParams, epoch: this.snap.epoch + 1 };
    this.listeners.forEach((l) => l());
    saveParams(nextParams);
  }

  /** Switch palette — overwrites params with the palette's defaults. */
  setPreset(paletteName: string): void {
    if (this.pending) {
      // A palette arriving for a not-yet-registered variant (configDriver
      // pushes variant then palette) belongs to the pending selection —
      // applying it against the fallback variant would wipe params.
      this.pending.palette = paletteName;
      savePalette(paletteName);
      return;
    }
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

  /** Switch variant. Loads the variant's default palette + any saved
   * params. Authoring overrides are KEPT across the swap — `composeBase`
   * silently drops keys that don't exist on the new variant, so
   * same-named uniforms compose and everything else no-ops. Crucially,
   * swapping away and back restores the authored deltas without having
   * to re-fetch from the server. */
  setVariant(id: string): void {
    const variant = variantRegistry.get(id);
    if (!variant) {
      // Not registered (yet) — likely a runtime-imported orb racing the
      // catalog fetch. Persist the intent and apply on registration.
      this.pending = { variantId: id };
      saveVariantId(id);
      return;
    }
    this.pending = null;
    const palette = variant.defaultPalette;
    const paletteParams = (variant.palettes[palette] ?? {}) as Record<string, unknown>;
    this.snap = {
      ...this.snap,
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

  /** Restore a saved custom preset (palette + params snapshot).
   * Looks up by name within the *active variant's* saved presets —
   * a name saved on Tetra won't be visible after switching to Nebula
   * because their schemas don't share fields. */
  loadCustomPreset(name: string): void {
    const customs = loadCustomByVariant(this.snap.variantId);
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
