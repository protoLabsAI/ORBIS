import { useCallback, useEffect, useRef, useState } from 'react';
import { Panel } from '@/components/ui/panel';
import { FieldSlider } from './FieldSlider';
import { FieldColor } from './FieldColor';
import { FieldDeltaSlider } from './FieldDeltaSlider';
import { PresetControls } from './PresetControls';
import { VariantPicker } from './VariantPicker';
import {
  AuthoringContextPicker,
  applySimulation,
  isBase,
  type AuthoringContext,
} from './AuthoringContext';
import {
  flushSave,
  resetBucket,
  setMoodDelta,
  setStateDelta,
} from './overrideWriter';
import {
  SECTIONS,
  formatPresetBlock,
  randomHex,
  randomSliderValue,
  type FieldSpec,
} from '../orb/shared/field-types';
import {
  loadCustomByVariant,
  saveCustomByVariant,
  type CustomPresetMap,
} from '../orb/storage';
import {
  applyParam,
  applyPreset,
  loadCustomPreset,
} from '../orb/broadcast';
import { useActiveVariant, useOrbOverrides, useOrbState } from '../orb/useOrbState';
import { simulationStore } from '../orb/simulationStore';

export function OrbSettingsPanel() {
  const variant = useActiveVariant();
  const { palette, params } = useOrbState();
  const { stateOverrides, moodOverrides } = useOrbOverrides();
  // Saved presets live per-variant — switching variants reloads the
  // map so the dropdown only ever shows entries that fit the active
  // schema. Initialise with an empty map; the variant.id effect
  // below populates it once the active variant resolves.
  const [customMap, setCustomMap] = useState<CustomPresetMap>({});
  const [customName, setCustomName] = useState<string>('');
  const [copyLabel, setCopyLabel] = useState<string>('Copy config');

  // Authoring context + simulation pin. Both live here so the panel
  // owns the editing mode; the composition store doesn't know anything
  // about "which context is active" — it just reads current state/mood.
  const [ctx, setCtx] = useState<AuthoringContext>({ kind: 'base' });
  const [simulate, setSimulate] = useState(false);

  // Release simulation pins on unmount and flush any pending save.
  useEffect(() => () => {
    simulationStore.clear();
    void flushSave();
  }, []);

  // Reload the saved-preset map whenever the active variant changes
  // (also triggers on mount once the variant resolves). Reset the
  // selected name too — a "MyFavorite" on Tetra has no analogue in
  // Nebula's space, so the dropdown should come up empty.
  useEffect(() => {
    if (!variant) return;
    setCustomMap(loadCustomByVariant(variant.id));
    setCustomName('');
  }, [variant?.id]);

  // Debounced save timer — broadcast.applyParam already persists, so this
  // is just a ref holder for any future flush-on-unmount needs.
  const saveTimerRef = useRef<number | null>(null);
  useEffect(() => {
    return () => {
      if (saveTimerRef.current != null) window.clearTimeout(saveTimerRef.current);
    };
  }, []);

  const updateParam = useCallback((key: string, value: unknown) => {
    applyParam(key, value);
  }, []);

  const onPaletteChange = (name: string) => {
    applyPreset(name);
  };

  const onReset = () => {
    applyPreset(palette);
    setCustomName('');
  };

  const onRandomize = () => {
    if (!variant) return;
    for (const spec of variant.fields) {
      const v = spec.kind === 'color' ? randomHex() : randomSliderValue(spec);
      applyParam(spec.key, v);
    }
    setCustomName('');
  };

  const onCopy = async () => {
    if (!variant) return;
    const snippet = formatPresetBlock(variant.fields, params, 'NewPreset');
    let ok = false;
    try {
      await navigator.clipboard.writeText(snippet);
      ok = true;
    } catch {
      const ta = document.createElement('textarea');
      ta.value = snippet;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try { ok = document.execCommand('copy'); } catch {}
      ta.remove();
    }
    setCopyLabel(ok ? 'Copied' : 'Copy failed');
    window.setTimeout(() => setCopyLabel('Copy config'), 1200);
  };

  const onSaveAs = () => {
    if (!variant) return;
    const name = (window.prompt('Save preset as:') ?? '').trim();
    if (!name) return;
    const next: CustomPresetMap = {
      ...customMap,
      [name]: { palette, params: { ...params } },
    };
    setCustomMap(next);
    saveCustomByVariant(variant.id, next);
    setCustomName(name);
  };

  const onLoadCustom = (name: string) => {
    if (!name) return;
    loadCustomPreset(name);
    setCustomName(name);
  };

  const onDeleteCustom = () => {
    if (!variant || !customName) return;
    if (!window.confirm(`Delete preset "${customName}"?`)) return;
    const next = { ...customMap };
    delete next[customName];
    setCustomMap(next);
    saveCustomByVariant(variant.id, next);
    setCustomName('');
  };

  if (!variant) return null;

  const paletteNames = Object.keys(variant.palettes);

  // Resolve the active override bucket for the current context.
  const activeBucket: Record<string, number | string | boolean | undefined> = (() => {
    if (ctx.kind === 'state') return stateOverrides[ctx.state] ?? {};
    if (ctx.kind === 'mood')  return moodOverrides[ctx.dim] ?? {};
    return {};
  })();

  const onStateDelta = useCallback(
    (key: string, delta: number) => {
      if (ctx.kind === 'state') setStateDelta(ctx.state, key, delta);
    },
    [ctx],
  );
  const onMoodDelta = useCallback(
    (key: string, delta: number) => {
      if (ctx.kind === 'mood') setMoodDelta(ctx.dim, key, delta);
    },
    [ctx],
  );
  const onDeltaReset = useCallback(
    (key: string) => {
      if (ctx.kind === 'state') setStateDelta(ctx.state, key, null);
      else if (ctx.kind === 'mood') setMoodDelta(ctx.dim, key, null);
    },
    [ctx],
  );
  const onResetAll = () => {
    if (ctx.kind === 'base') return;
    resetBucket(ctx);
  };

  return (
    <div className="space-y-5">
      <VariantPicker />
      <PresetControls
        palette={palette}
        paletteNames={paletteNames}
        onPaletteChange={onPaletteChange}
        customMap={customMap}
        customName={customName}
        onLoadCustom={onLoadCustom}
        onDeleteCustom={onDeleteCustom}
        onSaveAs={onSaveAs}
        onRandomize={onRandomize}
        onCopy={onCopy}
        onReset={onReset}
        copyLabel={copyLabel}
        disabled={!isBase(ctx)}
      />

      <AuthoringContextPicker
        ctx={ctx}
        onChange={(next) => {
          setCtx(next);
          if (simulate) applySimulation(next, true);
        }}
        simulate={simulate}
        onToggleSimulate={setSimulate}
      />

      {SECTIONS.map((s) => (
        <SettingsSection
          key={s.id}
          title={s.label}
          fields={variant.fields.filter((f) => f.section === s.id)}
          params={params}
          ctx={ctx}
          bucket={activeBucket}
          onBaseChange={updateParam}
          onStateDelta={onStateDelta}
          onMoodDelta={onMoodDelta}
          onDeltaReset={onDeltaReset}
        />
      ))}

      {!isBase(ctx) && (
        <div className="flex items-center justify-end">
          <button
            type="button"
            onClick={onResetAll}
            className="text-[11px] uppercase tracking-wider text-zinc-500 hover:text-zinc-300 transition-colors"
          >
            Reset all deltas for this context
          </button>
        </div>
      )}
    </div>
  );
}

function noop(): void {}

/**
 * Renders a section of fields in either "base" or "delta" mode.
 *
 * - Base mode: standard sliders / color pickers that edit orb.params.
 * - Delta mode: numeric fields show a delta slider keyed against the
 *   active state/mood bucket; color fields are skipped for now
 *   (replacement-semantics authoring UI is a follow-up — PR scope
 *   kept numeric-only to match what the composer reliably applies).
 */
function SettingsSection({
  title,
  fields,
  params,
  ctx,
  bucket,
  onBaseChange,
  onStateDelta,
  onMoodDelta,
  onDeltaReset,
}: {
  title: string;
  fields: FieldSpec[];
  params: Record<string, unknown>;
  ctx: AuthoringContext;
  bucket: Record<string, number | string | boolean | undefined>;
  onBaseChange: (key: string, value: unknown) => void;
  onStateDelta: (key: string, delta: number) => void;
  onMoodDelta: (key: string, delta: number) => void;
  onDeltaReset: (key: string) => void;
}) {
  if (fields.length === 0) return null;
  const baseMode = isBase(ctx);

  return (
    <Panel title={title}>
      {fields.map((f) => {
        if (baseMode) {
          return f.kind === 'color' ? (
            <FieldColor
              key={f.key}
              field={f}
              value={String(params[f.key] ?? '#000000')}
              onChange={(k, v) => onBaseChange(k, v)}
            />
          ) : (
            <FieldSlider
              key={f.key}
              field={f}
              value={Number(params[f.key] ?? f.min)}
              onChange={(k, v) => onBaseChange(k, v)}
            />
          );
        }
        // Delta mode — only numeric sliders today.
        if (f.kind === 'color') {
          return (
            <div key={f.key} className="text-[11px] text-zinc-600 py-1">
              {f.label} — color deltas not editable in this context.
            </div>
          );
        }
        const baseValue = Number(params[f.key] ?? f.min);
        const rawDelta = bucket[f.key];
        const delta = typeof rawDelta === 'number' ? rawDelta : 0;
        // Narrow explicitly so the ctx === 'base' branch can't
        // silently fall through to onMoodDelta if a future refactor
        // reshuffles the `baseMode` guard upstream.
        const onChange =
          ctx.kind === 'state' ? onStateDelta :
          ctx.kind === 'mood'  ? onMoodDelta  :
          /* istanbul ignore next */ noop;
        return (
          <FieldDeltaSlider
            key={f.key}
            field={f}
            baseValue={baseValue}
            delta={delta}
            onChange={onChange}
            onReset={onDeltaReset}
          />
        );
      })}
    </Panel>
  );
}
