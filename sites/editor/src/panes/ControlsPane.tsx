import { useState, useSyncExternalStore } from 'react';
import type { FieldSpec, SectionId, UniformType } from '@orbis/orb-runtime';
import { store } from '../state';

const SECTIONS: SectionId[] = ['color', 'energy', 'motion', 'fractal', 'perf'];

/**
 * Live controls — exactly what the app's settings panel will render for
 * this orb (same FieldSpec schema), driving the preview params. Plus
 * palette management and the one-step "Add control" flow (uniform +
 * field + param binding in a single action — the common authoring move).
 */
export function ControlsPane() {
  const snap = useSyncExternalStore(store().subscribe, store().getSnapshot, store().getSnapshot);
  const { definition: def, params, palette } = snap;

  return (
    <div className="flex h-full flex-col gap-4 overflow-auto p-4 text-sm">
      <div className="flex items-center gap-2">
        <span className="text-xs text-fg-subtle">palette</span>
        <select
          value={palette}
          onChange={(e) => store().setPalette(e.target.value)}
          className="rounded-md border border-edge bg-bg px-2 py-1 text-xs"
        >
          {Object.keys(def.palettes).map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
        <button
          className="rounded-md border border-edge px-2 py-1 text-xs text-fg-subtle hover:text-fg"
          onClick={() => store().savePalette(palette)}
        >
          Save params → "{palette}"
        </button>
        <SaveAsPalette />
      </div>

      {SECTIONS.map((section) => {
        const fields = def.fields.filter((f) => f.section === section);
        if (!fields.length) return null;
        return (
          <section key={section}>
            <h3 className="mb-2 text-xs uppercase tracking-wider text-fg-subtle">{section}</h3>
            <div className="flex flex-col gap-2">
              {fields.map((f) => (
                <FieldRow key={f.key} field={f} value={params[f.key]} />
              ))}
            </div>
          </section>
        );
      })}

      <AddControl />
    </div>
  );
}

function FieldRow({ field, value }: { field: FieldSpec; value: unknown }) {
  const remove = () =>
    store().updateDefinition((def) => ({
      ...def,
      fields: def.fields.filter((f) => f.key !== field.key),
    }));

  return (
    <div className="group flex items-center gap-3">
      <span className="w-32 shrink-0 truncate text-xs" title={field.key}>
        {field.label}
      </span>
      {field.kind === 'color' ? (
        <input
          type="color"
          value={typeof value === 'string' ? value : '#ffffff'}
          onChange={(e) => store().setParam(field.key, e.target.value)}
          className="h-6 w-10 cursor-pointer rounded border border-edge bg-transparent"
        />
      ) : (
        <>
          <input
            type="range"
            min={field.min}
            max={field.max}
            step={field.step}
            value={typeof value === 'number' ? value : field.min}
            onChange={(e) => store().setParam(field.key, Number(e.target.value))}
            className="flex-1 accent-(--color-brand)"
          />
          <span className="w-12 text-right text-xs tabular-nums text-fg-subtle">
            {typeof value === 'number' ? value.toFixed(2) : '—'}
          </span>
        </>
      )}
      <button
        onClick={remove}
        title="remove control"
        className="invisible text-fg-subtle hover:text-red-400 group-hover:visible"
      >
        ✕
      </button>
    </div>
  );
}

function SaveAsPalette() {
  const [name, setName] = useState('');
  return (
    <span className="flex items-center gap-1">
      <input
        placeholder="new palette…"
        value={name}
        onChange={(e) => setName(e.target.value)}
        className="w-28 rounded-md border border-edge bg-bg px-2 py-1 text-xs"
      />
      <button
        disabled={!name.trim()}
        className="rounded-md border border-edge px-2 py-1 text-xs text-fg-subtle hover:text-fg disabled:opacity-40"
        onClick={() => {
          store().savePalette(name.trim());
          setName('');
        }}
      >
        +
      </button>
    </span>
  );
}

/** Add uniform + field + `param.<key>` binding in one move. */
function AddControl() {
  const [key, setKey] = useState('');
  const [kind, setKind] = useState<'slider' | 'color'>('slider');
  const [section, setSection] = useState<SectionId>('energy');

  const add = () => {
    const k = key.trim();
    if (!/^[a-zA-Z][a-zA-Z0-9]{0,40}$/.test(k)) return;
    const uniform = `u${k[0].toUpperCase()}${k.slice(1)}`;
    const uniformType: UniformType = kind === 'color' ? 'color' : 'float';
    const field: FieldSpec =
      kind === 'color'
        ? { kind: 'color', key: k, label: k, section }
        : { kind: 'slider', key: k, label: k, section, min: 0, max: 2, step: 0.05 };
    store().updateDefinition((def) => ({
      ...def,
      uniforms: {
        ...def.uniforms,
        [uniform]: { type: uniformType, default: kind === 'color' ? '#9b87f2' : 1.0 },
      },
      fields: [...def.fields, field],
      palettes: Object.fromEntries(
        Object.entries(def.palettes).map(([name, p]) => [
          name,
          { ...p, [k]: kind === 'color' ? '#9b87f2' : 1.0 },
        ]),
      ),
      bindings: [...def.bindings, { target: uniform, signal: `param.${k}` }],
    }));
    store().setParam(k, kind === 'color' ? '#9b87f2' : 1.0);
    setKey('');
  };

  return (
    <section className="rounded-lg border border-edge p-3">
      <h3 className="mb-2 text-xs uppercase tracking-wider text-fg-subtle">
        Add control → uniform + field + binding
      </h3>
      <div className="flex items-center gap-2 text-xs">
        <input
          placeholder="paramName"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          className="w-32 rounded-md border border-edge bg-bg px-2 py-1"
        />
        <select
          value={kind}
          onChange={(e) => setKind(e.target.value as 'slider' | 'color')}
          className="rounded-md border border-edge bg-bg px-1.5 py-1"
        >
          <option value="slider">slider → float</option>
          <option value="color">color → vec3</option>
        </select>
        <select
          value={section}
          onChange={(e) => setSection(e.target.value as SectionId)}
          className="rounded-md border border-edge bg-bg px-1.5 py-1"
        >
          {SECTIONS.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <button
          onClick={add}
          className="rounded-md bg-brand-deep px-2.5 py-1 text-white disabled:opacity-40"
          disabled={!key.trim()}
        >
          Add
        </button>
      </div>
      <p className="mt-2 text-[11px] text-fg-subtle">
        Creates uniform <code>u{key ? key[0]?.toUpperCase() + key.slice(1) : 'X'}</code>, a
        settings field, a palette entry, and a <code>param.{key || 'x'}</code> binding.
      </p>
    </section>
  );
}
