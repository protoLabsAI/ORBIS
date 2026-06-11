import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import CodeMirror from '@uiw/react-codemirror';
import { json } from '@codemirror/lang-json';
import { validateOrbDefinition } from '@orbis/orb-runtime';
import { store } from '../state';

const extensions = [json()];

/**
 * Raw definition editor — the power-user path for everything without a
 * dedicated pane yet (uniform defaults, geometry/material/motion,
 * moodDefaults). Validates on every edit; only a valid document commits.
 */
export function JsonPane() {
  const snap = useSyncExternalStore(store().subscribe, store().getSnapshot, store().getSnapshot);
  const canonical = JSON.stringify(snap.definition, null, 2);
  const [text, setText] = useState(canonical);
  const [errors, setErrors] = useState<string[]>([]);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastCommitted = useRef(canonical);

  useEffect(() => {
    if (canonical !== lastCommitted.current) {
      lastCommitted.current = canonical;
      setText(canonical);
      setErrors([]);
    }
  }, [canonical]);

  const onChange = (value: string) => {
    setText(value);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      let raw: unknown;
      try {
        raw = JSON.parse(value);
      } catch (e) {
        setErrors([e instanceof Error ? e.message : 'invalid JSON']);
        return;
      }
      const res = validateOrbDefinition(raw);
      if (!res.ok) {
        setErrors(res.errors);
        return;
      }
      setErrors([]);
      lastCommitted.current = JSON.stringify(res.def, null, 2);
      store().loadDefinition(res.def);
    }, 600);
  };

  return (
    <div className="flex h-full flex-col">
      <div className="min-h-0 flex-1 overflow-auto">
        <CodeMirror
          value={text}
          onChange={onChange}
          extensions={extensions}
          theme="dark"
          height="100%"
          style={{ height: '100%', fontSize: 12 }}
        />
      </div>
      {errors.length > 0 && (
        <pre className="max-h-32 overflow-auto border-t border-red-900 bg-red-950/40 px-3 py-2 text-[11px] text-red-300">
          {errors.join('\n')}
        </pre>
      )}
    </div>
  );
}
