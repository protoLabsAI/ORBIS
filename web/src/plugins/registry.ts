import type { ComponentType } from 'react';

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
  /** Optional UI contributions, keyed by slot. */
  slots?: Partial<Record<UISlotName, ComponentType>>;
}

type Listener = () => void;

class PluginRegistry {
  private byId = new Map<string, Plugin>();
  private listeners = new Set<Listener>();

  register(plugin: Plugin): () => void {
    this.byId.set(plugin.id, plugin);
    this.emit();
    return () => {
      this.byId.delete(plugin.id);
      this.emit();
    };
  }

  all(): ReadonlyArray<Plugin> {
    return Array.from(this.byId.values());
  }

  componentsForSlot(slot: UISlotName): Array<{ id: string; Component: ComponentType }> {
    const out: Array<{ id: string; Component: ComponentType }> = [];
    for (const p of this.byId.values()) {
      const C = p.slots?.[slot];
      if (C) out.push({ id: p.id, Component: C });
    }
    return out;
  }

  subscribe(l: Listener): () => void {
    this.listeners.add(l);
    return () => {
      this.listeners.delete(l);
    };
  }

  private emit() {
    this.listeners.forEach((l) => l());
  }
}

export const pluginRegistry = new PluginRegistry();
