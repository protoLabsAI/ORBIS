import { useSyncExternalStore } from 'react';
import type { OrbDefinition } from '@orbis/orb-runtime';
import { store } from '../state';

/** Identity + motion/material knobs. Everything else structural lives
 * in the JSON pane. */
export function MetaPane() {
  const snap = useSyncExternalStore(store().subscribe, store().getSnapshot, store().getSnapshot);
  const def = snap.definition;

  const patch = (p: Partial<OrbDefinition>) =>
    store().updateDefinition((d) => ({ ...d, ...p }));

  return (
    <div className="flex h-full flex-col gap-4 overflow-auto p-4 text-sm">
      <Row label="id (slug)">
        <input
          value={def.id}
          onChange={(e) => patch({ id: e.target.value })}
          className="w-56 rounded-md border border-edge bg-bg px-2 py-1 font-mono text-xs"
        />
      </Row>
      <Row label="name">
        <input
          value={def.name}
          onChange={(e) => patch({ name: e.target.value })}
          className="w-56 rounded-md border border-edge bg-bg px-2 py-1 text-xs"
        />
      </Row>
      <Row label="author">
        <input
          value={def.author ?? ''}
          onChange={(e) => patch({ author: e.target.value })}
          className="w-56 rounded-md border border-edge bg-bg px-2 py-1 text-xs"
        />
      </Row>
      <Row label="description">
        <textarea
          value={def.description ?? ''}
          onChange={(e) => patch({ description: e.target.value })}
          rows={2}
          className="w-full max-w-md rounded-md border border-edge bg-bg px-2 py-1 text-xs"
        />
      </Row>

      <Row label="speech scale pump">
        <input
          type="range"
          min={0}
          max={0.2}
          step={0.005}
          value={def.motion?.scaleAudioPump ?? 0.06}
          onChange={(e) =>
            patch({ motion: { ...def.motion, scaleAudioPump: Number(e.target.value) } })
          }
          className="w-44 accent-(--color-brand)"
        />
        <span className="text-xs text-fg-subtle">
          {(def.motion?.scaleAudioPump ?? 0.06).toFixed(3)}
        </span>
      </Row>
      <Row label="idle breath">
        <input
          type="checkbox"
          checked={def.motion?.breath ?? true}
          onChange={(e) => patch({ motion: { ...def.motion, breath: e.target.checked } })}
          className="accent-(--color-brand)"
        />
      </Row>
      <Row label="blending">
        <select
          value={def.material?.blending ?? 'additive'}
          onChange={(e) =>
            patch({
              material: {
                ...def.material,
                blending: e.target.value as 'additive' | 'normal',
              },
            })
          }
          className="rounded-md border border-edge bg-bg px-2 py-1 text-xs"
        >
          <option value="additive">additive</option>
          <option value="normal">normal</option>
        </select>
      </Row>
      <Row label="sphere radius">
        <input
          type="number"
          step={0.5}
          value={def.geometry?.radius ?? 5.5}
          onChange={(e) =>
            patch({ geometry: { ...def.geometry, radius: Number(e.target.value) } })
          }
          className="w-20 rounded-md border border-edge bg-bg px-2 py-1 text-xs"
        />
      </Row>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex items-center gap-3">
      <span className="w-36 shrink-0 text-xs text-fg-subtle">{label}</span>
      {children}
    </label>
  );
}
