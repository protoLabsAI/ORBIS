import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import CodeMirror from '@uiw/react-codemirror';
import { cpp } from '@codemirror/lang-cpp';
import { compileCheck, buildFragmentSource } from '@orbis/orb-runtime';
import { store } from '../state';

const extensions = [cpp()];

/**
 * GLSL fragment editor. Edits compile against a real WebGL context
 * (debounced); only a clean compile commits to the live definition, so
 * the preview never goes black mid-keystroke — the last good shader
 * keeps rendering while the diagnostics show what's wrong.
 */
export function ShaderPane() {
  const snap = useSyncExternalStore(store().subscribe, store().getSnapshot, store().getSnapshot);
  const [text, setText] = useState(snap.definition.shaders.fragment);
  const [showPrelude, setShowPrelude] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // External definition swaps (template pick, import, JSON pane edits)
  // reset the buffer.
  const fragment = snap.definition.shaders.fragment;
  const lastExternal = useRef(fragment);
  useEffect(() => {
    if (fragment !== lastExternal.current) {
      lastExternal.current = fragment;
      setText(fragment);
    }
  }, [fragment]);

  const onChange = (value: string) => {
    setText(value);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      const candidate = {
        ...store().getSnapshot().definition,
        shaders: { ...store().getSnapshot().definition.shaders, fragment: value },
      };
      const result = compileCheck(candidate);
      store().setShaderLog(result.ok ? '' : result.log || 'shader failed to compile');
      if (result.ok) {
        lastExternal.current = value;
        store().updateDefinition((def) => ({
          ...def,
          shaders: { ...def.shaders, fragment: value },
        }));
      }
    }, 450);
  };

  const prelude = buildFragmentSource(snap.definition).split('// — definition body —')[0];

  return (
    <div className="flex h-full flex-col">
      <button
        onClick={() => setShowPrelude((v) => !v)}
        className="border-b border-edge px-3 py-1.5 text-left text-xs text-fg-subtle hover:text-fg"
      >
        {showPrelude ? '▾' : '▸'} injected prelude (standard + declared uniforms)
      </button>
      {showPrelude && (
        <pre className="max-h-44 overflow-auto border-b border-edge bg-bg px-3 py-2 text-[11px] leading-relaxed text-fg-subtle">
          {prelude}
        </pre>
      )}
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
      {snap.shaderLog && (
        <pre className="max-h-32 overflow-auto border-t border-red-900 bg-red-950/40 px-3 py-2 text-[11px] text-red-300">
          {snap.shaderLog}
        </pre>
      )}
    </div>
  );
}
