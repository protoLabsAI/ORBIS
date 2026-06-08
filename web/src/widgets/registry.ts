import type { ComponentType } from 'react';
import { createRegistry } from '@/lib/createRegistry';

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
  /** State set by voice (render_widget tool) — e.g. weather { location }. */
  props?: Record<string, unknown>;
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

export const widgetRegistry = createRegistry<WidgetDef>();

export function registerWidget(def: WidgetDef): void {
  widgetRegistry.register(def);
}
