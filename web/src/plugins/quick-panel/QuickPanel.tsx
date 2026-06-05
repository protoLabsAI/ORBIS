/**
 * Quick tab — the drawer's landing surface.
 *
 * PR1 ships a tidy placeholder so the new tab structure lands cleanly; the
 * follow-up fills it with the at-a-glance state row (mic / connection / model
 * / voice) and the most-used toggles pulled up from the deeper tabs. Kept
 * intentionally minimal so the landing never reads as raw config.
 */
export function QuickPanel() {
  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-edge bg-raised/40 p-5">
        <div className="text-label uppercase tracking-wider text-fg-muted mb-1.5">
          Quick controls
        </div>
        <p className="text-sm text-fg-muted">
          At-a-glance status and your most-used controls will live here.
        </p>
      </div>
    </div>
  );
}
