import { Panel } from '@/components/ui/panel';
import { Button } from '@/components/ui/button';
import { logBus, useLogBus, type LogEvent, type LogSource } from '@/shared/logBus';

const SOURCE_COLOR: Record<LogSource, string> = {
  rtvi: 'text-emerald-400/80',
  fetch: 'text-sky-400/80',
  webrtc: 'text-amber-400/80',
  voice: 'text-violet-400/80',
};

const LEVEL_COLOR = {
  debug: 'text-zinc-600',
  info: 'text-zinc-300',
  warn: 'text-amber-400',
  error: 'text-red-400',
};

const fmtTs = (ms: number): string => {
  // HH:MM:SS.mmm — local time, monospace-friendly. Keeps the lines
  // narrow so the message column gets most of the width.
  const d = new Date(ms);
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  const ss = String(d.getSeconds()).padStart(2, '0');
  const mmm = String(d.getMilliseconds()).padStart(3, '0');
  return `${hh}:${mm}:${ss}.${mmm}`;
};

/**
 * Live tail of client/server events — RTVI frames, REST calls, WebRTC
 * state. New entries appear at the top so the user doesn't have to
 * chase the bottom of a scrolling div. Bounded ring buffer (500 in
 * shared/logBus) so a long session can't blow memory.
 */
export function LogsPanel() {
  const events = useLogBus();

  return (
    <Panel title="Events">
      <div className="flex items-center justify-between -mt-1 mb-2">
        <span className="text-[10px] text-zinc-600 font-mono">
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
      <div className="font-mono text-[10px] max-h-[60vh] overflow-y-auto rounded-md border border-zinc-800 bg-zinc-950">
        {events.length === 0 ? (
          <div className="text-zinc-600 p-3">
            No events yet. Start a voice session to see RTVI frames flow
            through here.
          </div>
        ) : (
          events.slice().reverse().map((e: LogEvent, i: number) => (
            <div
              key={`${e.ts}-${i}`}
              className="flex gap-2 px-2 py-1 border-b border-zinc-900/60 last:border-b-0"
            >
              <span className="text-zinc-600 shrink-0">{fmtTs(e.ts)}</span>
              <span className={`shrink-0 w-12 ${SOURCE_COLOR[e.source]}`}>
                {e.source}
              </span>
              <span className={`break-all ${LEVEL_COLOR[e.level]}`}>
                {e.message}
              </span>
            </div>
          ))
        )}
      </div>
    </Panel>
  );
}
