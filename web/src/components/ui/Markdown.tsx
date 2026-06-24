import { lazy, Suspense } from 'react';

const Impl = lazy(() => import('./MarkdownImpl').then((m) => ({ default: m.MarkdownImpl })));

/**
 * Lazy markdown renderer — keeps react-markdown off the boot bundle (it only
 * loads once something renders markdown, e.g. the update changelog). Falls back
 * to the raw text until the chunk arrives.
 */
export function Markdown({ children }: { children: string }) {
  return (
    <Suspense fallback={<div className="whitespace-pre-wrap text-sm text-fg-muted">{children}</div>}>
      <Impl>{children}</Impl>
    </Suspense>
  );
}
