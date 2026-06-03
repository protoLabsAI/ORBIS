import { type PointerEvent, useRef, useState, useSyncExternalStore } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { PictureInPicture2, X } from 'lucide-react';
import { type WidgetDef, widgetRegistry } from './registry';
import { type OpenWidget, widgetWorkspace } from './store';

/**
 * Widget layer — open ('dock'-surface) widgets float freely over the surface,
 * each draggable and remembering where the user put it. Deliberately minimal
 * (JARVIS, not a window manager): a solid panel (no glassmorphism), no title
 * bar, a close affordance that only appears on hover. The orb stays voice-first;
 * the widgets are ambient glanceable readouts.
 *
 * The layer itself is pointer-transparent so empty space still reaches the orb
 * (double-click to toggle the mic); only the panels capture pointer events.
 */
export function WidgetDock() {
  const open = useSyncExternalStore(widgetWorkspace.subscribe, widgetWorkspace.getSnapshot);
  const docked = open.filter((w) => w.surface === 'dock');
  if (docked.length === 0) return null;

  return (
    <div className="pointer-events-none fixed inset-0 z-20">
      {docked.map((w, i) => {
        const def = widgetRegistry.get(w.id);
        if (!def) return null;
        return <DraggableWidget key={w.id} def={def} entry={w} index={i} />;
      })}
    </div>
  );
}

function DraggableWidget({
  def,
  entry,
  index,
}: {
  def: WidgetDef;
  entry: OpenWidget;
  index: number;
}) {
  const Body = def.render;
  const panelRef = useRef<HTMLDivElement>(null);
  const drag = useRef<{ px: number; py: number; ox: number; oy: number } | null>(null);
  const posRef = useRef({
    x: entry.x ?? 16 + index * 14,
    y: entry.y ?? 52 + index * 14,
  });
  const [pos, setPos] = useState(posRef.current);
  const [dragging, setDragging] = useState(false);

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    // Let interactive children (future widgets) work — only bare panel drags.
    if ((e.target as HTMLElement).closest('button, input, a, select, textarea')) return;
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { px: e.clientX, py: e.clientY, ox: posRef.current.x, oy: posRef.current.y };
    setDragging(true);
  };

  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d) return;
    const w = panelRef.current?.offsetWidth ?? 0;
    const h = panelRef.current?.offsetHeight ?? 0;
    const nx = Math.max(8, Math.min(d.ox + (e.clientX - d.px), window.innerWidth - w - 8));
    const ny = Math.max(8, Math.min(d.oy + (e.clientY - d.py), window.innerHeight - h - 8));
    posRef.current = { x: nx, y: ny };
    setPos(posRef.current);
  };

  const endDrag = (e: PointerEvent<HTMLDivElement>) => {
    if (!drag.current) return;
    drag.current = null;
    setDragging(false);
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      // capture may already be gone
    }
    widgetWorkspace.setPosition(def.id, posRef.current.x, posRef.current.y);
  };

  return (
    <div
      ref={panelRef}
      className={`group/w pointer-events-auto absolute select-none ${dragging ? 'cursor-grabbing' : 'cursor-grab'}`}
      style={{ left: pos.x, top: pos.y }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
    >
      <div className="relative rounded-xl bg-raised px-3 py-2 shadow-lg ring-1 ring-edge">
        <Body id={def.id} surface="dock" props={entry.props} />
        <div className="absolute -right-1.5 -top-1.5 flex gap-0.5 opacity-0 transition-opacity group-hover/w:opacity-100">
          <button
            type="button"
            onClick={() => {
              void invoke('open_widget_window', { id: def.id, title: def.title })
                .then(() => widgetWorkspace.setSurface(def.id, 'window'))
                .catch(() => {});
            }}
            aria-label={`Pop out ${def.title}`}
            className="grid h-5 w-5 place-items-center rounded-full bg-raised text-fg-subtle ring-1 ring-edge transition-colors hover:text-fg-body"
          >
            <PictureInPicture2 className="h-2.5 w-2.5" />
          </button>
          <button
            type="button"
            onClick={() => widgetWorkspace.close(def.id)}
            aria-label={`Close ${def.title}`}
            className="grid h-5 w-5 place-items-center rounded-full bg-raised text-fg-subtle ring-1 ring-edge transition-colors hover:text-fg-body"
          >
            <X className="h-3 w-3" />
          </button>
        </div>
      </div>
    </div>
  );
}
