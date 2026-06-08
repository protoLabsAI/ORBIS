import type { ComponentType } from 'react';
import { createRegistry } from '@/lib/createRegistry';

/**
 * UI slot names — add here first, then plugins can target them.
 * Layout-order decisions live in <PluginHost>, not in plugins.
 */
export type UISlotName =
  | 'stage' // primary visual area (orb)
  | 'overlay-top' // floating top, e.g. status chip, trace chip
  | 'overlay-bottom' // floating bottom, e.g. status pill, transcript strip
  | 'drawer-quick' // drawer tab: at-a-glance status + most-used toggles (landing)
  | 'drawer-orb' // drawer tab: orb customization editor (disabled for beta — tab not rendered)
  | 'drawer-voice' // drawer tab: audio I/O (mic, activation, STT, TTS)
  | 'drawer-brain' // drawer tab: LLM, delegates, replies
  | 'drawer-settings' // drawer tab: access, setup, diagnostics, about
  | 'drawer-dev'; // drawer tab: developer observability

export interface Plugin {
  id: string;
  /**
   * Render priority within a slot when several plugins share it
   * (lower = earlier). Only matters for multi-contributor slots like
   * `overlay-top`; defaults to 100. Registration order breaks ties.
   */
  order?: number;
  /** Optional UI contributions, keyed by slot. */
  slots?: Partial<Record<UISlotName, ComponentType>>;
}

// Built on the shared registry primitive; componentsForSlot is the
// plugin-specific query layered on top.
const base = createRegistry<Plugin>();

export const pluginRegistry = {
  register: base.register,
  get: base.get,
  all: base.all,
  subscribe: base.subscribe,

  /**
   * Render contributions for a slot, ordered by `order` (ties keep
   * registration order — Array.sort is stable). Called inside <Slot>'s
   * render after a subscribe tick, so a fresh array per call is fine.
   */
  componentsForSlot(slot: UISlotName): Array<{ id: string; Component: ComponentType }> {
    const out: Array<{ id: string; Component: ComponentType; order: number }> = [];
    for (const p of base.all()) {
      const C = p.slots?.[slot];
      if (C) out.push({ id: p.id, Component: C, order: p.order ?? 100 });
    }
    out.sort((a, b) => a.order - b.order);
    return out.map(({ id, Component }) => ({ id, Component }));
  },
};
