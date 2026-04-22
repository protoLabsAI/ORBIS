import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

/**
 * Section container with consistent spacing and a tiny-caps heading.
 * Replaces the inline
 *   <section className="space-y-3">
 *     <div className="text-[10px] font-mono uppercase tracking-wider text-zinc-500">…</div>
 * pattern that was repeated across every drawer panel.
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

export function PanelHeading({ children }: { children: ReactNode }) {
  return (
    <div className="text-[10px] font-mono uppercase tracking-wider text-zinc-500">
      {children}
    </div>
  );
}
