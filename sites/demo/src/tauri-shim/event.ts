// Browser stand-in for @tauri-apps/api/event. Only listen() + UnlistenFn
// are used by the app (useVoiceBridge, BootStatus, visibility).
import { on } from './bus';

export type UnlistenFn = () => void;

export interface Event<T> {
  event: string;
  id: number;
  payload: T;
}

export type EventCallback<T> = (event: Event<T>) => void;

let nextId = 0;

export async function listen<T>(
  event: string,
  handler: EventCallback<T>,
): Promise<UnlistenFn> {
  return on(event, (payload) =>
    handler({ event, id: nextId++, payload: payload as T }),
  );
}
