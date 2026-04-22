import { useCallback, useEffect, useRef, useState } from 'react';
import { Panel } from '@/components/ui/panel';
import { FieldSlider } from './FieldSlider';
import { FieldColor } from './FieldColor';
import { PresetControls } from './PresetControls';
import { VariantPicker } from './VariantPicker';
import {
  SECTIONS,
  formatPresetBlock,
  randomHex,
  randomSliderValue,
  type FieldSpec,
} from '../orb/shared/field-types';
import {
  loadCustom,
  saveCustom,
  type CustomPresetMap,
} from '../orb/storage';
import {
  applyParam,
  applyPreset,
  loadCustomPreset,
} from '../orb/broadcast';
import { useActiveVariant, useOrbState } from '../orb/useOrbState';

export function OrbSettingsPanel() {
  const variant = useActiveVariant();
  const { palette, params } = useOrbState();
  const [customMap, setCustomMap] = useState<CustomPresetMap>(() => loadCustom());
  const [customName, setCustomName] = useState<string>('');
  const [copyLabel, setCopyLabel] = useState<string>('Copy config');

  // Refresh custom presets if storage changes in another tab / on mount.
  useEffect(() => {
    setCustomMap(loadCustom());
  }, []);

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
    const name = (window.prompt('Save preset as:') ?? '').trim();
    if (!name) return;
    const next: CustomPresetMap = {
      ...customMap,
      [name]: { palette, params: { ...params } },
    };
    setCustomMap(next);
    saveCustom(next);
    setCustomName(name);
  };

  const onLoadCustom = (name: string) => {
    if (!name) return;
    loadCustomPreset(name);
    setCustomName(name);
  };

  const onDeleteCustom = () => {
    if (!customName) return;
    if (!window.confirm(`Delete preset "${customName}"?`)) return;
    const next = { ...customMap };
    delete next[customName];
    setCustomMap(next);
    saveCustom(next);
    setCustomName('');
  };

  if (!variant) return null;

  const paletteNames = Object.keys(variant.palettes);

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
        disabled={false}
      />

      {SECTIONS.map((s) => (
        <SettingsSection
          key={s.id}
          title={s.label}
          fields={variant.fields.filter((f) => f.section === s.id)}
          params={params}
          onChange={updateParam}
        />
      ))}
    </div>
  );
}

function SettingsSection({
  title,
  fields,
  params,
  onChange,
}: {
  title: string;
  fields: FieldSpec[];
  params: Record<string, unknown>;
  onChange: (key: string, value: unknown) => void;
}) {
  if (fields.length === 0) return null;
  return (
    <Panel title={title}>
      {fields.map((f) =>
        f.kind === 'color' ? (
          <FieldColor
            key={f.key}
            field={f}
            value={String(params[f.key] ?? '#000000')}
            onChange={(k, v) => onChange(k, v)}
          />
        ) : (
          <FieldSlider
            key={f.key}
            field={f}
            value={Number(params[f.key] ?? f.min)}
            onChange={(k, v) => onChange(k, v)}
          />
        ),
      )}
    </Panel>
  );
}
