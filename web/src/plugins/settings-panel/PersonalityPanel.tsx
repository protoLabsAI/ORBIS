import { useEffect, useState } from 'react';
import { Panel } from '@/components/ui/panel';
import { api, type PersonalityState } from '@/lib/api';

/**
 * Read-only Profile surface: mood, personality axes, recent drift,
 * session stats. The implicit personality (voice + behavior + orb
 * visuals) stays primary — this panel is for users who want to peek
 * at what's driving the orb's current tone.
 *
 * Refreshes on mount + every 60s while the drawer is open. Not
 * live — the orb's personality drifts on the order of days, so
 * sub-minute polling isn't needed.
 */

// Human-readable axis endpoints for the UI. Keep in sync with
// agent/personality.py::_AXIS_DESCRIPTIONS.
const AXIS_LABELS: Record<string, [string, string]> = {
  playful_serious: ['serious', 'playful'],
  warm_guarded: ['guarded', 'warm'],
  sarcastic_sincere: ['sincere', 'sarcastic'],
  verbose_terse: ['terse', 'verbose'],
  hopeful_cynical: ['cynical', 'hopeful'],
  grandiose_grounded: ['grounded', 'grandiose'],
  probing_incurious: ['incurious', 'probing'],
  philosophical_pragmatic: ['pragmatic', 'philosophical'],
  independent_clingy: ['clingy', 'independent'],
  curious_bored: ['bored', 'curious'],
};

function moodDescriptor(mood: PersonalityState['mood']): string {
  if (!mood) return 'neutral';
  const parts: string[] = [];
  if (Math.abs(mood.valence) >= 0.15) parts.push(mood.valence > 0 ? 'bright' : 'low');
  if (Math.abs(mood.arousal) >= 0.15) parts.push(mood.arousal > 0 ? 'energetic' : 'sleepy');
  if (mood.guardedness > 0.15) parts.push('guarded');
  return parts.length ? parts.join(', ') : 'neutral';
}

function formatDaysAgo(iso: string | null): string {
  if (!iso) return 'never';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return 'never';
  const days = (Date.now() - then) / 86_400_000;
  if (days < 0.04) return 'just now';
  if (days < 1) return `${Math.round(days * 24)}h ago`;
  if (days < 30) return `${Math.round(days)}d ago`;
  return new Date(iso).toLocaleDateString();
}

export function PersonalityPanel() {
  const [state, setState] = useState<PersonalityState | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    try {
      const data = await api.personality();
      setState(data);
      setError(null);
    } catch (e) {
      setError(String((e as Error).message ?? e));
    }
  };

  useEffect(() => {
    load();
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, []);

  if (error) {
    return (
      <Panel title="Profile">
        <div className="text-xs text-red-400">{error}</div>
      </Panel>
    );
  }
  if (!state) {
    return (
      <Panel title="Profile">
        <div className="text-xs text-zinc-500">Loading…</div>
      </Panel>
    );
  }

  const prominentAxes = state.axes
    .filter((a) => Math.abs(a.value) >= 0.15)
    .sort((a, b) => Math.abs(b.value) - Math.abs(a.value))
    .slice(0, 6);

  return (
    <Panel title="Profile">
      <div className="space-y-4">
        <div>
          <div className="text-xs uppercase tracking-wider text-zinc-500 mb-1">Mood</div>
          <div className="text-sm text-zinc-200 capitalize">
            {moodDescriptor(state.mood)}
          </div>
        </div>

        <div>
          <div className="text-xs uppercase tracking-wider text-zinc-500 mb-1">
            Temperament
          </div>
          {prominentAxes.length === 0 ? (
            <div className="text-sm text-zinc-500">Still forming.</div>
          ) : (
            <ul className="space-y-1.5">
              {prominentAxes.map((a) => {
                const [neg, pos] = AXIS_LABELS[a.axis] ?? ['—', '—'];
                const adj = a.value > 0 ? pos : neg;
                const magnitude = Math.abs(a.value);
                const qualifier =
                  magnitude < 0.4 ? 'slightly ' : magnitude < 0.75 ? '' : 'strongly ';
                return (
                  <li key={a.axis} className="flex items-center justify-between text-sm">
                    <span className="text-zinc-300">
                      {qualifier}
                      {adj}
                    </span>
                    <AxisBar value={a.value} />
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <div>
          <div className="text-xs uppercase tracking-wider text-zinc-500 mb-1">
            Sessions
          </div>
          <div className="text-sm text-zinc-300">
            {state.sessions.count} total · last {formatDaysAgo(state.sessions.last_ended_at)}
          </div>
        </div>

        {state.recent_events.length > 0 && (
          <div>
            <div className="text-xs uppercase tracking-wider text-zinc-500 mb-1">
              Recent drift
            </div>
            <ul className="space-y-1 text-xs text-zinc-400 max-h-32 overflow-y-auto">
              {state.recent_events.slice(0, 8).map((e, i) => (
                <li key={i} className="flex items-center justify-between">
                  <span>
                    {e.axis} {e.delta >= 0 ? '+' : ''}
                    {e.delta.toFixed(2)}
                  </span>
                  <span className="text-zinc-600 truncate ml-2 max-w-[60%]">
                    {e.reason || '—'}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </Panel>
  );
}

function AxisBar({ value }: { value: number }) {
  // A thin symmetric bar centered at 0; fills left for negative, right for positive.
  const magnitude = Math.min(1, Math.abs(value));
  const left = value < 0;
  return (
    <div className="relative w-24 h-1.5 rounded-full bg-zinc-800/80">
      <div className="absolute top-0 bottom-0 left-1/2 w-px bg-zinc-700" />
      <div
        className={`absolute top-0 bottom-0 rounded-full ${left ? 'bg-sky-500/70' : 'bg-amber-500/70'}`}
        style={{
          left: left ? `${50 - magnitude * 50}%` : '50%',
          right: left ? '50%' : `${50 - magnitude * 50}%`,
        }}
      />
    </div>
  );
}
