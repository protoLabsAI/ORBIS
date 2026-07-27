import { invoke } from '@tauri-apps/api/core';
import { useAppVersion } from '@/lib/useAppVersion';
import { cn } from '@/lib/utils';

/** The studio homepage. protoAgent points its attribution at the same place —
 *  the two apps should read as one product family. */
const STUDIO_URL = 'https://protolabs.studio';

/**
 * "built by protoLabs.studio", opening the studio homepage in the user's
 * browser.
 *
 * External links go through the `open_url` IPC (→ `shell.open`), never a plain
 * `<a target="_blank">` — an anchor navigates the WKWebView itself, which
 * replaces the app UI with a web page and strands the user.
 *
 * The wordmark is `protoLabs.studio`, exactly — matching protoAgent.
 */
export function ProtoLabsLink({ className }: { className?: string }) {
  return (
    <button
      type="button"
      onClick={() => invoke('open_url', { url: STUDIO_URL }).catch(() => {})}
      className={cn(
        'text-fg-muted transition-colors hover:text-brand focus-visible:text-brand',
        'focus-visible:outline-none',
        className,
      )}
    >
      built by <strong className="font-semibold text-fg-body transition-colors">protoLabs.studio</strong>
    </button>
  );
}

/**
 * Version + studio attribution as one row — the persistent footer of the
 * drawer, so the shipped version and who made it are always in reach instead
 * of buried in Settings → About. Mirrors protoAgent's drawer footer.
 */
export function BuiltBy({ className }: { className?: string }) {
  const version = useAppVersion();

  return (
    <div className={cn('flex items-center justify-between gap-2 text-helper', className)}>
      {version ? (
        <span className="inline-flex items-center rounded-full border border-edge px-2 py-0.5 text-fg-muted tabular-nums">
          v{version}
        </span>
      ) : (
        <span />
      )}
      <ProtoLabsLink />
    </div>
  );
}
