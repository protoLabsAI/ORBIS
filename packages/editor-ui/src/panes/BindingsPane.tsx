import { useSyncExternalStore } from 'react';
import type { Binding, BindingCurve, BindingOp } from '@orbis/orb-runtime';
import { STANDARD_UNIFORM_NAMES, RESERVED_BINDING_TARGETS } from '@orbis/orb-runtime';
import { store } from '../state';

const SIGNALS = [
  'time',
  'bot.level',
  'user.level',
  'breath',
  'pointer.clickStrength',
  'mood.valence',
  'mood.arousal',
  'mood.guardedness',
  'snap.density',
  'snap.glow',
  'snap.speed',
  'snap.ca',
  'snap.asymmetry',
  'snap.rotation',
  'snap.scale',
  'snap.primary',
  'snap.secondary',
];
const OPS: BindingOp[] = ['set', 'add', 'mul'];
const CURVES: BindingCurve[] = ['linear', 'exp', 'smoothstep'];

/**
 * Signal→uniform wiring table. Per target, bindings evaluate top-down
 * each frame from the uniform's declared default:
 * acc = acc &lt;op&gt; (curve(signal) · scale + offset).
 */
export function BindingsPane() {
  const snap = useSyncExternalStore(store().subscribe, store().getSnapshot, store().getSnapshot);
  const def = snap.definition;

  const targets = [
    ...Object.keys(def.uniforms),
    ...STANDARD_UNIFORM_NAMES.filter((n) => !RESERVED_BINDING_TARGETS.has(n)),
  ];
  const paramSignals = def.fields.map((f) => `param.${f.key}`);
  const allSignals = [...paramSignals, ...SIGNALS];

  const update = (i: number, patch: Partial<Binding>) =>
    store().updateDefinition((d) => ({
      ...d,
      bindings: d.bindings.map((b, j) => (j === i ? { ...b, ...patch } : b)),
    }));

  const remove = (i: number) =>
    store().updateDefinition((d) => ({
      ...d,
      bindings: d.bindings.filter((_, j) => j !== i),
    }));

  const add = () =>
    store().updateDefinition((d) => ({
      ...d,
      bindings: [
        ...d.bindings,
        { target: targets[0] ?? 'uGlow', signal: allSignals[0] ?? 'time' },
      ],
    }));

  return (
    <div className="h-full overflow-auto p-4 text-xs">
      <table className="w-full border-separate border-spacing-y-1">
        <thead className="text-left text-fg-subtle">
          <tr>
            <th className="font-normal">target</th>
            <th className="font-normal">signal</th>
            <th className="font-normal">op</th>
            <th className="font-normal">scale</th>
            <th className="font-normal">offset</th>
            <th className="font-normal">curve</th>
            <th className="font-normal">smooth</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {def.bindings.map((b, i) => (
            <tr key={i} className="align-middle">
              <td>
                <input
                  value={b.target}
                  onChange={(e) => update(i, { target: e.target.value })}
                  list="binding-targets"
                  className="w-32 rounded-md border border-edge bg-bg px-1.5 py-1"
                />
              </td>
              <td>
                <input
                  value={b.signal}
                  onChange={(e) => update(i, { signal: e.target.value })}
                  list="binding-signals"
                  className="w-36 rounded-md border border-edge bg-bg px-1.5 py-1"
                />
              </td>
              <td>
                <select
                  value={b.op ?? 'set'}
                  onChange={(e) => update(i, { op: e.target.value as BindingOp })}
                  className="rounded-md border border-edge bg-bg px-1 py-1"
                >
                  {OPS.map((o) => (
                    <option key={o}>{o}</option>
                  ))}
                </select>
              </td>
              <NumCell value={b.scale} fallback={1} onChange={(scale) => update(i, { scale })} />
              <NumCell value={b.offset} fallback={0} onChange={(offset) => update(i, { offset })} />
              <td>
                <select
                  value={b.curve ?? 'linear'}
                  onChange={(e) => update(i, { curve: e.target.value as BindingCurve })}
                  className="rounded-md border border-edge bg-bg px-1 py-1"
                >
                  {CURVES.map((c) => (
                    <option key={c}>{c}</option>
                  ))}
                </select>
              </td>
              <NumCell
                value={b.smooth}
                fallback={0}
                onChange={(v) => update(i, { smooth: v > 0 && v <= 1 ? v : undefined })}
              />
              <td>
                <button onClick={() => remove(i)} className="px-1 text-fg-subtle hover:text-red-400">
                  ✕
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <datalist id="binding-targets">
        {targets.map((t) => (
          <option key={t} value={t} />
        ))}
      </datalist>
      <datalist id="binding-signals">
        {allSignals.map((s) => (
          <option key={s} value={s} />
        ))}
      </datalist>

      <button
        onClick={add}
        className="mt-3 rounded-md border border-edge px-2.5 py-1.5 text-fg-subtle hover:text-fg"
      >
        + Add binding
      </button>
      <p className="mt-3 max-w-xl leading-relaxed text-fg-subtle">
        Per target, rows evaluate top-down each frame starting from the uniform's declared
        default: <code>acc = acc ⟨op⟩ (curve(signal) · scale + offset)</code>. Vector
        components bind as <code>uName.x</code>. Color targets take color signals
        (<code>snap.primary</code>, <code>param.&lt;colorKey&gt;</code>) with op <code>set</code>.
      </p>
    </div>
  );
}

function NumCell(props: { value: number | undefined; fallback: number; onChange: (v: number) => void }) {
  return (
    <td>
      <input
        type="number"
        step="0.05"
        value={props.value ?? props.fallback}
        onChange={(e) => props.onChange(Number(e.target.value))}
        className="w-16 rounded-md border border-edge bg-bg px-1.5 py-1"
      />
    </td>
  );
}
