// Browser stand-in for @tauri-apps/api/core. The app calls invoke() for
// its backend (api_request) and for native device/window commands; all of
// it routes through handleInvoke (commands.ts).
import { handleInvoke } from './commands';

export async function invoke<T>(
  cmd: string,
  args?: Record<string, unknown>,
): Promise<T> {
  return handleInvoke(cmd, args ?? {}) as Promise<T>;
}
