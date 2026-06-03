import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { Check, LayoutGrid } from 'lucide-react';
import { widgetRegistry } from './registry';
import { widgetWorkspace } from './store';
import { useSurfaceEnabled } from '../surface';

/**
 * Widget launcher — a chrome-rail button (below the mic toggle) that opens a
 * popover of registered widgets; click one to open/close it. Reads the registry
 * + workspace reactively, so a newly-registered widget appears with no change
 * here.
 */
export function WidgetLauncher() {
  const enabled = useSurfaceEnabled();
  const [menuOpen, setMenuOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const defs = useSyncExternalStore(widgetRegistry.subscribe, widgetRegistry.all);
  const open = useSyncExternalStore(widgetWorkspace.subscribe, widgetWorkspace.getSnapshot);
  const openIds = new Set(open.map((w) => w.id));

  useEffect(() => {
    if (!menuOpen) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [menuOpen]);

  if (!enabled || defs.length === 0) return null;

  return (
    <div
      ref={rootRef}
      className="fixed z-20"
      style={{
        // Below the mic toggle in the right-edge rail (gear → bell → mic → this).
        top: 'calc(0.75rem + 9rem + env(safe-area-inset-top, 0px))',
        right: 'calc(0.75rem + env(safe-area-inset-right, 0px))',
      }}
    >
      <button
        type="button"
        onClick={() => setMenuOpen((v) => !v)}
        aria-label="Widgets"
        aria-expanded={menuOpen}
        className="relative grid place-items-center h-11 w-11 sm:h-10 sm:w-10 rounded-full bg-transparent text-fg-subtle/60 hover:text-fg-body focus-visible:text-fg-body focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-fg-faint transition-colors"
      >
        <LayoutGrid className="h-[18px] w-[18px]" strokeWidth={1.5} />
      </button>

      {menuOpen && (
        <div
          role="menu"
          aria-label="Widgets"
          className="absolute right-0 mt-2 w-56 max-w-[calc(100vw-1.5rem)] rounded-lg border border-edge bg-surface/95 p-1.5 shadow-xl backdrop-blur-sm"
        >
          <div className="px-2 py-1 font-mono text-helper uppercase tracking-wider text-fg-muted">
            Widgets
          </div>
          {defs.map((def) => {
            const isOpen = openIds.has(def.id);
            const Icon = def.icon;
            return (
              <button
                key={def.id}
                type="button"
                role="menuitemcheckbox"
                aria-checked={isOpen}
                onClick={() => widgetWorkspace.toggle(def.id, def.defaultSurface ?? 'dock')}
                className="w-full flex items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-fg-body hover:bg-edge transition-colors"
              >
                <Icon className="h-4 w-4 text-fg-subtle shrink-0" />
                <span className="flex-1 truncate">{def.title}</span>
                {isOpen && <Check className="h-3.5 w-3.5 text-brand shrink-0" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
