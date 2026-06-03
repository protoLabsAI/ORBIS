import { type KeyboardEvent, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { getCurrentWindow } from '@tauri-apps/api/window';
import { Search } from 'lucide-react';
import { type CommandAction, collectActions } from './registry';
import { widgetWorkspace } from '../widgets/store';
import './actions'; // register the built-in actions

/**
 * Command bar — a Raycast/Script Kit-style palette, summoned by the global
 * hotkey (Cmd+Shift+O) into its own centered, transparent native window. Type
 * to filter, ↑/↓ to move, Enter to run, Esc / click-away to dismiss. The
 * keyboard-fast complement to voice; ORBIS stays voice-first.
 */
export function CommandBar() {
  const [query, setQuery] = useState('');
  const [sel, setSel] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  // Re-render when widget state changes so open/close labels stay accurate.
  useSyncExternalStore(widgetWorkspace.subscribe, widgetWorkspace.getSnapshot);
  const actions = collectActions();

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return actions;
    const terms = q.split(/\s+/);
    return actions.filter((a) => {
      const hay = `${a.title} ${a.subtitle ?? ''} ${a.keywords?.join(' ') ?? ''}`.toLowerCase();
      return terms.every((t) => hay.includes(t));
    });
  }, [query, actions]);

  useEffect(() => {
    setSel((s) => Math.min(s, Math.max(0, filtered.length - 1)));
  }, [filtered.length]);

  const hide = () => {
    setQuery('');
    setSel(0);
    void getCurrentWindow().hide();
  };

  const runAction = async (a: CommandAction) => {
    try {
      await a.run();
    } finally {
      hide();
    }
  };

  // Focus + reset whenever the window is (re)summoned or loses focus (dismiss).
  useEffect(() => {
    const onFocus = () => {
      setQuery('');
      setSel(0);
      inputRef.current?.focus();
    };
    const onBlur = () => hide();
    onFocus();
    window.addEventListener('focus', onFocus);
    window.addEventListener('blur', onBlur);
    return () => {
      window.removeEventListener('focus', onFocus);
      window.removeEventListener('blur', onBlur);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      hide();
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSel((s) => Math.min(s + 1, filtered.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSel((s) => Math.max(s - 1, 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const a = filtered[sel];
      if (a) void runAction(a);
    }
  };

  return (
    <div className="fixed inset-0 flex flex-col overflow-hidden rounded-xl bg-raised ring-1 ring-edge shadow-2xl">
      <div className="flex items-center gap-2.5 border-b border-edge px-4">
        <Search className="h-4 w-4 shrink-0 text-fg-subtle" />
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setSel(0);
          }}
          onKeyDown={onKeyDown}
          placeholder="Type a command…"
          spellCheck={false}
          // eslint-disable-next-line jsx-a11y/no-autofocus
          autoFocus
          className="h-14 w-full bg-transparent text-lg text-fg-body placeholder-fg-subtle outline-none"
        />
      </div>
      <ul className="flex-1 overflow-y-auto p-1.5">
        {filtered.length === 0 && (
          <li className="px-3 py-2 text-sm text-fg-subtle">No matching commands.</li>
        )}
        {filtered.map((a, i) => {
          const Icon = a.icon;
          return (
            <li key={a.id}>
              <button
                type="button"
                onMouseEnter={() => setSel(i)}
                onClick={() => void runAction(a)}
                className={`flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left transition-colors ${
                  i === sel ? 'bg-brand/15' : 'hover:bg-edge/50'
                }`}
              >
                {Icon && <Icon className="h-4 w-4 shrink-0 text-fg-subtle" />}
                <span className="flex-1 truncate text-sm text-fg-body">{a.title}</span>
                {a.subtitle && (
                  <span className="shrink-0 text-helper uppercase tracking-wider text-fg-subtle">
                    {a.subtitle}
                  </span>
                )}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
