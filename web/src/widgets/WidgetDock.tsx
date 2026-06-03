import { useSyncExternalStore } from 'react';
import { X } from 'lucide-react';
import { widgetRegistry } from './registry';
import { widgetWorkspace } from './store';

/**
 * In-app widget dock — a column of open ('dock'-surface) widgets down the left
 * edge, below the macOS traffic lights. Each is a card: title bar (icon + name
 * + close) over the widget body. Native pop-out (a card → its own window) lands
 * in Stage 2b. Hidden entirely when nothing is docked.
 */
export function WidgetDock() {
  const open = useSyncExternalStore(widgetWorkspace.subscribe, widgetWorkspace.getSnapshot);
  const docked = open.filter((w) => w.surface === 'dock');
  if (docked.length === 0) return null;

  return (
    <div
      className="fixed z-20 flex flex-col gap-2"
      style={{
        // Below the traffic lights (top-left), clear of the top-right chrome rail.
        top: 'calc(2.75rem + env(safe-area-inset-top, 0px))',
        left: 'calc(0.75rem + env(safe-area-inset-left, 0px))',
        width: '18rem',
        maxWidth: 'calc(100vw - 1.5rem)',
      }}
    >
      {docked.map((w) => {
        const def = widgetRegistry.get(w.id);
        if (!def) return null;
        const Body = def.render;
        const Icon = def.icon;
        return (
          <section
            key={w.id}
            className="rounded-lg border border-edge bg-surface/95 shadow-xl backdrop-blur-sm overflow-hidden"
          >
            <header className="flex items-center gap-2 px-2.5 py-1.5 border-b border-edge">
              <Icon className="h-3.5 w-3.5 text-fg-subtle" />
              <span className="flex-1 text-helper uppercase tracking-wider text-fg-muted truncate">
                {def.title}
              </span>
              <button
                type="button"
                onClick={() => widgetWorkspace.close(w.id)}
                aria-label={`Close ${def.title}`}
                className="p-1 rounded text-fg-subtle hover:text-fg-body hover:bg-edge transition-colors"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </header>
            <div className="p-3">
              <Body id={w.id} surface="dock" />
            </div>
          </section>
        );
      })}
    </div>
  );
}
