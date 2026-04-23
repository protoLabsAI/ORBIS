import { useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { OrbPreview } from '@/plugins/orb/OrbPreview';
import { orbStore } from '@/plugins/orb/store';
import type { StarterOrb } from '@/lib/api';

/**
 * Fullscreen preview modal for a single starter orb. Mounts the
 * existing OrbPreview (which reads from the global orbStore) after
 * swapping the store to show THIS starter's look; restores the
 * previous orbStore state on close so the wizard's background orb
 * doesn't inherit the preview's state.
 *
 * Variant components handle their own drag-to-rotate via the shared
 * pointer driver — so the preview is interactive for free.
 */
export function OrbPreviewModal({
  starter,
  onClose,
  onConfirm,
}: {
  starter: StarterOrb;
  onClose: () => void;
  onConfirm: () => void;
}) {
  useEffect(() => {
    const s = orbStore.get();
    const snapshot = {
      variantId: s.getSnapshot().variantId,
      palette: s.getSnapshot().palette,
      params: { ...s.getSnapshot().params },
    };
    // Apply the starter's look.
    s.setVariant(starter.variant);
    s.setPreset(starter.palette);
    for (const [k, v] of Object.entries(starter.params ?? {})) {
      s.setParam(k, v);
    }
    // Escape closes.
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => {
      // Restore prior state on unmount.
      s.setVariant(snapshot.variantId);
      s.setPreset(snapshot.palette);
      for (const [k, v] of Object.entries(snapshot.params)) {
        s.setParam(k, v);
      }
      window.removeEventListener('keydown', onKey);
    };
  }, [starter, onClose]);

  return (
    <div className="fixed inset-0 z-40 bg-[#0a0a0a]">
      {/* Full-screen orb canvas. OrbPreview reads from orbStore, which
          we just swapped for this starter. Variant components handle
          drag-to-rotate internally. */}
      <OrbPreview />

      {/* Top bar — close + starter name */}
      <div className="absolute inset-x-0 top-0 p-6 flex items-center justify-between pointer-events-none">
        <div className="pointer-events-auto">
          <div className="font-mono text-sm tracking-wider uppercase text-zinc-400">
            {starter.name}
          </div>
          <div className="text-xs text-zinc-500 max-w-md mt-1">
            {starter.description}
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="pointer-events-auto h-10 w-10 rounded-full grid place-items-center bg-zinc-900/60 hover:bg-zinc-800 text-zinc-400 hover:text-zinc-200 border border-zinc-800 transition-colors"
          aria-label="Close preview"
        >
          ×
        </button>
      </div>

      {/* Bottom bar — pick action */}
      <div className="absolute inset-x-0 bottom-0 p-6 flex items-center justify-center gap-3 pointer-events-none">
        <div className="pointer-events-auto flex items-center gap-3">
          <Button variant="ghost" onClick={onClose}>Keep looking</Button>
          <Button onClick={onConfirm}>Use this orb</Button>
        </div>
      </div>

      {/* Corner hint */}
      <div className="absolute bottom-6 right-6 text-[10px] uppercase tracking-wider text-zinc-600 pointer-events-none">
        drag to rotate
      </div>
    </div>
  );
}
