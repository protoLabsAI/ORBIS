import { useCallback, useEffect, useState } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { CollapsiblePanelProvider, Panel } from '@/components/ui/panel';
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
  commitOrbNow,
  commitParam,
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

  // Live (debounced) updates during a slider/color drag.
  const updateParam = useCallback((key: string, value: unknown) => {
    commitParam(key, value);
  }, []);
  // Commit edge of a base edit — slider release / color blur. Flushes
  // the pending debounced write immediately so a reload can't beat it.
  const commitBase = useCallback(() => {
    commitOrbNow();
  }, []);

  const onPaletteChange = (name: string) => {
    applyPreset(name);
    commitOrbNow();
  };

  const onReset = () => {
    applyPreset(palette);
    commitOrbNow();
    setCustomName('');
  };

  const onRandomize = () => {
    if (!variant) return;
    for (const spec of variant.fields) {
      // Skip resolution/dpr — it's a performance dial the user has
      // tuned for their device, not a creative knob. Randomizing it
      // would land them on a 0.1× pixelated mess or a 2.0× thermal
      // hotbox without warning.
      if (spec.key === 'dpr') continue;
      const v = spec.kind === 'color' ? randomHex() : randomSliderValue(spec);
      applyParam(spec.key, v);
    }
    commitOrbNow();
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
    commitOrbNow();
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
    <CollapsiblePanelProvider storageKey="orbis.orbPanel">
      <div className="space-y-5">
      {/* Authoring your own orb (shader/controls/bindings) lives in the full
          editor on the marketing site — route out rather than duplicating it
          in-app for now. */}
      <button
        type="button"
        onClick={() =>
          invoke('open_url', { url: 'https://orbis.protolabs.studio/editor/' }).catch(() => {})
        }
        className="text-left text-helper text-brand/80 underline-offset-2 hover:text-brand hover:underline"
      >
        Build your own orb in the full editor →
      </button>
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
          onBaseCommit={commitBase}
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
            className="text-helper uppercase tracking-wider text-fg-subtle hover:text-fg-body transition-colors"
          >
            Reset all deltas for this context
          </button>
        </div>
      )}

      </div>
    </CollapsiblePanelProvider>
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
  onBaseCommit,
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
  onBaseCommit: () => void;
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
              onCommit={onBaseCommit}
            />
          ) : (
            <FieldSlider
              key={f.key}
              field={f}
              value={Number(params[f.key] ?? f.min)}
              onChange={(k, v) => onBaseChange(k, v)}
              onCommit={onBaseCommit}
            />
          );
        }
        // Delta mode — only numeric sliders today.
        if (f.kind === 'color') {
          return (
            <div key={f.key} className="text-helper text-fg-faint py-1">
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
