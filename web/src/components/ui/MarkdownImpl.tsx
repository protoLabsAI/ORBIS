import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

/**
 * react-markdown + GFM, styled with ORBIS tokens. Lazy-loaded via `Markdown.tsx`
 * so the markdown bundle stays off the boot path (it only matters when something
 * actually renders markdown — today, the update changelog).
 */
export function MarkdownImpl({ children }: { children: string }) {
  return (
    <div className="space-y-2 text-sm leading-relaxed text-fg-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => <h2 className="mt-3 mb-1 text-base font-semibold text-fg">{children}</h2>,
          h2: ({ children }) => <h3 className="mt-3 mb-1 text-sm font-semibold text-fg">{children}</h3>,
          h3: ({ children }) => <h4 className="mt-2 mb-1 text-sm font-medium text-fg">{children}</h4>,
          p: ({ children }) => <p className="text-fg-body">{children}</p>,
          ul: ({ children }) => <ul className="list-disc space-y-1 pl-5">{children}</ul>,
          ol: ({ children }) => <ol className="list-decimal space-y-1 pl-5">{children}</ol>,
          li: ({ children }) => <li className="text-fg-body">{children}</li>,
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noreferrer" className="text-brand underline-offset-2 hover:underline">
              {children}
            </a>
          ),
          code: ({ children }) => (
            <code className="rounded bg-raised px-1 py-0.5 font-mono text-[0.85em] text-fg">{children}</code>
          ),
          pre: ({ children }) => (
            <pre className="overflow-x-auto rounded bg-raised p-2 font-mono text-[0.85em]">{children}</pre>
          ),
          strong: ({ children }) => <strong className="font-semibold text-fg">{children}</strong>,
          hr: () => <hr className="border-edge" />,
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
