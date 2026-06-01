import type { ComponentProps } from 'react';
import { cn } from '@/lib/utils';

/**
 * Muted helper / description text shown under form fields and controls.
 * Replaces the scattered raw inline divs with one
 * tokened role so legibility is tuned in a single place.
 */
export function Hint({ className, ...props }: ComponentProps<'div'>) {
  return <div className={cn('text-helper text-fg-muted', className)} {...props} />;
}
