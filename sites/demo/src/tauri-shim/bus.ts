/**
 * A tiny in-process event bus that stands in for Tauri's event system.
 *
 * The shim's listen() (event.ts) subscribes here; the in-browser engine
 * publishes here. The app's useVoiceBridge listens for 'orbis-sse' with a
 * { event, data } envelope — emitSse() produces exactly that shape so the
 * orb / status pill / transcripts react identically to the native app.
 */
type Handler = (payload: unknown) => void;

const handlers = new Map<string, Set<Handler>>();

export function on(name: string, handler: Handler): () => void {
  let set = handlers.get(name);
  if (!set) {
    set = new Set();
    handlers.set(name, set);
  }
  set.add(handler);
  return () => {
    set!.delete(handler);
  };
}

export function emit(name: string, payload: unknown): void {
  handlers.get(name)?.forEach((h) => h(payload));
}

/** Emit one ORBIS SSE event in the { event, data } envelope the app's
 *  useVoiceBridge unpacks (data is a JSON string, as on the wire). */
export function emitSse(event: string, data: Record<string, unknown>): void {
  emit('orbis-sse', { event, data: JSON.stringify(data) });
}
