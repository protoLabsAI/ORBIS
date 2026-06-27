// Browser stand-in for @tauri-apps/plugin-updater. No auto-update in the
// browser demo: check() reports "no update", so UpdateNotice stays hidden.
export type Update = {
  available: boolean;
  version?: string;
  downloadAndInstall?: () => Promise<void>;
} | null;

export async function check(): Promise<Update> {
  return null;
}
