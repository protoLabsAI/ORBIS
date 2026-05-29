import { Panel } from '@/components/ui/panel';
import { Button } from '@/components/ui/button';
import { logBus, useLogBus, type LogEvent, type LogSource } from '@/shared/logBus';

const SOURCE_COLOR: Record<LogSource, string> = {
  api: 'text-sky-400/80',
  sse: 'text-emerald-400/80',
  voice: 'text-violet-400/80',
};

const LEVEL_COLOR = {
  debug: 'text-zinc-600',
  info: 'text-zinc-300',
  warn: 'text-amber-400',
  error: 'text-red-400',
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
        <span className="font-mono text-[10px] text-zinc-600">
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
      <div className="max-h-[60vh] overflow-y-auto rounded-md border border-zinc-800 bg-zinc-950 font-mono text-[10px]">
        {events.length === 0 ? (
          <div className="p-3 text-zinc-600">
            No events yet. Start a native voice session to see SSE and voice
            state flow through here.
          </div>
        ) : (
          events.slice().reverse().map((event: LogEvent, i: number) => (
            <div
              key={`${event.ts}-${i}`}
              className="flex gap-2 border-b border-zinc-900/60 px-2 py-1 last:border-b-0"
            >
              <span className="shrink-0 text-zinc-600">{fmtTs(event.ts)}</span>
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
