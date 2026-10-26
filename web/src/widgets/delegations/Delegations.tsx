import { useEffect, useState, useSyncExternalStore } from 'react';
import { api } from '@/lib/api';
import { useVoiceStateSelector } from '@/voice/hooks';
import {
  type DelegationStatus,
  type DelegationTask,
  delegationTasks,
  isLive,
} from '@/voice/delegationTasks';

/**
 * Delegations — ambient readout of work handed to other agents: who, what,
 * how it's going, how long. No inputs; voice drives it ("what's ava working
 * on"). Live rows come from the SSE-fed task store; a snapshot from
 * GET /api/delegations hydrates it on open and after a reconnect.
 */

const LABEL: Record<DelegationStatus, string> = {
  working: 'working',
  input: 'needs input',
  done: 'done',
  failed: 'failed',
  cancelled: 'cancelled',
};

const TONE: Record<DelegationStatus, string> = {
  working: 'text-fg-body',
  input: 'text-brand',
  done: 'text-success',
  failed: 'text-danger',
  cancelled: 'text-fg-faint',
};

const MAX_ROWS = 6;

function formatElapsed(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, '0')}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`;
}

function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(id);
  }, [active]);
  return now;
}

export function Delegations() {
  const tasks = useSyncExternalStore(delegationTasks.subscribe, delegationTasks.getSnapshot);
  const connected = useVoiceStateSelector((s) => s.connected);
  const anyLive = tasks.some((t) => isLive(t.status));
  const now = useNow(anyLive);

  // Rows we learned of over SSE carry no query; the DB snapshot does.
  const missingQuery = tasks.filter((t) => !t.query).length;

  useEffect(() => {
    if (!connected) return;
    let alive = true;
    // The registry row is written within tens of ms of first sighting; a short
    // delay lets a just-started task be there when we ask.
    const id = window.setTimeout(() => {
      api.delegations().then(
        (r) => { if (alive) delegationTasks.hydrate(r.tasks ?? []); },
        () => { /* ambient widget: stay quiet, SSE rows still show */ },
      );
    }, missingQuery > 0 ? 600 : 0);
    return () => {
      alive = false;
      window.clearTimeout(id);
    };
  }, [connected, missingQuery]);

  const rows = [...tasks]
    .sort((a, b) => Number(isLive(b.status)) - Number(isLive(a.status)) || b.startedAt - a.startedAt)
    .slice(0, MAX_ROWS);

  if (rows.length === 0) {
    return <div className="text-helper text-fg-faint">no delegations</div>;
  }

  return (
    <ul className="flex w-64 flex-col gap-2.5">
      {rows.map((t) => <Row key={t.key} task={t} now={now} />)}
    </ul>
  );
}

function Row({ task, now }: { task: DelegationTask; now: number }) {
  const live = isLive(task.status);
  const elapsed = formatElapsed((task.endedAt ?? now) - task.startedAt);
  return (
    <li className="min-w-0 leading-tight">
      <div className="flex items-baseline justify-between gap-2">
        <span className="truncate text-label text-fg-body">{task.delegate}</span>
        <span className={`shrink-0 text-helper tabular-nums ${TONE[task.status]} ${live ? 'animate-pulse' : ''}`}>
          {LABEL[task.status]} · {elapsed}
        </span>
      </div>
      {task.query && <div className="truncate text-helper text-fg-subtle">{task.query}</div>}
      {task.progress && <div className="truncate text-helper text-fg-faint">{task.progress}</div>}
    </li>
  );
}
