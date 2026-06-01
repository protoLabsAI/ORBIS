import { Panel } from '@/components/ui/panel';
import { Button } from '@/components/ui/button';
import { logBus, useLogBus, type LogEvent, type LogSource } from '@/shared/logBus';

const SOURCE_COLOR: Record<LogSource, string> = {
  api: 'text-sky-400/80',
  sse: 'text-success/80',
  voice: 'text-violet-400/80',
};

const LEVEL_COLOR = {
  debug: 'text-fg-faint',
  info: 'text-fg-body',
  warn: 'text-brand',
  error: 'text-danger',
};

const fmtTs = (ms: number): string => {
  // HH:MM:SS.mmm: compact and monospace-friendly.
  const d = new Date(ms);
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  const ss = String(d.getSeconds()).padStart(2, '0');
  const mmm = String(d.getMilliseconds()).padStart(3, '0');
  return `${hh}:${mm}:${ss}.${mmm}`;
};

export function LogsPanel() {
  const events = useLogBus();

  return (
    <Panel title="Events">
      <div className="-mt-1 mb-2 flex items-center justify-between">
        <span className="font-mono text-helper text-fg-faint">
          {events.length} / 500 entries
        </span>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => logBus.clear()}
          disabled={events.length === 0}
        >
          Clear
        </Button>
      </div>
      <div className="max-h-[60vh] overflow-y-auto rounded-md border border-edge bg-surface font-mono text-helper">
        {events.length === 0 ? (
          <div className="p-3 text-fg-faint">
            No events yet. Start a native voice session to see SSE and voice
            state flow through here.
          </div>
        ) : (
          events.slice().reverse().map((event: LogEvent, i: number) => (
            <div
              key={`${event.ts}-${i}`}
              className="flex gap-2 border-b border-raised/60 px-2 py-1 last:border-b-0"
            >
              <span className="shrink-0 text-fg-faint">{fmtTs(event.ts)}</span>
              <span className={`w-12 shrink-0 ${SOURCE_COLOR[event.source]}`}>
                {event.source}
              </span>
              <span className={`break-all ${LEVEL_COLOR[event.level]}`}>
                {event.message}
              </span>
            </div>
          ))
        )}
      </div>
    </Panel>
  );
}
