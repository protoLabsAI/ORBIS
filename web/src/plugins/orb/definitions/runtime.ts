/**
 * Runtime-imported `.orbis` orbs — the app-side catalog client.
 *
 * The sidecar persists definitions in the app-data orbs dir and serves
 * them via /api/orbs (agent/orb_definitions.py). This module loads the
 * catalog at boot, registers each definition with the raymarch-v1
 * engine, and owns import/remove. Built-in variants and bundled
 * definitions are untouched — runtime orbs layer on top through the
 * same registry.
 */
import {
  compileCheck,
  validateOrbDefinition,
  type OrbDefinition,
} from '@orbis/orb-runtime';
import { api } from '@/lib/api';
import { registerDefinition } from './host';
import { variantRegistry } from '../variants/registry';
import { orbStore } from '../store';
import { setVariant } from '../broadcast';

/** Deregister fns for runtime-imported orbs, keyed by id. Doubles as
 * the "is this id an imported orb?" predicate for the settings UI. */
const disposers = new Map<string, () => void>();

export function isRuntimeOrb(id: string): boolean {
  return disposers.has(id);
}

export function runtimeOrbIds(): string[] {
  return [...disposers.keys()];
}

function registerRuntime(def: OrbDefinition): void {
  disposers.get(def.id)?.();
  disposers.set(def.id, registerDefinition(def));
}

/** Fetch + register the imported-orb catalog. Fire-and-forget at boot;
 * one retry covers the sidecar still coming up. */
export async function loadRuntimeOrbs(attempt = 0): Promise<void> {
  try {
    const { orbs } = await api.orbs.list();
    for (const raw of orbs) {
      const res = validateOrbDefinition(raw);
      if (res.ok) registerRuntime(res.def);
      else console.warn('[orb] stored definition rejected:', res.errors);
    }
  } catch {
    if (attempt < 2) setTimeout(() => loadRuntimeOrbs(attempt + 1), 4000);
  }
}

export interface ImportResult {
  ok: boolean;
  id?: string;
  errors: string[];
}

/** Full import path for a user-picked `.orbis` file: parse → validate →
 * shader compile check → persist via the sidecar → register + activate. */
export async function importOrbisFile(file: File): Promise<ImportResult> {
  let raw: unknown;
  try {
    raw = JSON.parse(await file.text());
  } catch {
    return { ok: false, errors: ['not valid JSON'] };
  }

  const res = validateOrbDefinition(raw);
  if (!res.ok) return { ok: false, errors: res.errors };
  const def = res.def;

  // An id that collides with a built-in variant would shadow it in the
  // registry — only same-id REimports (updates) are allowed.
  if (variantRegistry.get(def.id) && !disposers.has(def.id)) {
    return { ok: false, errors: [`id "${def.id}" collides with a built-in orb`] };
  }

  const compiled = compileCheck(def);
  if (!compiled.ok) {
    return { ok: false, errors: [`shader failed to compile: ${compiled.log || 'unknown error'}`] };
  }

  const saved = await api.orbs.import(def).catch((e: unknown) => ({
    ok: false as const,
    error: e instanceof Error ? e.message : String(e),
    errors: undefined,
  }));
  if (!saved.ok) {
    return { ok: false, errors: saved.errors ?? [saved.error ?? 'import failed'] };
  }

  registerRuntime(def);
  setVariant(def.id);
  return { ok: true, id: def.id, errors: [] };
}

/** Delete an imported orb: sidecar first, then deregister. Falls back
 * to the first registered variant when the deleted orb was active. */
export async function removeRuntimeOrb(id: string): Promise<boolean> {
  const res = await api.orbs.remove(id).catch(() => ({ ok: false }));
  if (!res.ok) return false;
  disposers.get(id)?.();
  disposers.delete(id);
  if (orbStore.getSnapshot().variantId === id) {
    const fallback = variantRegistry.all()[0];
    if (fallback) setVariant(fallback.id);
  }
  return true;
}
