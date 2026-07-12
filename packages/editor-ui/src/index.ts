/**
 * @orbis/editor-ui — the shared .orbis authoring surface.
 *
 * The editor store + the five authoring panes (Shader / Controls / Bindings /
 * JSON / Meta), extracted so BOTH the standalone editor (sites/editor) and the
 * in-app editor (web/) render the same panes against the same store. Consumed
 * as source via a bundler alias (like @orbis/orb-runtime).
 */

// Editor store (single source of truth: definition + params + sim + shaderLog).
export { initStore, store } from './state';
export type { EditorSnapshot, SimState, LevelMode } from './state';

// Authoring panes.
export { ShaderPane } from './panes/ShaderPane';
export { ControlsPane } from './panes/ControlsPane';
export { BindingsPane } from './panes/BindingsPane';
export { JsonPane } from './panes/JsonPane';
export { MetaPane } from './panes/MetaPane';
