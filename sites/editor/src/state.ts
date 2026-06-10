/**
 * Editor store. One definition under edit + the live preview inputs
 * (params, simulated voice state / levels / mood). Same minimal
 * useSyncExternalStore pattern the app uses — no state library.
 *
 * `definition` is STRUCTURE (shader, uniforms, fields, bindings…):
 * changing it remounts the preview material. `params` are VALUES the
 * authored controls produce: changing them only writes uniforms, so
 * slider scrubbing never recompiles the shader.
 */

import type { OrbDefinition, Mood, ParamMap, VoiceState } from '@orbis/orb-runtime';

export type LevelMode = 'off' | 'pulse' | 'manual' | 'mic';

export interface SimState {
  voiceState: VoiceState;
  botMode: LevelMode; // bot = her TTS voice (mic not applicable)
  userMode: LevelMode; // user = you (mic available — this is a website)
  botManual: number;
  userManual: number;
  mood: Mood;
}

export interface EditorSnapshot {
  definition: OrbDefinition;
  params: ParamMap;
  palette: string;
  sim: SimState;
  /** Shader compile diagnostics from the last attempt ('' = clean). */
  shaderLog: string;
  epoch: number;
}

const DRAFT_KEY = 'orbis-editor.draft.v1';

type Listener = () => void;

function paletteParams(def: OrbDefinition, palette: string): ParamMap {
  return { ...(def.palettes[palette] ?? {}) } as ParamMap;
}

class EditorStore {
  private snap: EditorSnapshot;
  private listeners = new Set<Listener>();
  private saveTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(initial: OrbDefinition) {
    const def = loadDraft() ?? initial;
    const palette = def.defaultPalette;
    this.snap = {
      definition: def,
      params: paletteParams(def, palette),
      palette,
      sim: {
        voiceState: 'speaking',
        botMode: 'pulse',
        userMode: 'off',
        botManual: 0.5,
        userManual: 0.5,
        mood: { valence: 0, arousal: 0, guardedness: 0 },
      },
      shaderLog: '',
      epoch: 0,
    };
  }

  getSnapshot = (): EditorSnapshot => this.snap;

  subscribe = (l: Listener): (() => void) => {
    this.listeners.add(l);
    return () => {
      this.listeners.delete(l);
    };
  };

  private commit(next: Partial<EditorSnapshot>, persist = false): void {
    this.snap = { ...this.snap, ...next, epoch: this.snap.epoch + 1 };
    this.listeners.forEach((l) => l());
    if (persist) this.scheduleDraftSave();
  }

  private scheduleDraftSave(): void {
    if (this.saveTimer) clearTimeout(this.saveTimer);
    this.saveTimer = setTimeout(() => {
      try {
        localStorage.setItem(DRAFT_KEY, JSON.stringify(this.snap.definition));
      } catch {
        /* quota / private mode — drafts are best-effort */
      }
    }, 800);
  }

  /** Structural edit — preview material remounts. */
  updateDefinition(fn: (def: OrbDefinition) => OrbDefinition): void {
    const definition = fn(this.snap.definition);
    // Keep params/palette coherent if palettes changed under us.
    const palette = definition.palettes[this.snap.palette]
      ? this.snap.palette
      : definition.defaultPalette;
    this.commit({ definition, palette }, true);
  }

  /** Replace the whole definition (template pick / file import). */
  loadDefinition(def: OrbDefinition): void {
    this.commit(
      {
        definition: def,
        palette: def.defaultPalette,
        params: paletteParams(def, def.defaultPalette),
        shaderLog: '',
      },
      true,
    );
  }

  setParam(key: string, value: number | string): void {
    this.commit({ params: { ...this.snap.params, [key]: value } });
  }

  setPalette(name: string): void {
    this.commit({
      palette: name,
      params: paletteParams(this.snap.definition, name),
    });
  }

  /** Snapshot the current params into a (new or existing) palette. */
  savePalette(name: string): void {
    const def = this.snap.definition;
    this.updateDefinition(() => ({
      ...def,
      palettes: {
        ...def.palettes,
        [name]: { ...(this.snap.params as Record<string, number | string>) },
      },
    }));
    this.commit({ palette: name });
  }

  setSim(patch: Partial<SimState>): void {
    this.commit({ sim: { ...this.snap.sim, ...patch } });
  }

  setMood(patch: Partial<Mood>): void {
    this.commit({ sim: { ...this.snap.sim, mood: { ...this.snap.sim.mood, ...patch } } });
  }

  setShaderLog(log: string): void {
    if (log === this.snap.shaderLog) return;
    this.commit({ shaderLog: log });
  }

  clearDraft(): void {
    try {
      localStorage.removeItem(DRAFT_KEY);
    } catch {
      /* best-effort */
    }
  }
}

function loadDraft(): OrbDefinition | null {
  try {
    const raw = localStorage.getItem(DRAFT_KEY);
    return raw ? (JSON.parse(raw) as OrbDefinition) : null;
  } catch {
    return null;
  }
}

let _store: EditorStore | null = null;

export function initStore(initial: OrbDefinition): EditorStore {
  if (!_store) _store = new EditorStore(initial);
  return _store;
}

export function store(): EditorStore {
  if (!_store) throw new Error('editor store not initialized');
  return _store;
}
