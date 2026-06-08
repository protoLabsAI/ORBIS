/**
 * Plugin auto-discovery.
 *
 * Every `plugins/<name>/index.tsx` calls registerPlugin() at module
 * top-level. This eager glob compiles to static side-effect imports, so
 * importing this file is what wires every built-in plugin into the registry.
 *
 * Adding a plugin is: drop a `plugins/<name>/index.tsx` that calls
 * registerPlugin(...). No edit here, no central import list in App.tsx.
 *
 * (Widgets have their own discovery in ../widgets; orb variants in
 * ./orb/variants — each scoped to where its registry is consumed.)
 */
import.meta.glob('./*/index.tsx', { eager: true });
