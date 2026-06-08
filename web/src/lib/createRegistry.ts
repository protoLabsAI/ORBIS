/**
 * Tiny id-keyed registry primitive shared by the plugin, widget, and
 * orb-variant registries (web/src/plugins, web/src/widgets,
 * web/src/plugins/orb/variants).
 *
 * Items register by `id` — re-registering the same id replaces it. `all()`
 * returns a cached array whose reference stays stable until the set changes,
 * so it is safe to pass straight to `useSyncExternalStore` as the snapshot
 * (a fresh array every call drives an infinite render loop). `register()`
 * returns a deregister fn. Every method is a closure (no `this`), so
 * `registry.all` / `registry.subscribe` can be passed as bare references.
 */
export interface Registry<T extends { id: string }> {
  register: (item: T) => () => void;
  get: (id: string) => T | undefined;
  all: () => ReadonlyArray<T>;
  subscribe: (listener: () => void) => () => void;
}

export function createRegistry<T extends { id: string }>(): Registry<T> {
  const byId = new Map<string, T>();
  const listeners = new Set<() => void>();
  let snapshot: ReadonlyArray<T> = [];

  const rebuild = () => {
    snapshot = Array.from(byId.values());
    listeners.forEach((l) => l());
  };

  return {
    register: (item) => {
      byId.set(item.id, item);
      rebuild();
      return () => {
        if (byId.delete(item.id)) rebuild();
      };
    },
    get: (id) => byId.get(id),
    all: () => snapshot,
    subscribe: (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}
