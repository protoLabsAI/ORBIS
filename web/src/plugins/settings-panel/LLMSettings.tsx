import { useCallback, useEffect, useRef, useState } from 'react';
import { Panel } from '@/components/ui/panel';
import { Button } from '@/components/ui/button';
import { api, type OrbisConfig } from '@/lib/api';
import {
  LLM_PRESETS,
  matchPreset,
  type LLMPreset,
} from '@/shared/llm/presets';

type LLMPayload = NonNullable<OrbisConfig['llm']>;

type TestState =
  | { kind: 'idle' }
  | { kind: 'checking' }
  | { kind: 'ok'; latency: number }
  | { kind: 'error'; message: string };

type SaveState =
  | { kind: 'idle' }
  | { kind: 'saving' }
  | { kind: 'saved' }
  | { kind: 'error'; message: string };

/**
 * LLM settings — edit the router-brain provider without re-running the
 * setup wizard. Persists via POST /api/config which reloads the persona
 * server-side, so the next voice turn picks up the new endpoint.
 *
 * Shows the same minimal "featured" preset surface as the wizard's LLM
 * step, with an expander for the long-tail of OpenAI-compatible
 * providers.
 */
export function LLMSettings() {
  const [loading, setLoading] = useState(true);
  const [provider, setProvider] = useState<string>('openai');
  const [url, setUrl] = useState('');
  const [model, setModel] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [keyPlaceholder, setKeyPlaceholder] = useState('key is already set — leave blank to keep');
  const [keyIsSet, setKeyIsSet] = useState(false);
  const [test, setTest] = useState<TestState>({ kind: 'idle' });
  const [save, setSave] = useState<SaveState>({ kind: 'idle' });
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showAllProviders, setShowAllProviders] = useState(false);

  // Tracks the "saved" → "idle" timer so we can cancel it on unmount and
  // avoid setState-after-unmount if the drawer closes mid-toast.
  const saveResetTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    return () => {
      if (saveResetTimerRef.current) clearTimeout(saveResetTimerRef.current);
    };
  }, []);

  // Load current /api/config and pre-fill.
  useEffect(() => {
    let cancelled = false;
    api.config()
      .then((r) => {
        if (cancelled) return;
        const llm = r.config?.llm ?? {};
        const rawUrl = llm.url ?? '';
        const rawModel = llm.model ?? '';
        const hasKey = typeof llm.api_key === 'string' && llm.api_key.length > 0;
        setUrl(rawUrl);
        setModel(rawModel);
        setKeyIsSet(hasKey);
        // Don't prefill the key field — show a placeholder instead so the
        // user can tell a key is saved without us rendering it in the DOM.
        setApiKey('');
        setProvider(matchPreset(rawUrl, rawModel));
      })
      .catch((e) => setError(String((e as Error).message ?? e)))
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const current: LLMPreset =
    LLM_PRESETS.find((p) => p.id === provider) ?? LLM_PRESETS[LLM_PRESETS.length - 1];

  const visiblePresets = (showAllProviders || !current.featured)
    ? LLM_PRESETS
    : LLM_PRESETS.filter((p) => p.featured || p.id === provider);
  const hiddenCount = LLM_PRESETS.length - visiblePresets.length;

  useEffect(() => {
    setKeyPlaceholder(
      keyIsSet
        ? 'key is already set — leave blank to keep'
        : current.keyPlaceholder ?? 'api key',
    );
  }, [current.keyPlaceholder, keyIsSet]);

  const pickProvider = useCallback((next: string) => {
    setProvider(next);
    const preset = LLM_PRESETS.find((p) => p.id === next) ?? LLM_PRESETS[0];
    // Only overwrite url/model if the user hasn't typed something custom.
    // Simpler semantics for a runtime panel: always overwrite, since the
    // user explicitly picked a preset. They can type over after.
    setUrl(preset.url);
    setModel(preset.model);
    setTest({ kind: 'idle' });
    setSave({ kind: 'idle' });
    setAvailableModels([]);
  }, []);

  const onFetchModels = async () => {
    if (!url.trim()) {
      setError('Need a URL to fetch models.');
      return;
    }
    setError(null);
    try {
      const r = await api.llmModels({
        url: url.trim(),
        api_key: apiKey.trim() || undefined,
      });
      if (r.ok) {
        setAvailableModels(r.models);
        if (!model && r.models[0]) setModel(r.models[0]);
      } else {
        setError(`Couldn't list models: ${r.error ?? 'unknown'}`);
      }
    } catch (e) {
      setError(String((e as Error).message ?? e));
    }
  };

  const onTest = async () => {
    if (!url.trim() || !model.trim()) {
      setError('URL and model required to test.');
      return;
    }
    setError(null);
    setTest({ kind: 'checking' });
    try {
      const r = await api.llmTest({
        url: url.trim(),
        model: model.trim(),
        api_key: apiKey.trim() || undefined,
      });
      if (r.ok) {
        setTest({ kind: 'ok', latency: r.latency_ms ?? 0 });
      } else {
        setTest({ kind: 'error', message: r.error ?? 'unknown error' });
      }
    } catch (e) {
      setTest({ kind: 'error', message: String((e as Error).message ?? e) });
    }
  };

  const onSave = async () => {
    setError(null);
    if (!url.trim() || !model.trim()) {
      setError('URL and model are both required.');
      return;
    }
    setSave({ kind: 'saving' });
    try {
      // Only include the key when the user typed something — empty string
      // would clear it, which they probably don't want when a key was
      // already set.
      const llm: LLMPayload = { url: url.trim(), model: model.trim() };
      if (apiKey.trim()) llm.api_key = apiKey.trim();
      await api.putConfig({ llm });
      setSave({ kind: 'saved' });
      if (apiKey.trim()) {
        setKeyIsSet(true);
        setApiKey('');
      }
      if (saveResetTimerRef.current) clearTimeout(saveResetTimerRef.current);
      saveResetTimerRef.current = setTimeout(
        () => setSave({ kind: 'idle' }),
        2000,
      );
    } catch (e) {
      setSave({ kind: 'error', message: String((e as Error).message ?? e) });
    }
  };

  if (loading) {
    return (
      <Panel title="LLM">
        <div className="text-xs text-zinc-500">Loading…</div>
      </Panel>
    );
  }

  return (
    <Panel title="LLM">
      <div className="space-y-4">
        <p className="text-sm text-zinc-400 -mt-1">
          Router brain. Pick a preset or type your own URL.
        </p>

        <div className="grid grid-cols-2 gap-2">
          {visiblePresets.map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() => pickProvider(p.id)}
              className={
                'p-2 text-left rounded-md border transition-colors ' +
                (provider === p.id
                  ? 'border-amber-500/60 bg-amber-500/5'
                  : 'border-zinc-800 bg-zinc-900/40 hover:bg-zinc-900/70')
              }
            >
              <div className="text-xs text-zinc-200">{p.label}</div>
              <div className="text-[11px] text-zinc-500 mt-0.5 line-clamp-2">
                {p.blurb}
              </div>
            </button>
          ))}
        </div>

        {hiddenCount > 0 && (
          <button
            type="button"
            onClick={() => setShowAllProviders(true)}
            className="text-xs uppercase tracking-wider text-zinc-500 hover:text-zinc-300 transition-colors"
          >
            Show {hiddenCount} more providers
          </button>
        )}
        {showAllProviders && (
          <button
            type="button"
            onClick={() => setShowAllProviders(false)}
            className="text-xs uppercase tracking-wider text-zinc-500 hover:text-zinc-300 transition-colors"
          >
            Show fewer
          </button>
        )}

        <div className="space-y-3">
          <div>
            <label className="text-xs uppercase tracking-wider text-zinc-500 mb-1 block">
              URL
            </label>
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://api.openai.com/v1"
              className="w-full h-9 rounded-md border border-zinc-800 bg-zinc-900/60 px-2.5 text-xs text-zinc-200 placeholder-zinc-600 font-mono"
              spellCheck={false}
            />
          </div>

          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="text-xs uppercase tracking-wider text-zinc-500">
                Model
              </label>
              <button
                type="button"
                onClick={onFetchModels}
                className="text-[11px] uppercase tracking-wider text-zinc-500 hover:text-zinc-300 transition-colors"
              >
                Fetch list
              </button>
            </div>
            <input
              list={`settings-llm-models-${provider}`}
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="gpt-4o-mini"
              className="w-full h-9 rounded-md border border-zinc-800 bg-zinc-900/60 px-2.5 text-xs text-zinc-200 placeholder-zinc-600 font-mono"
              spellCheck={false}
            />
            {availableModels.length > 0 && (
              <datalist id={`settings-llm-models-${provider}`}>
                {availableModels.map((m) => <option key={m} value={m} />)}
              </datalist>
            )}
          </div>

          {current.needsKey && (
            <div>
              <label className="text-xs uppercase tracking-wider text-zinc-500 mb-1 block">
                API key
              </label>
              <input
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder={keyPlaceholder}
                className="w-full h-9 rounded-md border border-zinc-800 bg-zinc-900/60 px-2.5 text-xs text-zinc-200 placeholder-zinc-600 font-mono"
                autoComplete="off"
                spellCheck={false}
              />
              <div className="text-[11px] text-zinc-600 mt-1">
                {keyIsSet
                  ? 'A key is saved on your machine. Leave blank to keep it.'
                  : 'Stored in config/orbis.yaml on your machine.'}
              </div>
            </div>
          )}
        </div>

        <div className="flex items-center gap-2">
          <Button
            variant="secondary"
            size="sm"
            onClick={onTest}
            disabled={test.kind === 'checking'}
          >
            {test.kind === 'checking' ? 'Testing…' : 'Test'}
          </Button>
          <Button
            size="sm"
            onClick={onSave}
            disabled={save.kind === 'saving'}
          >
            {save.kind === 'saving' ? 'Saving…' : 'Save'}
          </Button>
          {test.kind === 'ok' && (
            <span className="text-xs text-emerald-400">
              ✓ Connected ({test.latency} ms)
            </span>
          )}
          {test.kind === 'error' && (
            <span className="text-xs text-red-400 truncate max-w-[55%]">
              ✗ {test.message}
            </span>
          )}
          {save.kind === 'saved' && (
            <span className="text-xs text-emerald-400">✓ Saved</span>
          )}
          {save.kind === 'error' && (
            <span className="text-xs text-red-400 truncate max-w-[55%]">
              ✗ {save.message}
            </span>
          )}
        </div>

        {error && <div className="text-xs text-red-400">{error}</div>}
      </div>
    </Panel>
  );
}
