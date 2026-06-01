/**
 * Immersive title bar. With macOS `titleBarStyle: "Overlay"` the window has
 * no opaque title bar — the orb fills the frame top-to-bottom and the native
 * traffic lights float top-left. This thin transparent strip restores
 * window-dragging (`data-tauri-drag-region`).
 *
 * z-10 keeps the strip above the orb (draggable) but below the gear/bell
 * (z-20, still clickable) and the boot/splash overlays. It's intentionally
 * empty — no brand mark — so it doesn't compete with the orb.
 */
export function TitleBar() {
  return (
    <div
      data-tauri-drag-region
      className="fixed top-0 inset-x-0 z-10 h-7"
    />
  );
}
