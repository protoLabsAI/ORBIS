import { useEffect, useState } from 'react';
import { pluginRegistry, type Plugin, type UISlotName } from './registry';

/**
 * Mount a plugin at module load — call at module top-level, not inside
 * a component, so the plugin is present before any <Slot> tries to read it.
 */
export function registerPlugin(plugin: Plugin): void {
  pluginRegistry.register(plugin);
}

/**
 * Render all plugin contributions for a given slot. Plugins render in
 * registration order; use one Slot per layout region.
 */
export function Slot({ name }: { name: UISlotName }) {
  const entries = useRegistrySnapshot().componentsForSlot(name);
  return (
    <>
      {entries.map(({ id, Component }) => (
        <Component key={id} />
      ))}
    </>
  );
}

function useRegistrySnapshot() {
  // Intentionally use a counter + subscribe pattern rather than
  // useSyncExternalStore: the registry is usually static (plugins
  // register at module load), but we allow late-mounted plugins for
  // lazy-loaded extensibility.
  const [, setTick] = useState(0);
  useEffect(() => pluginRegistry.subscribe(() => setTick((n) => n + 1)), []);
  return pluginRegistry;
}
