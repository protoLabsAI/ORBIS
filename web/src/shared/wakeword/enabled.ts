/**
 * Wake-word UI kill-switch — TEMPORARILY DISABLED 2026-06-25.
 *
 * The bundled `hey_orbis` wake-word model needs retraining before it's
 * good enough to ship. Until the new model lands, the wake-word
 * activation style is hidden from the activation pickers (Quick tab +
 * Activation settings) and the on-device model catalog is neither
 * loaded nor suggested in the UI.
 *
 * Nothing is deleted: the backend `/api/wakeword*` endpoints, the model
 * catalog, and the Rust detector all stay intact. The detector is gated
 * server-side on `style === 'wake_word'` (src-tauri … wake_config), and
 * that style can no longer be selected, so it stays dormant.
 *
 * To restore: flip this to `true` (and re-run the build). That's the
 * whole switch — every wake-word surface keys off it.
 */
export const WAKE_WORD_ENABLED = false;
