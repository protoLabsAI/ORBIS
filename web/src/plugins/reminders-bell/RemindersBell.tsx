import { useCallback, useEffect, useRef, useState } from 'react';
import { Bell, Repeat, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { api, type ReminderItem } from '@/lib/api';

/**
 * Reminders bell — top-level alert affordance. Sits in the top-right
 * chrome (left of the settings gear) and carries a subtle dot, tinted
 * by the orb's current accent color, whenever the agent has reminders
 * scheduled. Clicking opens a popover to review and cancel them.
 *
 * This is the visual surface for the same `/api/reminders` data the
 * agent manages by voice (`list_reminders` / `cancel_reminder`). It
 * used to live as a panel inside the Agent settings tab; it was lifted
 * out to top-level so a stray recurring reminder is one click away
 * without opening settings.
 */

const POLL_MS = 15_000;

function whenLabel(fireAt: string): string {
  const t = new Date(fireAt).getTime();
  if (Number.isNaN(t)) return '';
  const mins = Math.round((t - Date.now()) / 60000);
  if (mins <= 0) return 'any moment';
  if (mins < 60) return `in ${mins} min`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `in ${hrs} hr${hrs === 1 ? '' : 's'}`;
  const days = Math.round(hrs / 24);
  return `in ${days} day${days === 1 ? '' : 's'}`;
}

function everyLabel(repeatSecs: number | null): string {
  if (!repeatSecs) return '';
  const m = Math.round(repeatSecs / 60);
  if (m < 60) return `every ${m} min`;
  const h = Math.round(m / 60);
  return h % 24 === 0 && h >= 24
    ? `every ${h / 24} day${h / 24 === 1 ? '' : 's'}`
    : `every ${h} hr${h === 1 ? '' : 's'}`;
}

export function RemindersBell() {
  const [items, setItems] = useState<ReminderItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | 'all' | null>(null);
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  const reload = useCallback(async () => {
    try {
      const r = await api.reminders.list();
      setItems(r.reminders);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  // Poll so the dot stays current even while the popover is closed, and
  // refresh whenever the window regains focus (a reminder may have fired
  // or been set by voice while it was backgrounded).
  useEffect(() => {
    void reload();
    const id = window.setInterval(() => void reload(), POLL_MS);
    const onFocus = () => void reload();
    window.addEventListener('focus', onFocus);
    return () => {
      window.clearInterval(id);
      window.removeEventListener('focus', onFocus);
    };
  }, [reload]);

  // Dismiss the popover on outside click or Escape.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    window.addEventListener('mousedown', onDown);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('mousedown', onDown);
      window.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const cancelOne = useCallback(
    async (id: number) => {
      setBusy(id);
      try {
        await api.reminders.cancel({ id });
        await reload();
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setBusy(null);
      }
    },
    [reload],
  );

  const cancelAll = useCallback(async () => {
    setBusy('all');
    try {
      await api.reminders.cancel({ all: true });
      await reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }, [reload]);

  const count = items?.length ?? 0;
  const hasReminders = count > 0;

  return (
    <div
      ref={rootRef}
      className="fixed z-20"
      style={{
        // Stack vertically down the right edge, directly below the settings
        // gear (which sits at top: 0.75rem, right: 0.75rem, ~2.75rem tall).
        top: 'calc(0.75rem + 3rem + env(safe-area-inset-top, 0px))',
        right: 'calc(0.75rem + env(safe-area-inset-right, 0px))',
      }}
    >
      <button
        type="button"
        aria-label={
          hasReminders
            ? `Reminders — ${count} scheduled`
            : 'Reminders — none scheduled'
        }
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => {
          setOpen((o) => !o);
          void reload();
        }}
        className="relative grid place-items-center h-11 w-11 sm:h-10 sm:w-10 rounded-full bg-transparent text-fg-subtle/60 hover:text-fg-body focus-visible:text-fg-body focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-fg-faint transition-colors"
      >
        <Bell className="h-[18px] w-[18px]" strokeWidth={1.5} />
        {hasReminders && (
          <span
            aria-hidden="true"
            className="absolute top-2 right-2 h-2 w-2 rounded-full bg-orb ring-1 ring-base shadow-[0_0_6px_var(--orb)]"
          />
        )}
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="Reminders"
          className="absolute right-0 mt-2 w-80 max-w-[calc(100vw-1.5rem)] rounded-lg border border-edge bg-base/95 p-3 shadow-xl backdrop-blur-sm"
        >
          <div className="mb-2 flex items-center justify-between">
            <div className="font-mono text-helper uppercase tracking-wider text-fg-muted">
              Reminders
            </div>
            {hasReminders && (
              <Button
                size="sm"
                variant="secondary"
                disabled={busy === 'all'}
                onClick={() => void cancelAll()}
              >
                Clear all
              </Button>
            )}
          </div>

          {error && <div className="text-xs text-danger">{error}</div>}

          {items === null ? (
            <div className="text-xs text-fg-subtle">Loading…</div>
          ) : items.length === 0 ? (
            <div className="py-2 text-xs text-fg-subtle">No reminders scheduled.</div>
          ) : (
            <div className="max-h-[60vh] space-y-2 overflow-y-auto">
              {items.map((r) => (
                <div
                  key={r.id}
                  className="flex items-center justify-between gap-2 rounded-md border border-edge px-2.5 py-2"
                >
                  <div className="min-w-0">
                    <div className="truncate text-sm text-fg-body">{r.text}</div>
                    <div className="flex items-center gap-1.5 text-helper text-fg-subtle">
                      {r.recurring && (
                        <Repeat className="h-3 w-3" strokeWidth={1.5} />
                      )}
                      <span>
                        {r.recurring
                          ? `${everyLabel(r.repeat_secs)} · next ${whenLabel(r.fire_at)}`
                          : whenLabel(r.fire_at)}
                      </span>
                    </div>
                  </div>
                  <Button
                    size="icon"
                    variant="ghost"
                    aria-label={`Cancel reminder: ${r.text}`}
                    disabled={busy === r.id}
                    onClick={() => void cancelOne(r.id)}
                  >
                    <Trash2 className="h-4 w-4 text-fg-muted" strokeWidth={1.5} />
                  </Button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
