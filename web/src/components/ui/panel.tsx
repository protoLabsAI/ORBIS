import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { ChevronRight } from 'lucide-react';
import { cn } from '@/lib/utils';

/**
 * Section container with consistent spacing and a tiny-caps heading.
 * Replaces the inline
 *   <section className="space-y-3">
 *     <div className="text-[10px] font-mono uppercase tracking-wider text-zinc-500">…</div>
 * pattern that was repeated across every drawer panel.
 *
 * Optionally collapsible via ``CollapsiblePanelProvider``. Wrapped
 * panels persist open/closed state per title in localStorage so long
 * settings surfaces stay compact without losing the user's layout.
 */
export function Panel({
  title,
  aside,
  className,
  children,
}: {
  title?: string;
  /** Optional right-aligned element rendered next to the heading. */
  aside?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  const collapsibleCtx = useContext(CollapsiblePanelContext);
  const collapsible = collapsibleCtx.enabled && Boolean(title);
  const storageKey = collapsibleCtx.storageKey && title
    ? `${collapsibleCtx.storageKey}:${title}`
    : null;
  const [open, setOpen] = useState<boolean>(() => readStored(storageKey, false));

  useEffect(() => {
    if (!storageKey) return;
    try {
      window.localStorage.setItem(storageKey, open ? '1' : '0');
    } catch {
      // localStorage can fail in restricted/private contexts; in that
      // case the state simply becomes session-local.
    }
  }, [storageKey, open]);

  if (!collapsible) {
    return (
      <section className={cn('space-y-3', className)}>
        {title && (
          <div className="flex items-center justify-between">
            <PanelHeading>{title}</PanelHeading>
            {aside}
          </div>
        )}
        {children}
      </section>
    );
  }

  return (
    <section className={cn('space-y-3', className)}>
      <div className="flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="flex items-center gap-1.5 -m-1 p-1 rounded hover:bg-zinc-900/50 transition-colors text-left"
          aria-expanded={open}
        >
          <ChevronRight
            className={cn(
              'h-4 w-4 text-zinc-500 transition-transform',
              open && 'rotate-90',
            )}
            aria-hidden="true"
          />
          <PanelHeading>{title}</PanelHeading>
        </button>
        {aside}
      </div>
      {open && children}
    </section>
  );
}

export function PanelHeading({ children }: { children: ReactNode }) {
  return (
    <div className="text-[11px] font-mono uppercase tracking-wider text-zinc-400">
      {children}
    </div>
  );
}

interface CollapsiblePanelContextValue {
  enabled: boolean;
  storageKey: string | null;
}

const CollapsiblePanelContext = createContext<CollapsiblePanelContextValue>({
  enabled: false,
  storageKey: null,
});

export function CollapsiblePanelProvider({
  storageKey,
  children,
}: {
  storageKey: string;
  children: ReactNode;
}) {
  return (
    <CollapsiblePanelContext.Provider value={{ enabled: true, storageKey }}>
      {children}
    </CollapsiblePanelContext.Provider>
  );
}

function readStored(key: string | null, fallback: boolean): boolean {
  if (!key) return fallback;
  try {
    const raw = window.localStorage.getItem(key);
    if (raw === '0') return false;
    if (raw === '1') return true;
  } catch {
    // localStorage unavailable; use the default.
  }
  return fallback;
}
