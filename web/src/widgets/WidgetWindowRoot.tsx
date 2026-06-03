import { useEffect, useSyncExternalStore } from 'react';
import { getCurrentWindow } from '@tauri-apps/api/window';
import { X } from 'lucide-react';
import { widgetRegistry } from './registry';
import { widgetWorkspace } from './store';

/**
 * Root for a popped-out widget window — renders one widget full-bleed in a
 * borderless, transparent, always-on-top panel (the JARVIS look). The whole
 * panel is the window's drag handle; the close affordance fades in on hover and
 * returns the widget to the dock.
 *
 * Closing for ANY reason (the ×, Cmd+W, app quit) re-docks the widget via
 * `beforeunload`, so it's never orphaned at surface='window' with no window —
 * the localStorage write is synchronous and the main window's storage listener
 * picks it up.
 */
export function WidgetWindowRoot({ id }: { id: string }) {
  const open = useSyncExternalStore(widgetWorkspace.subscribe, widgetWorkspace.getSnapshot);
  const entry = open.find((w) => w.id === id);
  const def = widgetRegistry.get(id);

  useEffect(() => {
    const onBeforeUnload = () => widgetWorkspace.setSurface(id, 'dock');
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, [id]);

  if (!def) return null;
  const Body = def.render;

  return (
    <div
      data-tauri-drag-region
      className="group fixed inset-0 flex cursor-grab select-none items-center justify-center rounded-xl bg-surface ring-1 ring-edge"
    >
      {/* Pointer-transparent so the whole panel drags the window (widgets are
          display-only, per the voice-first model — no interactive content). */}
      <div className="pointer-events-none px-3 py-2">
        <Body id={id} surface="window" props={entry?.props} />
      </div>
      <button
        type="button"
        onClick={() => void getCurrentWindow().close()}
        aria-label="Dock back"
        className="pointer-events-auto absolute right-1.5 top-1.5 grid h-5 w-5 place-items-center rounded-full text-fg-subtle opacity-0 transition-opacity hover:bg-edge hover:text-fg-body group-hover:opacity-100"
      >
        <X className="h-3 w-3" />
      </button>
    </div>
  );
}
