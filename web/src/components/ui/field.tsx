import type { ReactNode } from 'react';
import { Label } from '@/components/ui/label';
import { cn } from '@/lib/utils';

/**
 * Labeled form field wrapper. Keeps the recurring
 *   <div className="space-y-1.5">
 *     <Label>…</Label>
 *     <input …/>
 * pattern in one place, and provides a header-row slot for
 * inline-with-label controls (e.g. a live numeric readout).
 */
export function Field({
  label,
  htmlFor,
  headerAside,
  hint,
  error,
  className,
  children,
}: {
  label?: string;
  htmlFor?: string;
  /** Right-aligned element in the label row (numeric readout, status). */
  headerAside?: ReactNode;
  /** Muted helper text below the input. */
  hint?: ReactNode;
  /** Error text below the input (red). */
  error?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={cn('space-y-1.5', className)}>
      {label && (
        <div className="flex items-center justify-between">
          <Label htmlFor={htmlFor} className="text-xs text-zinc-400">{label}</Label>
          {headerAside}
        </div>
      )}
      {children}
      {hint && <div className="text-[11px] text-zinc-500">{hint}</div>}
      {error && <div className="text-[11px] text-red-400">{error}</div>}
    </div>
  );
}
