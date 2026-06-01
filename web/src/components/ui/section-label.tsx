import type { ComponentProps } from 'react';
import { cn } from '@/lib/utils';

/**
 * The uppercase micro-label used for section headings and form-field
 * labels across the chrome. One definition so size/tracking/tone are
 * tuned in a single place instead of re-typed as raw uppercase micro-label
 * classes everywhere.
 *
 * `sectionLabelClass` is exported separately so a real `<label>` element
 * (which needs `htmlFor` association) can share the exact styling without
 * nesting an extra element.
 */
export const sectionLabelClass =
  'text-label uppercase tracking-wider text-fg-muted';

export function SectionLabel({ className, ...props }: ComponentProps<'div'>) {
  return <div className={cn(sectionLabelClass, className)} {...props} />;
}
