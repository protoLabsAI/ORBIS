import type { ComponentType } from 'react';

/**
 * Widget runtime — content panels the user opens, docks in the main window, or
 * (Stage 2b) pops out to a native window. Distinct from plugins: plugins are
 * always-on chrome; widgets are openable/closable content with a surface.
 *
 * A widget is defined once and can mount in either a dock card or a window root
 * — it never knows where it lives (it gets `surface` as a prop). Adding a widget
 * is one `registerWidget(...)`.
 */

export type WidgetClass = 'glance' | 'content' | 'agent';
export type WidgetSurface = 'dock' | 'window';

export interface WidgetProps {
  id: string;
  surface: WidgetSurface;
}

export interface WidgetDef {
  id: string;
  title: string;
  icon: ComponentType<{ className?: string }>;
  klass: WidgetClass;
  /** Default opening surface. Locked to 'dock' until native pop-out (2b). */
  defaultSurface?: WidgetSurface;
  render: ComponentType<WidgetProps>;
}

type Listener = () => void;

class WidgetRegistry {
  private byId = new Map<string, WidgetDef>();
  // Cached array so useSyncExternalStore gets a stable reference between
  // registrations (rebuilt only when the set changes).
  private snap: ReadonlyArray<WidgetDef> = [];
  private listeners = new Set<Listener>();

  register(def: WidgetDef): void {
    this.byId.set(def.id, def);
    this.snap = Array.from(this.byId.values());
    this.listeners.forEach((l) => l());
  }

  get(id: string): WidgetDef | undefined {
    return this.byId.get(id);
  }

  all = (): ReadonlyArray<WidgetDef> => this.snap;

  subscribe = (l: Listener): (() => void) => {
    this.listeners.add(l);
    return () => {
      this.listeners.delete(l);
    };
  };
}

export const widgetRegistry = new WidgetRegistry();

export function registerWidget(def: WidgetDef): void {
  widgetRegistry.register(def);
}
