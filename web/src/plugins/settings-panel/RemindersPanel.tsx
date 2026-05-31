import { useCallback, useEffect, useState } from 'react';
import { Repeat, Trash2 } from 'lucide-react';
import { Panel } from '@/components/ui/panel';
import { Button } from '@/components/ui/button';
import { api, type ReminderItem } from '@/lib/api';

/**
 * Reminders — see and cancel the time-based reminders the agent has
 * scheduled (one-time and recurring). The agent can also list/cancel by
 * voice (`list_reminders` / `cancel_reminder`); this is the visual surface
 * for the same `/api/reminders` data so a stray recurring reminder can be
 * killed with a click.
 */

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

export function RemindersPanel() {
  const [items, setItems] = useState<ReminderItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | 'all' | null>(null);

  const reload = useCallback(async () => {
    try {
      const r = await api.reminders.list();
      setItems(r.reminders);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

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

  return (
    <Panel title="Reminders">
      {error && <div className="text-xs text-red-400">{error}</div>}
      {items === null ? (
        <div className="text-xs text-zinc-500">Loading…</div>
      ) : items.length === 0 ? (
        <div className="text-xs text-zinc-500">No reminders scheduled.</div>
      ) : (
        <div className="space-y-2">
          {items.map((r) => (
            <div
              key={r.id}
              className="flex items-center justify-between gap-2 rounded-md border border-zinc-800 px-2.5 py-2"
            >
              <div className="min-w-0">
                <div className="truncate text-sm text-zinc-200">{r.text}</div>
                <div className="flex items-center gap-1.5 text-[11px] text-zinc-500">
                  {r.recurring && <Repeat className="h-3 w-3" strokeWidth={1.5} />}
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
                <Trash2 className="h-4 w-4 text-zinc-400" strokeWidth={1.5} />
              </Button>
            </div>
          ))}
          <div className="flex justify-end pt-1">
            <Button
              size="sm"
              variant="secondary"
              disabled={busy === 'all'}
              onClick={() => void cancelAll()}
            >
              Clear all
            </Button>
          </div>
        </div>
      )}
    </Panel>
  );
}
