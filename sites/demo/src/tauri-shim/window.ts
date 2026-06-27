// Browser stand-in for @tauri-apps/api/window. The demo is always the
// single "main" window; close/hide are no-ops.
export interface ShimWindow {
  label: string;
  close(): Promise<void>;
  hide(): Promise<void>;
}

const mainWindow: ShimWindow = {
  label: 'main',
  async close() {},
  async hide() {},
};

export function getCurrentWindow(): ShimWindow {
  return mainWindow;
}
