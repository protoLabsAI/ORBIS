/**
 * Logs panel module — the panel itself is now embedded in the Dev tab
 * (via DevPanel) rather than getting its own drawer slot, so this
 * index doesn't register anything in the plugin registry. The
 * LogsCollector export is mounted unconditionally at App root so the
 * ring buffer keeps capturing whether the panel is visible or not.
 */
export { LogsCollector } from './LogsCollector';
export { LogsPanel } from './LogsPanel';
