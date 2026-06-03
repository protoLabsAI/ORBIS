import { invoke } from '@tauri-apps/api/core';

/**
 * Mini-orb — the ambient presence shown (left edge) when ORBIS's main window is
 * hidden. A small glowing orb in the orb's own accent; click it to bring ORBIS
 * back. Fills its tiny transparent native window.
 */
export function MiniOrb() {
  return (
    <button
      type="button"
      onClick={() => void invoke('show_main').catch(() => {})}
      aria-label="Show ORBIS"
      className="fixed inset-0 grid place-items-center bg-transparent outline-none"
    >
      <span className="relative grid place-items-center">
        {/* soft glow */}
        <span className="absolute h-12 w-12 animate-pulse rounded-full bg-brand/50 blur-lg" />
        {/* the orb */}
        <span className="relative h-10 w-10 rounded-full bg-brand ring-1 ring-brand/60 transition-transform hover:scale-110" />
      </span>
    </button>
  );
}
