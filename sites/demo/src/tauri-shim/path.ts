// Browser stand-in for @tauri-apps/api/path. Only appLogDir() is used
// (Diagnostics); there's no filesystem in the browser, so report a label.
export async function appLogDir(): Promise<string> {
  return '/orbis-demo/logs/';
}
