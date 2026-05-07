/**
 * Detect Document Picture-in-Picture support.
 *
 * Chrome 116+, Edge 116+, Opera 102+. Safari and Firefox don't ship
 * this yet (as of 2026-05). Used to gate the PiP trigger button so we
 * don't render a control that does nothing on the unsupported half of
 * the desktop browser market.
 *
 * Static getter — the API doesn't appear / disappear at runtime, so
 * checking once on module load is enough.
 */
export const isPiPSupported: boolean = typeof window !== 'undefined'
  && 'documentPictureInPicture' in window;

// Permissive ambient typing for the API shape we use. The lib.dom
// typings don't include documentPictureInPicture yet on every TS
// release. Keeping it scoped here so the rest of the codebase doesn't
// import a global declaration.
declare global {
  interface Window {
    documentPictureInPicture?: {
      requestWindow(options?: {
        width?: number;
        height?: number;
        disallowReturnToOpener?: boolean;
        preferInitialWindowPlacement?: boolean;
      }): Promise<Window>;
    };
  }
}
