import { registerPlugin } from '../plugins/PluginHost';
import { WidgetLauncher } from './WidgetLauncher';

/**
 * Widget auto-discovery.
 *
 * Every `widgets/<name>/index.tsx` calls registerWidget() at module
 * top-level. This eager glob compiles to static side-effect imports, so
 * importing this file registers every built-in widget.
 *
 * Adding a widget is: drop a `widgets/<name>/index.tsx` that calls
 * registerWidget(...). No edit here.
 */
import.meta.glob('./*/index.tsx', { eager: true });

// The launcher is always-on chrome (a rail button), not a widget, so it rides
// the plugin slot. order:40 keeps it last in overlay-top — after the reminders
// bell (10), mic toggle (20) and setup wizard (30) — matching the pre-glob
// import order.
registerPlugin({ id: 'widget-launcher', order: 40, slots: { 'overlay-top': WidgetLauncher } });
