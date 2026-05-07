import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { isPiPSupported } from './usePipSupport';
import { PiPOverlay } from './PiPOverlay';

/**
 * Lifecycle owner for the Document Picture-in-Picture overlay.
 *
 * When ``open`` is true:
 *   1. Request a PiP window from the browser (Chrome 116+).
 *   2. Copy the main document's stylesheets into the PiP window's
 *      <head> so Tailwind classes resolve identically.
 *   3. Render PiPOverlay into the PiP window's body via createPortal.
 *      Because we portal (instead of opening a separate React root),
 *      the overlay reads from the same context tree — same
 *      PipecatClientProvider, same voiceStore — without any prop
 *      drilling.
 *   4. Listen for ``pagehide`` on the PiP window so a user closing
 *      the floating window via its native X flips ``open`` back to
 *      false instead of leaving the trigger UI stale.
 *
 * Stylesheet copy uses ``CSSStyleSheet.replaceSync`` for cross-origin
 * resilient @import handling on Chrome — the simpler route of cloning
 * <link> tags fails when the browser refuses to re-fetch the same
 * origin into a child context.
 */
export interface PiPHostProps {
  open: boolean;
  onClose: () => void;
}

export function PiPHost({ open, onClose }: PiPHostProps) {
  const [pipWindow, setPipWindow] = useState<Window | null>(null);

  useEffect(() => {
    if (!open || !isPiPSupported || pipWindow) return;
    let cancelled = false;
    let cleanup: (() => void) | null = null;

    void (async () => {
      try {
        const requestWindow = window.documentPictureInPicture?.requestWindow;
        if (!requestWindow) return;
        const w = await requestWindow.call(window.documentPictureInPicture, {
          width: 280,
          height: 280,
        });
        if (cancelled) {
          w.close();
          return;
        }
        copyStyles(w);
        // pagehide is fired on user-initiated close; flip the trigger
        // state so the button reflects "no PiP open" again.
        const onHide = () => {
          setPipWindow(null);
          onClose();
        };
        w.addEventListener('pagehide', onHide);
        cleanup = () => w.removeEventListener('pagehide', onHide);
        setPipWindow(w);
      } catch (e) {
        // Most likely user denied the prompt. Reset trigger state.
        console.warn('[pip] requestWindow failed:', e);
        if (!cancelled) onClose();
      }
    })();

    return () => {
      cancelled = true;
      cleanup?.();
    };
    // pipWindow intentionally excluded — re-running on every set
    // would re-request a fresh window every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Caller toggled off — close the PiP window if we own one.
  useEffect(() => {
    if (!open && pipWindow) {
      pipWindow.close();
      setPipWindow(null);
    }
  }, [open, pipWindow]);

  if (!pipWindow) return null;
  return createPortal(<PiPOverlay />, pipWindow.document.body);
}

/**
 * Mirror the main document's stylesheets into the PiP window. Without
 * this, Tailwind utility classes resolve to nothing in the new window
 * and the overlay renders unstyled.
 *
 * Two strategies in order of preference:
 *   1. ``CSSStyleSheet.replaceSync`` + ``adoptedStyleSheets`` — works
 *      for in-document <style> rules and is fast (no network round-trip).
 *   2. Clone <link rel="stylesheet"> tags into the PiP head — the
 *      fallback for sheets the browser exposed only as cross-origin.
 *      Slower (re-fetches), but resilient.
 *
 * Both fire — same-origin sheets get adopted, link tags handle the rest.
 */
function copyStyles(target: Window): void {
  const adopted: CSSStyleSheet[] = [];
  for (const sheet of Array.from(document.styleSheets)) {
    try {
      const rules = Array.from(sheet.cssRules);
      // Use the target window's globalThis to construct — it carries
      // CSSStyleSheet on its own global, but the lib.dom typings
      // don't reflect that on Window directly.
      const StyleSheetCtor = (target as unknown as { CSSStyleSheet: typeof CSSStyleSheet }).CSSStyleSheet;
      const out = new StyleSheetCtor();
      out.replaceSync(rules.map((r) => r.cssText).join('\n'));
      adopted.push(out);
    } catch {
      // SecurityError on cross-origin — fall through to <link> clone.
      if (sheet.href) {
        const link = target.document.createElement('link');
        link.rel = 'stylesheet';
        link.href = sheet.href;
        target.document.head.appendChild(link);
      }
    }
  }
  // `adoptedStyleSheets` is the preferred Chrome path; assignment is
  // setter-only so the arrays-merging idiom doesn't apply.
  target.document.adoptedStyleSheets = adopted;
}
