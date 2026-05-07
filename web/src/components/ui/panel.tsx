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
 * Optionally collapsible — see ``CollapsiblePanelProvider``. When the
 * surrounding context says so, the heading becomes a click-to-toggle
 * button and the content collapses underneath. Open/closed state is
 * persisted per panel title in localStorage so the user's layout
 * survives reload. Default-on so first-time users see everything;
 * they collapse what they don't need.
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

  const [open, setOpen] = useState<boolean>(() => readStored(storageKey, true));

  useEffect(() => {
    if (!storageKey) return;
    try {
      window.localStorage.setItem(storageKey, open ? '1' : '0');
    } catch {
      // localStorage write failed (private mode / quota) — silently
      // degrade to "this session only," which is fine.
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
              'h-3 w-3 text-zinc-600 transition-transform',
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
    <div className="text-[10px] font-mono uppercase tracking-wider text-zinc-500">
      {children}
    </div>
  );
}

/**
 * Provider that flips every nested ``<Panel title=…>`` into a
 * collapsible accordion section. Opt-in — non-wrapped Panels stay
 * the same. Each provider needs a unique ``storageKey`` so panels in
 * different drawers don't share open/closed state.
 */
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
    // private mode / SSR — fall through to default
  }
  return fallback;
}
