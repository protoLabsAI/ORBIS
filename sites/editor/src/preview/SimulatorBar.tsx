import { useSyncExternalStore } from 'react';
import type { VoiceState } from '@orbis/orb-runtime';
import { store, type LevelMode } from '@orbis/editor-ui';

const STATES: VoiceState[] = ['idle', 'listening', 'thinking', 'speaking'];
const BOT_MODES: LevelMode[] = ['off', 'pulse', 'manual'];
const USER_MODES: LevelMode[] = ['off', 'pulse', 'manual', 'mic'];
const MOOD_DIMS = ['valence', 'arousal', 'guardedness'] as const;

/** Voice-state scrubber + level sources + mood pins. Drives the preview
 * with the same signals the app feeds the engine. */
export function SimulatorBar() {
  const snap = useSyncExternalStore(store().subscribe, store().getSnapshot, store().getSnapshot);
  const { sim } = snap;

  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-edge bg-panel px-4 py-2.5 text-xs">
      <label className="flex items-center gap-2">
        <span className="text-fg-subtle">state</span>
        <div className="flex overflow-hidden rounded-md border border-edge">
          {STATES.map((s) => (
            <button
              key={s}
              onClick={() => store().setSim({ voiceState: s })}
              className={
                'px-2 py-1 transition-colors ' +
                (sim.voiceState === s
                  ? 'bg-brand-deep text-white'
                  : 'text-fg-subtle hover:text-fg')
              }
            >
              {s}
            </button>
          ))}
        </div>
      </label>

      <LevelControl
        label="her voice"
        modes={BOT_MODES}
        mode={sim.botMode}
        manual={sim.botManual}
        onMode={(botMode) => store().setSim({ botMode })}
        onManual={(botManual) => store().setSim({ botManual })}
      />
      <LevelControl
        label="your voice"
        modes={USER_MODES}
        mode={sim.userMode}
        manual={sim.userManual}
        onMode={(userMode) => store().setSim({ userMode })}
        onManual={(userManual) => store().setSim({ userManual })}
      />

      <div className="flex items-center gap-3">
        {MOOD_DIMS.map((dim) => (
          <label key={dim} className="flex items-center gap-1.5">
            <span className="text-fg-subtle">{dim}</span>
            <input
              type="range"
              min={dim === 'guardedness' ? 0 : -1}
              max={1}
              step={0.05}
              value={sim.mood[dim]}
              onChange={(e) => store().setMood({ [dim]: Number(e.target.value) })}
              className="w-20 accent-(--color-brand)"
            />
          </label>
        ))}
      </div>
    </div>
  );
}

function LevelControl(props: {
  label: string;
  modes: LevelMode[];
  mode: LevelMode;
  manual: number;
  onMode: (m: LevelMode) => void;
  onManual: (v: number) => void;
}) {
  return (
    <label className="flex items-center gap-2">
      <span className="text-fg-subtle">{props.label}</span>
      <select
        value={props.mode}
        onChange={(e) => props.onMode(e.target.value as LevelMode)}
        className="rounded-md border border-edge bg-bg px-1.5 py-1"
      >
        {props.modes.map((m) => (
          <option key={m} value={m}>
            {m}
          </option>
        ))}
      </select>
      {props.mode === 'manual' && (
        <input
          type="range"
          min={0}
          max={1}
          step={0.02}
          value={props.manual}
          onChange={(e) => props.onManual(Number(e.target.value))}
          className="w-20 accent-(--color-brand)"
        />
      )}
    </label>
  );
}
