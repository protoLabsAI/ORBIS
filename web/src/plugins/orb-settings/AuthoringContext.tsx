/**
 * Authoring-context selector — picks whether the field sliders in the
 * Orb panel edit `base` params or per-(state|mood) deltas.
 *
 * Shape lives in `ctx`:
 *   { kind: 'base' }
 *   { kind: 'state', state: 'idle' | 'listening' | 'thinking' | 'speaking' }
 *   { kind: 'mood',  dim:   'valence' | 'arousal' | 'guardedness' }
 */

import { Panel } from '@/components/ui/panel';
import { Button } from '@/components/ui/button';
import { simulationStore } from '../orb/simulationStore';
import type { VoiceState } from '@/voice/state';

export type MoodDim = 'valence' | 'arousal' | 'guardedness';

export type AuthoringContext =
  | { kind: 'base' }
  | { kind: 'state'; state: VoiceState }
  | { kind: 'mood'; dim: MoodDim };

const STATES: VoiceState[] = ['idle', 'listening', 'thinking', 'speaking'];
const DIMS: MoodDim[] = ['valence', 'arousal', 'guardedness'];

export function isBase(c: AuthoringContext): boolean {
  return c.kind === 'base';
}

export function contextLabel(c: AuthoringContext): string {
  if (c.kind === 'base') return 'Base';
  if (c.kind === 'state') return cap(c.state);
  return cap(c.dim);
}

function cap(s: string): string {
  return s.slice(0, 1).toUpperCase() + s.slice(1);
}

interface Props {
  ctx: AuthoringContext;
  onChange: (ctx: AuthoringContext) => void;
  simulate: boolean;
  onToggleSimulate: (next: boolean) => void;
}

export function AuthoringContextPicker({
  ctx, onChange, simulate, onToggleSimulate,
}: Props) {
  const isActive = (candidate: AuthoringContext): boolean => {
    if (candidate.kind !== ctx.kind) return false;
    if (candidate.kind === 'base') return true;
    if (candidate.kind === 'state' && ctx.kind === 'state') {
      return candidate.state === ctx.state;
    }
    if (candidate.kind === 'mood' && ctx.kind === 'mood') {
      return candidate.dim === ctx.dim;
    }
    return false;
  };

  const pick = (next: AuthoringContext) => {
    onChange(next);
    // If simulate is on, re-pin immediately so the orb reflects the
    // new context without waiting for the user to re-toggle.
    if (simulate) applySimulation(next, true);
  };

  return (
    <Panel title="Authoring">
      {/* Single radiogroup — ctx is one mutually-exclusive selection
          across all three visual sections. Nested radiogroups
          mis-describe the actual behavior to screen readers. */}
      <div role="radiogroup" aria-label="Authoring context" className="space-y-3">
        <div>
          <div
            id="authoring-section-base"
            className="text-[11px] uppercase tracking-wider text-zinc-500 mb-1.5"
          >
            Base + live values
          </div>
          <div
            className="flex flex-wrap gap-1.5"
            aria-labelledby="authoring-section-base"
          >
            <Chip active={isActive({ kind: 'base' })} onClick={() => pick({ kind: 'base' })}>
              Base
            </Chip>
          </div>
        </div>

        <div>
          <div
            id="authoring-section-state"
            className="text-[11px] uppercase tracking-wider text-zinc-500 mb-1.5"
          >
            State overrides
          </div>
          <div
            className="flex flex-wrap gap-1.5"
            aria-labelledby="authoring-section-state"
          >
            {STATES.map((s) => (
              <Chip
                key={s}
                active={isActive({ kind: 'state', state: s })}
                onClick={() => pick({ kind: 'state', state: s })}
              >
                {cap(s)}
              </Chip>
            ))}
          </div>
        </div>

        <div>
          <div
            id="authoring-section-mood"
            className="text-[11px] uppercase tracking-wider text-zinc-500 mb-1.5"
          >
            Mood overrides
          </div>
          <div
            className="flex flex-wrap gap-1.5"
            aria-labelledby="authoring-section-mood"
          >
            {DIMS.map((d) => (
              <Chip
                key={d}
                active={isActive({ kind: 'mood', dim: d })}
                onClick={() => pick({ kind: 'mood', dim: d })}
              >
                {cap(d)}
              </Chip>
            ))}
          </div>
        </div>

        {!isBase(ctx) && (
          <div className="flex items-center justify-between pt-1">
            <div className="text-[11px] text-zinc-500">
              Simulate{' '}
              <span className="text-zinc-400">{contextLabel(ctx)}</span>
              {' '}so you can see the delta without waiting.
            </div>
            <Button
              variant={simulate ? 'default' : 'secondary'}
              size="sm"
              onClick={() => {
                const next = !simulate;
                onToggleSimulate(next);
                applySimulation(ctx, next);
              }}
            >
              {simulate ? 'Simulating' : 'Simulate'}
            </Button>
          </div>
        )}
      </div>
    </Panel>
  );
}

/** Project the authoring context onto the simulation store. `on=false`
 * releases any pin. Exported so the panel can clear on unmount. */
export function applySimulation(ctx: AuthoringContext, on: boolean): void {
  if (!on || ctx.kind === 'base') {
    simulationStore.clear();
    return;
  }
  if (ctx.kind === 'state') {
    simulationStore.setPinnedState(ctx.state);
    simulationStore.setPinnedMood(null);
    return;
  }
  // mood: pin the selected dim to +1 (max magnitude), others to 0. The
  // composer multiplies mood deltas by the live value, so +1 shows
  // the authored delta at full strength.
  const pinned = { valence: 0, arousal: 0, guardedness: 0 };
  pinned[ctx.dim] = 1;
  simulationStore.setPinnedMood(pinned);
  simulationStore.setPinnedState(null);
}

function Chip({
  active, onClick, children,
}: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={active}
      onClick={onClick}
      className={
        'px-2.5 py-1 rounded-md text-xs transition-colors border ' +
        (active
          ? 'bg-amber-500/15 border-amber-500/50 text-amber-200'
          : 'bg-zinc-900/40 border-zinc-800 text-zinc-300 hover:bg-zinc-900/70')
      }
    >
      {children}
    </button>
  );
}
