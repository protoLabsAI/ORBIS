import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { invoke } from '@tauri-apps/api/core';
import { NativeLevelMeter } from '@/shared/audio/NativeLevelMeter';
import { api, type StarterOrb } from '@/lib/api';
import { LLM_PRESETS } from '@/shared/llm/presets';
import { pullMlxModel, pullOllamaModel } from '@/shared/llm/ollamaPull';
import { applyPreset, setVariant } from '@/plugins/orb/broadcast';
import {
  getPreferredAudioDeviceId,
  setPreferredAudioDeviceId,
} from '@/shared/audio/preferredDevice';
import { OrbPreviewModal } from './OrbPreviewModal';
import { paletteColors } from './paletteColors';

// A stable default for the LLM step — explicit id lookup so the
// default doesn't drift if the preset ordering changes. Ollama is
// the recommended path for the desktop app; if that id ever goes
// away, fall back to the first entry.
const DEFAULT_LLM_PRESET =
  LLM_PRESETS.find((p) => p.id === 'ollama') ?? LLM_PRESETS[0];

const STORAGE_COMPLETE = 'orbis.setupComplete';

type Step = 'welcome' | 'names' | 'llm' | 'pick' | 'mic' | 'done' | 'hatching';

/**
 * First-run setup wizard. Detects "no setup done yet" via a
 * localStorage flag, overlays a full-screen panel above the app,
 * walks the user through: welcome → API key (optional) → pick
 * starter orb → done.
 *
 * Re-enter from settings at any time by clearing
 * ``localStorage['orbis.setupComplete']``.
 */
export function SetupWizard() {
  const [needsSetup, setNeedsSetup] = useState<boolean>(() => {
    try {
      return localStorage.getItem(STORAGE_COMPLETE) !== 'true';
    } catch {
      return true;
    }
  });

  if (!needsSetup) return null;

  return (
    <div className="fixed inset-0 z-30 bg-[#0a0a0a]/95 backdrop-blur-sm overflow-y-auto">
      <WizardFlow
        onFinish={() => {
          try { localStorage.setItem(STORAGE_COMPLETE, 'true'); } catch {}
          setNeedsSetup(false);
        }}
      />
    </div>
  );
}

function WizardFlow({ onFinish }: { onFinish: () => void }) {
  const [step, setStep] = useState<Step>('welcome');

  if (step === 'hatching') {
    return <HatchAnimation onDone={onFinish} />;
  }

  return (
    <div className="min-h-full flex items-center justify-center p-6">
      <div className="w-full max-w-xl">
        <StepIndicator current={step} />
        <div className="mt-8">
          {step === 'welcome' && <WelcomeStep onNext={() => setStep('names')} />}
          {step === 'names' && (
            <NamesStep
              onNext={() => setStep('llm')}
              onBack={() => setStep('welcome')}
            />
          )}
          {step === 'llm' && (
            <LLMStep
              onNext={() => setStep('pick')}
              onBack={() => setStep('names')}
            />
          )}
          {step === 'pick' && (
            <PickStep
              onNext={() => setStep('mic')}
              onBack={() => setStep('llm')}
            />
          )}
          {step === 'mic' && (
            <MicStep
              onNext={() => setStep('done')}
              onBack={() => setStep('pick')}
            />
          )}
          {step === 'done' && (
            <DoneStep onFinish={() => setStep('hatching')} />
          )}
        </div>
      </div>
    </div>
  );
}

// ── Indicator ──────────────────────────────────────────────────────────────

function StepIndicator({ current }: { current: Step }) {
  const order: Step[] = ['welcome', 'names', 'llm', 'pick', 'mic', 'done'];
  const idx = Math.max(0, order.indexOf(current));
  return (
    <div className="flex items-center gap-2 justify-center">
      {order.map((s, i) => (
        <div
          key={s}
          className={
            'h-1.5 rounded-full transition-all ' +
            (i < idx ? 'bg-amber-500/70 w-6' : i === idx ? 'bg-amber-500 w-10' : 'bg-zinc-700 w-6')
          }
        />
      ))}
    </div>
  );
}

// ── Steps ──────────────────────────────────────────────────────────────────

function WelcomeStep({ onNext }: { onNext: () => void }) {
  return (
    <div className="text-center space-y-6">
      <h1 className="font-mono text-3xl tracking-wider text-zinc-200">ORBIS</h1>
      <p className="text-sm text-zinc-400 max-w-sm mx-auto leading-relaxed">
        A voice companion. It talks back in real time, remembers you
        across sessions, and can route the heavy lifting to the agents
        you've configured.
      </p>
      <p className="text-xs text-zinc-600 max-w-sm mx-auto">
        Four quick steps: access, pick an orb, and meet it.
      </p>
      <Button onClick={onNext}>Let's go</Button>
    </div>
  );
}

function NamesStep({ onNext, onBack }: { onNext: () => void; onBack: () => void }) {
  const [userName, setUserName] = useState('');
  const [orbName, setOrbName] = useState('ORBIS');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onContinue = async () => {
    setSaving(true);
    setError(null);
    try {
      await api.putConfig({
        persona: {
          name: orbName.trim() || 'ORBIS',
          user_name: userName.trim(),
        },
      });
      onNext();
    } catch (e) {
      setSaving(false);
      setError(String((e as Error).message ?? e));
    }
  };

  return (
    <div className="space-y-6">
      <div className="text-center space-y-2">
        <h2 className="text-lg text-zinc-200">Introductions</h2>
        <p className="text-sm text-zinc-500 max-w-md mx-auto">
          Both fields are optional — the orb will still work without
          them, just more generically.
        </p>
      </div>

      <div className="space-y-4">
        <div>
          <label className="text-xs uppercase tracking-wider text-zinc-500 mb-1.5 block">
            Your name — what the orb should call you
          </label>
          <input
            value={userName}
            onChange={(e) => setUserName(e.target.value)}
            placeholder="Alice"
            className="w-full h-10 rounded-md border border-zinc-800 bg-zinc-900/60 px-3 text-sm text-zinc-200 placeholder-zinc-600"
            autoComplete="off"
            spellCheck={false}
          />
        </div>

        <div>
          <label className="text-xs uppercase tracking-wider text-zinc-500 mb-1.5 block">
            Orb's name — what you'll call the orb
          </label>
          <input
            value={orbName}
            onChange={(e) => setOrbName(e.target.value)}
            placeholder="ORBIS"
            className="w-full h-10 rounded-md border border-zinc-800 bg-zinc-900/60 px-3 text-sm text-zinc-200 placeholder-zinc-600"
            autoComplete="off"
            spellCheck={false}
          />
          <div className="text-[11px] text-zinc-600 mt-1">
            Defaults to ORBIS. Rename to whatever suits.
          </div>
        </div>

        {error && <div className="text-xs text-red-400">{error}</div>}
      </div>

      <div className="flex items-center justify-between">
        <Button variant="ghost" onClick={onBack}>Back</Button>
        <Button onClick={onContinue} disabled={saving}>
          {saving ? 'Saving…' : 'Continue'}
        </Button>
      </div>
    </div>
  );
}

// LLM_PRESETS is imported at the top of the file — shared with the
// Settings panel via web/src/shared/llm/presets.ts.

interface LocalDetected {
  ollama?: { url: string; models: string[] };
  lm_studio?: { url: string; models: string[] };
}

type TestState =
  | { kind: 'idle' }
  | { kind: 'checking' }
  | { kind: 'ok'; latency: number }
  | { kind: 'error'; message: string };

function LLMStep({ onNext, onBack }: { onNext: () => void; onBack: () => void }) {
  const [provider, setProvider] = useState<string>(DEFAULT_LLM_PRESET.id);
  const [url, setUrl] = useState(DEFAULT_LLM_PRESET.url);
  const [model, setModel] = useState(DEFAULT_LLM_PRESET.model);
  const [apiKey, setApiKey] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [test, setTest] = useState<TestState>({ kind: 'idle' });
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [local, setLocal] = useState<LocalDetected>({});
  const [showAllProviders, setShowAllProviders] = useState(false);

  const current = LLM_PRESETS.find((p) => p.id === provider) ?? DEFAULT_LLM_PRESET;
  // Show featured presets up front; reveal the long-tail OpenAI-compat
  // providers (Groq / DeepSeek / OpenRouter / etc.) only if the user
  // expands the accordion or has selected one of them already.
  const visiblePresets = (showAllProviders || !current.featured)
    ? LLM_PRESETS
    : LLM_PRESETS.filter((p) => p.featured || p.id === provider);
  const hiddenCount = LLM_PRESETS.length - visiblePresets.length;

  // Probe localhost for Ollama / LM Studio once on mount. Silent failure —
  // if they're not running, we just don't show the callout.
  useEffect(() => {
    api.llmDetectLocal()
      .then((found) => setLocal(found as LocalDetected))
      .catch(() => {});
  }, []);

  const pickProvider = (next: string) => {
    setProvider(next);
    const preset = LLM_PRESETS.find((p) => p.id === next) ?? DEFAULT_LLM_PRESET;
    setUrl(preset.url);
    setModel(preset.model);
    setTest({ kind: 'idle' });
    setAvailableModels([]);
  };

  const applyDetected = (name: 'ollama' | 'lm_studio') => {
    const entry = local[name];
    if (!entry) return;
    setProvider(name);
    setUrl(entry.url);
    setAvailableModels(entry.models);
    setModel(entry.models[0] ?? '');
    setTest({ kind: 'idle' });
  };

  const onFetchModels = async () => {
    if (!url.trim()) {
      setError('Need a URL to fetch models.');
      return;
    }
    setError(null);
    try {
      const r = await api.llmModels({ url: url.trim(), api_key: apiKey.trim() || undefined });
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

  const onContinue = async () => {
    setError(null);
    if (!url.trim() || !model.trim()) {
      setError('URL and model are both required.');
      return;
    }
    if (current.needsKey && !apiKey.trim() && provider !== 'custom') {
      setError(`${current.label} needs an API key.`);
      return;
    }
    setSaving(true);
    try {
      const llm: Record<string, string> = { url: url.trim(), model: model.trim() };
      if (apiKey.trim()) llm.api_key = apiKey.trim();
      await api.putConfig({ llm: llm as never });
      onNext();
    } catch (e) {
      setSaving(false);
      setError(String((e as Error).message ?? e));
    }
  };

  const localCallouts: Array<{ key: 'ollama' | 'lm_studio'; label: string; count: number }> = [];
  if (local.ollama) localCallouts.push({ key: 'ollama', label: 'Ollama', count: local.ollama.models.length });
  if (local.lm_studio) localCallouts.push({ key: 'lm_studio', label: 'LM Studio', count: local.lm_studio.models.length });

  return (
    <div className="space-y-6">
      <div className="text-center space-y-2">
        <h2 className="text-lg text-zinc-200">Router brain</h2>
        <p className="text-sm text-zinc-500 max-w-md mx-auto">
          Small + fast is the right pick — this LLM handles conversation
          + routing decisions. Heavy reasoning delegates out.
        </p>
      </div>

      {localCallouts.length > 0 && (
        <div className="rounded-md border border-emerald-500/30 bg-emerald-500/5 p-3 text-sm text-zinc-300">
          <div className="text-xs uppercase tracking-wider text-emerald-400 mb-2">
            Detected on your machine
          </div>
          <div className="flex flex-wrap gap-2">
            {localCallouts.map((c) => (
              <button
                key={c.key}
                type="button"
                onClick={() => applyDetected(c.key)}
                className="px-3 py-1.5 rounded-md bg-emerald-500/10 hover:bg-emerald-500/20 border border-emerald-500/30 text-emerald-200 text-xs transition-colors"
              >
                Use {c.label} ({c.count} model{c.count === 1 ? '' : 's'})
              </button>
            ))}
          </div>
        </div>
      )}
      {localCallouts.length === 0 && <OllamaInstallHelper />}
      {provider === 'mlx' && (
        <ModelPullCallout
          source="mlx"
          modelName={model || DEFAULT_LLM_PRESET.model}
          onPulled={() => {
            // Mark "test" as ok once the model is local — first
            // voice session will load from disk in seconds.
            setTest({ kind: 'ok', latency: 0 });
          }}
        />
      )}
      {provider === 'ollama' &&
        local.ollama &&
        !local.ollama.models.includes(model) && (
          <ModelPullCallout
            source="ollama"
            modelName={model}
            ollamaUrl={local.ollama.url}
            onPulled={() => {
              api
                .llmDetectLocal()
                .then((found) => {
                  setLocal(found as LocalDetected);
                  const next = (found as LocalDetected).ollama;
                  if (next?.models.includes(model)) {
                    setAvailableModels(next.models);
                    setTest({ kind: 'idle' });
                  }
                })
                .catch(() => {});
            }}
          />
        )}

      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 max-h-72 overflow-y-auto pr-1">
        {visiblePresets.map((p) => (
          <button
            key={p.id}
            type="button"
            onClick={() => pickProvider(p.id)}
            className={
              'p-3 text-left rounded-md border transition-colors ' +
              (provider === p.id
                ? 'border-amber-500/60 bg-amber-500/5'
                : 'border-zinc-800 bg-zinc-900/40 hover:bg-zinc-900/70')
            }
          >
            <div className="text-sm text-zinc-200">{p.label}</div>
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
          className="text-[11px] uppercase tracking-wider text-zinc-500 hover:text-zinc-300 transition-colors"
        >
          Show {hiddenCount} more providers ▾
        </button>
      )}
      {showAllProviders && (
        <button
          type="button"
          onClick={() => setShowAllProviders(false)}
          className="text-[11px] uppercase tracking-wider text-zinc-500 hover:text-zinc-300 transition-colors"
        >
          Show fewer ▴
        </button>
      )}

      <div className="space-y-3">
        <div>
          <label className="text-xs uppercase tracking-wider text-zinc-500 mb-1.5 block">URL</label>
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://api.openai.com/v1"
            className="w-full h-10 rounded-md border border-zinc-800 bg-zinc-900/60 px-3 text-sm text-zinc-200 placeholder-zinc-600 font-mono"
            spellCheck={false}
          />
        </div>

        <div>
          <div className="flex items-center justify-between mb-1.5">
            <label className="text-xs uppercase tracking-wider text-zinc-500">Model</label>
            <button
              type="button"
              onClick={onFetchModels}
              className="text-[11px] uppercase tracking-wider text-zinc-500 hover:text-zinc-300 transition-colors"
            >
              Fetch list
            </button>
          </div>
          <input
            list={`llm-models-${provider}`}
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder="gpt-4o-mini"
            className="w-full h-10 rounded-md border border-zinc-800 bg-zinc-900/60 px-3 text-sm text-zinc-200 placeholder-zinc-600 font-mono"
            spellCheck={false}
          />
          {availableModels.length > 0 && (
            <datalist id={`llm-models-${provider}`}>
              {availableModels.map((m) => <option key={m} value={m} />)}
            </datalist>
          )}
          {availableModels.length > 0 && (
            <div className="text-[11px] text-zinc-600 mt-1">
              {availableModels.length} models available — type to filter.
            </div>
          )}
        </div>

        {current.needsKey && (
          <div>
            <label className="text-xs uppercase tracking-wider text-zinc-500 mb-1.5 block">API key</label>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={current.keyPlaceholder}
              className="w-full h-10 rounded-md border border-zinc-800 bg-zinc-900/60 px-3 text-sm text-zinc-200 placeholder-zinc-600 font-mono"
              autoComplete="off"
              spellCheck={false}
            />
            <div className="text-[11px] text-zinc-600 mt-1">
              Stored in <code>config/orbis.yaml</code> on your machine.
              Never sent to protoLabsAI.
            </div>
          </div>
        )}

        <div className="flex items-center gap-2">
          <Button variant="secondary" size="sm" onClick={onTest} disabled={test.kind === 'checking'}>
            {test.kind === 'checking' ? 'Testing…' : 'Test connection'}
          </Button>
          {test.kind === 'ok' && (
            <span className="text-xs text-emerald-400">
              ✓ Connected ({test.latency} ms)
            </span>
          )}
          {test.kind === 'error' && (
            <span className="text-xs text-red-400 truncate max-w-[60%]">
              ✗ {test.message}
            </span>
          )}
        </div>

        {error && <div className="text-xs text-red-400">{error}</div>}
      </div>

      <div className="flex items-center justify-between">
        <Button variant="ghost" onClick={onBack}>Back</Button>
        <Button onClick={onContinue} disabled={saving}>
          {saving ? 'Saving…' : 'Continue'}
        </Button>
      </div>
    </div>
  );
}

/**
 * Shown on the LLM step when no local-LLM server is detected. Points
 * users at Ollama — the recommended path for the desktop app — with
 * copy-to-clipboard install one-liners per-OS. Non-blocking: users
 * can still pick a cloud provider from the grid below.
 */
function OllamaInstallHelper() {
  const [copied, setCopied] = useState<string | null>(null);
  const platform = detectOS();

  const commands: Record<OSKind, { label: string; cmd: string }> = {
    macos:   { label: 'macOS',   cmd: 'curl -fsSL https://ollama.com/install.sh | sh' },
    linux:   { label: 'Linux',   cmd: 'curl -fsSL https://ollama.com/install.sh | sh' },
    windows: { label: 'Windows', cmd: 'winget install Ollama.Ollama' },
  };

  const onCopy = async (key: string, cmd: string) => {
    try {
      await navigator.clipboard.writeText(cmd);
      setCopied(key);
      window.setTimeout(() => setCopied(null), 1500);
    } catch {
      // Browser might block clipboard in some contexts — UX falls back
      // to the visible text the user can select manually.
    }
  };

  return (
    <div className="rounded-md border border-amber-500/30 bg-amber-500/5 p-3 text-sm text-zinc-300">
      <div className="text-xs uppercase tracking-wider text-amber-400 mb-2">
        Recommended — Install Ollama
      </div>
      <p className="text-[13px] text-zinc-400 mb-3">
        ORBIS works best with a local LLM. Ollama is the fastest way to
        get one running; it's free, open-source, and auto-detected once
        installed.
      </p>
      <div className="space-y-2">
        {(['macos', 'linux', 'windows'] as const).map((k) => {
          const { label, cmd } = commands[k];
          const highlight = k === platform;
          const key = `cmd-${k}`;
          return (
            <div
              key={k}
              className={
                'flex items-center gap-2 rounded px-2.5 py-1.5 ' +
                (highlight
                  ? 'bg-amber-500/10 border border-amber-500/40'
                  : 'bg-zinc-900/40 border border-zinc-800')
              }
            >
              <span className="text-[10px] uppercase tracking-wider text-zinc-500 w-14 shrink-0">
                {label}
              </span>
              <code className="flex-1 font-mono text-[11px] text-zinc-200 truncate">
                {cmd}
              </code>
              <button
                type="button"
                onClick={() => onCopy(key, cmd)}
                className="text-[10px] uppercase tracking-wider text-zinc-500 hover:text-zinc-200 transition-colors shrink-0"
              >
                {copied === key ? 'Copied' : 'Copy'}
              </button>
            </div>
          );
        })}
      </div>
      <div className="text-[11px] text-zinc-500 mt-2">
        After install, reopen this step — we'll detect Ollama and offer
        to pull the recommended <code>gemma3n:e2b</code> model
        automatically.
      </div>
    </div>
  );
}

/**
 * Shown when Ollama IS detected but the recommended model isn't
 * installed. One-click pull through the backend's SSE proxy of
 * Ollama's `/api/pull`. Renders a progress bar from
 * `completed`/`total` byte counts; on success calls `onPulled` so
 * the parent can refresh its model list and advance.
 */
function ModelPullCallout({
  modelName,
  source,
  ollamaUrl,
  onPulled,
}: {
  modelName: string;
  /** 'ollama' uses /api/llm/pull; 'mlx' uses /api/llm/mlx/pull. */
  source: 'ollama' | 'mlx';
  /** Required when source='ollama'. */
  ollamaUrl?: string;
  onPulled: () => void;
}) {
  const [pulling, setPulling] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState('');
  const [completed, setCompleted] = useState(0);
  const [total, setTotal] = useState(0);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  const start = async () => {
    setPulling(true);
    setError(null);
    setDone(false);
    setStatus(source === 'mlx' ? 'starting download' : 'contacting Ollama');
    setCompleted(0);
    setTotal(0);
    const ac = new AbortController();
    abortRef.current = ac;
    try {
      const stream = source === 'mlx'
        ? pullMlxModel(modelName, { signal: ac.signal })
        : pullOllamaModel(modelName, ollamaUrl ?? 'http://127.0.0.1:11434', { signal: ac.signal });
      for await (const evt of stream) {
        if (evt.error) {
          setError(evt.error);
          continue;
        }
        if (evt.status) setStatus(evt.status);
        if (typeof evt.completed === 'number') setCompleted(evt.completed);
        if (typeof evt.total === 'number') setTotal(evt.total);
      }
      if (!ac.signal.aborted) {
        setDone(true);
        onPulled();
      }
    } catch (e) {
      if (!ac.signal.aborted) setError(String((e as Error).message ?? e));
    } finally {
      setPulling(false);
    }
  };

  const cancel = () => abortRef.current?.abort();
  const pct = total > 0 ? Math.round((completed / total) * 100) : 0;
  const mb = (n: number) => (n / (1024 * 1024)).toFixed(0);

  return (
    <div className="rounded-md border border-amber-500/30 bg-amber-500/5 p-3 text-sm text-zinc-300">
      <div className="text-xs uppercase tracking-wider text-amber-400 mb-2">
        {source === 'mlx' ? 'Built-in model — first run' : 'Recommended model not installed'}
      </div>
      <p className="text-[13px] text-zinc-400 mb-3">
        {source === 'mlx'
          ? <>Download <code className="text-zinc-200">{modelName}</code> from HuggingFace (~2-5 GB). One-time; cached locally for every future session.</>
          : <>Pull <code className="text-zinc-200">{modelName}</code> for the fastest local voice loop on this machine. ~5.6 GB; takes a few minutes on a normal connection.</>}
      </p>
      {!pulling && !done && (
        <div className="flex items-center justify-between gap-3">
          <div className="text-[11px] text-zinc-500">
            {error ? <span className="text-rose-400">{error}</span> : 'One-time download.'}
          </div>
          <Button onClick={start}>Pull {modelName}</Button>
        </div>
      )}
      {pulling && (
        <div className="space-y-2">
          <div className="flex items-center justify-between text-[11px]">
            <span className="text-zinc-400 truncate pr-2">{status}</span>
            <span className="text-zinc-500 tabular-nums shrink-0">
              {total > 0 ? `${mb(completed)} / ${mb(total)} MB · ${pct}%` : '…'}
            </span>
          </div>
          <div className="h-1.5 rounded-full bg-zinc-800 overflow-hidden">
            <div
              className="h-full bg-amber-500/80 transition-[width] duration-200"
              style={{ width: `${pct}%` }}
            />
          </div>
          <div className="flex justify-end">
            <Button variant="ghost" onClick={cancel}>Cancel</Button>
          </div>
        </div>
      )}
      {done && (
        <div className="text-sm text-emerald-400">
          ✓ {modelName} installed. You can continue.
        </div>
      )}
    </div>
  );
}

type OSKind = 'macos' | 'linux' | 'windows';

function detectOS(): OSKind {
  const ua = typeof navigator !== 'undefined' ? navigator.userAgent : '';
  if (/Mac|iPhone|iPad|iPod/i.test(ua)) return 'macos';
  if (/Windows/i.test(ua)) return 'windows';
  return 'linux';
}

function PickStep({ onNext, onBack }: { onNext: () => void; onBack: () => void }) {
  const [starters, setStarters] = useState<StarterOrb[] | null>(null);
  const [chosen, setChosen] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState<StarterOrb | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [committing, setCommitting] = useState(false);

  useEffect(() => {
    api.starterOrbs()
      .then((r) => setStarters(r.starters))
      .catch((e) => setError(String((e as Error).message ?? e)));
  }, []);

  const commitPick = async (slug: string) => {
    setCommitting(true);
    setError(null);
    try {
      const res = await api.selectStarter(slug);
      // Server-side write to config/orbis.yaml is necessary but not
      // sufficient — the orb store reads variant + palette from
      // localStorage on first mount and never re-syncs afterward, so
      // without this push the rendered orb stays on whatever's in
      // localStorage (defaults to fractal + Aurora) regardless of the
      // user's pick. Push immediately so the hatch animation and the
      // rest of the app reflect the selection.
      setVariant(res.starter.variant);
      applyPreset(res.starter.palette);
      onNext();
    } catch (e) {
      setCommitting(false);
      setError(String((e as Error).message ?? e));
    }
  };

  const onConfirm = async () => {
    if (!chosen) return;
    commitPick(chosen);
  };

  return (
    <div className="space-y-6">
      <div className="text-center space-y-2">
        <h2 className="text-lg text-zinc-200">Pick your orb</h2>
        <p className="text-sm text-zinc-500 max-w-md mx-auto">
          This is your starter. Tap a card to pick, or open a preview
          to see it live and drag to rotate.
        </p>
      </div>

      {error && <div className="text-xs text-red-400 text-center">{error}</div>}

      {starters === null ? (
        <div className="text-center text-zinc-500 text-sm">Loading pool…</div>
      ) : starters.length === 0 ? (
        <div className="text-center text-zinc-500 text-sm">
          No starters configured on the server.
        </div>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {starters.map((s) => (
            <StarterCard
              key={s.slug}
              starter={s}
              selected={chosen === s.slug}
              onSelect={() => setChosen(s.slug)}
              onPreview={() => setPreviewing(s)}
            />
          ))}
        </div>
      )}

      <div className="flex items-center justify-between">
        <Button variant="ghost" onClick={onBack}>Back</Button>
        <Button onClick={onConfirm} disabled={!chosen || committing}>
          {committing ? 'Saving…' : 'Continue'}
        </Button>
      </div>

      {previewing && (
        <OrbPreviewModal
          starter={previewing}
          onClose={() => setPreviewing(null)}
          onConfirm={() => {
            setPreviewing(null);
            commitPick(previewing.slug);
          }}
        />
      )}
    </div>
  );
}

function StarterCard({
  starter, selected, onSelect, onPreview,
}: {
  starter: StarterOrb;
  selected: boolean;
  onSelect: () => void;
  onPreview: () => void;
}) {
  const { primary, secondary } = paletteColors(starter.variant, starter.palette);
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && onSelect()}
      className={
        'relative flex flex-col items-start text-left p-3 rounded-lg border transition-colors cursor-pointer ' +
        (selected
          ? 'border-amber-500/60 bg-amber-500/5'
          : 'border-zinc-800 bg-zinc-900/40 hover:bg-zinc-900/70')
      }
    >
      {/* Gradient swatch derived from the palette's primary/secondary
          energy colors. Cheap visual hint — the actual shader is one
          click away via the preview button. Decorative but contains
          the focusable Preview button, so no aria-hidden. */}
      <div
        className="relative w-full aspect-square rounded-md mb-3 overflow-hidden border border-zinc-800/60"
        style={{
          background: `radial-gradient(circle at 35% 35%, ${primary} 0%, ${secondary} 55%, #0a0a0a 100%)`,
        }}
      >
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onPreview(); }}
          className="absolute bottom-1.5 right-1.5 h-7 px-2 text-[10px] uppercase tracking-wider rounded-md bg-black/60 hover:bg-black/80 text-zinc-200 backdrop-blur-sm border border-white/10 transition-colors"
          aria-label={`Preview ${starter.name}`}
        >
          Preview
        </button>
      </div>
      <div className="text-sm text-zinc-200">{starter.name}</div>
      <div className="text-[11px] text-zinc-500 mt-1 line-clamp-2">
        {starter.description}
      </div>
      <div className="text-[10px] text-zinc-600 mt-2 uppercase tracking-wider">
        {starter.variant} · {starter.palette}
      </div>
    </div>
  );
}


function MicStep({
  onNext,
  onBack,
}: {
  onNext: () => void;
  onBack: () => void;
}) {
  const [devices, setDevices] = useState<string[]>([]);
  const [device, setDevice] = useState<string>('');

  const onChangeDevice = (name: string) => {
    setDevice(name);
    setPreferredAudioDeviceId(name);
  };

  useEffect(() => {
    invoke<string[]>('list_audio_inputs')
      .then((devs) => {
        setDevices(devs);
        if (devs.length > 0) {
          const saved = getPreferredAudioDeviceId();
          setDevice(devs.includes(saved) ? saved : devs[0]);
        }
      })
      .catch(() => {});
  }, []);

  return (
    <div className="space-y-6">
      <div className="text-center space-y-2">
        <h2 className="text-lg text-zinc-200">Microphone</h2>
        <p className="text-sm text-zinc-500 max-w-sm mx-auto">
          ORBIS is voice-first. Select your input device and watch the level meter react to your voice.
        </p>
      </div>

      {devices.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-xs uppercase tracking-wider text-zinc-500">
            Input device
          </p>
          <Select value={device} onValueChange={onChangeDevice}>
            <SelectTrigger className="w-full bg-zinc-900 border-zinc-800">
              <SelectValue placeholder="System default" />
            </SelectTrigger>
            <SelectContent className="bg-zinc-900 border-zinc-800">
              {devices.map((name) => (
                <SelectItem key={name} value={name}>
                  {name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}

      <NativeLevelMeter deviceName={device} />

      <div className="flex justify-between pt-2">
        <Button variant="ghost" onClick={onBack}>Back</Button>
        <Button onClick={onNext}>Continue</Button>
      </div>
    </div>
  );
}

function DoneStep({ onFinish }: { onFinish: () => void }) {
  return (
    <div className="text-center space-y-6">
      <h2 className="text-lg text-zinc-200">Ready.</h2>
      <p className="text-sm text-zinc-500 max-w-sm mx-auto">
        Press Start in the main view to meet the orb. You can tweak
        anything from the settings drawer — voice, memory, access,
        profile.
      </p>
      <Button onClick={onFinish}>Let it hatch</Button>
    </div>
  );
}

/**
 * Hatch animation — a scripted 3-beat reveal:
 *   0.0-0.8s  black with a slow-pulse dot (the seed)
 *   0.8-2.4s  dot expands + soft flare
 *   2.4-3.6s  flare fades, main-app orb bleeds through
 *   3.6s+     dismiss; orb takes over
 *
 * Pure CSS animation — no shader work. A richer version (per-variant
 * shader-driven hatch) is tracked as a follow-up once the state/mood
 * authoring editor is in; for now this "dark → flare → reveal"
 * sequence is the minimum viable hatch that still feels like *an
 * event* and not just a close-the-wizard transition.
 */
function HatchAnimation({ onDone }: { onDone: () => void }) {
  useEffect(() => {
    const t = window.setTimeout(onDone, 3600);
    return () => window.clearTimeout(t);
  }, [onDone]);

  return (
    <div
      className="fixed inset-0 bg-[#0a0a0a] flex items-center justify-center pointer-events-none orbis-hatch-fade"
      aria-label="Hatching"
    >
      <div className="relative w-48 h-48">
        <div className="orbis-hatch-seed absolute inset-0 rounded-full" />
        <div className="orbis-hatch-flare absolute inset-0 rounded-full" />
      </div>
      <style>{`
        @keyframes orbis-hatch-fade {
          0% { opacity: 1; }
          82% { opacity: 1; }
          100% { opacity: 0; }
        }
        @keyframes orbis-hatch-seed {
          0% { transform: scale(0.08); opacity: 0.6; filter: blur(2px); }
          30% { transform: scale(0.14); opacity: 0.9; filter: blur(1px); }
          60% { transform: scale(0.5); opacity: 1; filter: blur(0px); }
          100% { transform: scale(1); opacity: 0; }
        }
        @keyframes orbis-hatch-flare {
          0% { transform: scale(0.1); opacity: 0; }
          55% { transform: scale(0.6); opacity: 0; }
          70% { transform: scale(1.4); opacity: 0.85; filter: blur(12px); }
          100% { transform: scale(3.4); opacity: 0; filter: blur(24px); }
        }
        .orbis-hatch-fade {
          animation: orbis-hatch-fade 3.6s ease-out forwards;
        }
        .orbis-hatch-seed {
          background: radial-gradient(circle, rgba(245,158,11,0.95) 0%, rgba(245,158,11,0.2) 55%, transparent 75%);
          animation: orbis-hatch-seed 3.2s cubic-bezier(.45,0,.25,1) forwards;
        }
        .orbis-hatch-flare {
          background: radial-gradient(circle, rgba(251,191,36,0.9) 0%, rgba(245,158,11,0.35) 40%, transparent 70%);
          animation: orbis-hatch-flare 3.2s cubic-bezier(.2,0,.2,1) forwards;
        }
      `}</style>
    </div>
  );
}
