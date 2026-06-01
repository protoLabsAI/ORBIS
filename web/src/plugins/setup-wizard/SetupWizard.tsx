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
import {
  getAudioInputMode,
  usesSelectableInputDevice,
  type AudioInputMode,
} from '@/shared/audio/nativeAudio';
import {
  canRequestMicrophone,
  getMicrophonePermissionStatus,
  isMicrophoneAuthorized,
  needsMicrophoneSettings,
  openMicrophoneSettings,
  requestMicrophonePermission,
  type MicrophonePermissionStatus,
} from '@/shared/audio/microphonePermission';
import { OrbPreview } from '@/plugins/orb/OrbPreview';
import { orbStore } from '@/plugins/orb/store';

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
    <div className="fixed inset-0 z-30 bg-surface/95 backdrop-blur-sm overflow-y-auto">
        <WizardFlow
          onFinish={() => {
          try {
            localStorage.setItem(STORAGE_COMPLETE, 'true');
          } catch {
            // Ignore storage failures; setup completion is best-effort UI state.
          }
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
            (i < idx ? 'bg-brand/70 w-6' : i === idx ? 'bg-brand w-10' : 'bg-edge w-6')
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
      <h1 className="font-mono text-3xl tracking-wider text-fg-body">ORBIS</h1>
      <p className="text-base text-fg-muted max-w-sm mx-auto leading-relaxed">
        A voice companion. It talks back in real time, remembers you
        across sessions, and can route the heavy lifting to the agents
        you've configured.
      </p>
      <p className="text-xs text-fg-faint max-w-sm mx-auto">
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
        <h2 className="text-lg text-fg-body">Introductions</h2>
        <p className="text-base text-fg-muted max-w-md mx-auto">
          Both fields are optional — the orb will still work without
          them, just more generically.
        </p>
      </div>

      <div className="space-y-4">
        <div>
          <label className="text-xs uppercase tracking-wider text-fg-subtle mb-1.5 block">
            Your name — what the orb should call you
          </label>
          <input
            value={userName}
            onChange={(e) => setUserName(e.target.value)}
            placeholder="Alice"
            className="w-full h-10 rounded-md border border-edge bg-raised/60 px-3 text-sm text-fg-body placeholder-fg-faint"
            autoComplete="off"
            spellCheck={false}
          />
        </div>

        <div>
          <label className="text-xs uppercase tracking-wider text-fg-subtle mb-1.5 block">
            Orb's name — what you'll call the orb
          </label>
          <input
            value={orbName}
            onChange={(e) => setOrbName(e.target.value)}
            placeholder="ORBIS"
            className="w-full h-10 rounded-md border border-edge bg-raised/60 px-3 text-sm text-fg-body placeholder-fg-faint"
            autoComplete="off"
            spellCheck={false}
          />
          <div className="text-xs text-fg-faint mt-1">
            Defaults to ORBIS. Rename to whatever suits.
          </div>
        </div>

        {error && <div className="text-xs text-danger">{error}</div>}
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
        <h2 className="text-lg text-fg-body">Router brain</h2>
        <p className="text-base text-fg-muted max-w-md mx-auto">
          Small + fast is the right pick — this LLM handles conversation
          + routing decisions. Heavy reasoning delegates out.
        </p>
      </div>

      {localCallouts.length > 0 && (
        <div className="rounded-md border border-success/30 bg-success/5 p-3 text-sm text-fg-body">
          <div className="text-xs uppercase tracking-wider text-success mb-2">
            Detected on your machine
          </div>
          <div className="flex flex-wrap gap-2">
            {localCallouts.map((c) => (
              <button
                key={c.key}
                type="button"
                onClick={() => applyDetected(c.key)}
                className="px-3 py-1.5 rounded-md bg-success/10 hover:bg-success/20 border border-success/30 text-success text-xs transition-colors"
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
                ? 'border-brand/60 bg-brand/5'
                : 'border-edge bg-raised/40 hover:bg-raised/70')
            }
          >
            <div className="text-sm text-fg-body">{p.label}</div>
            <div className="text-xs text-fg-subtle mt-0.5 line-clamp-2">
              {p.blurb}
            </div>
          </button>
        ))}
      </div>

      {hiddenCount > 0 && (
        <button
          type="button"
          onClick={() => setShowAllProviders(true)}
          className="text-xs uppercase tracking-wider text-fg-subtle hover:text-fg-body transition-colors"
        >
          Show {hiddenCount} more providers ▾
        </button>
      )}
      {showAllProviders && (
        <button
          type="button"
          onClick={() => setShowAllProviders(false)}
          className="text-xs uppercase tracking-wider text-fg-subtle hover:text-fg-body transition-colors"
        >
          Show fewer ▴
        </button>
      )}

      <div className="space-y-3">
        <div>
          <label className="text-xs uppercase tracking-wider text-fg-subtle mb-1.5 block">URL</label>
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://api.openai.com/v1"
            className="w-full h-10 rounded-md border border-edge bg-raised/60 px-3 text-sm text-fg-body placeholder-fg-faint font-mono"
            spellCheck={false}
          />
        </div>

        <div>
          <div className="flex items-center justify-between mb-1.5">
            <label className="text-xs uppercase tracking-wider text-fg-subtle">Model</label>
            <button
              type="button"
              onClick={onFetchModels}
              className="text-xs uppercase tracking-wider text-fg-subtle hover:text-fg-body transition-colors"
            >
              Fetch list
            </button>
          </div>
          <input
            list={`llm-models-${provider}`}
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder="gpt-4o-mini"
            className="w-full h-10 rounded-md border border-edge bg-raised/60 px-3 text-sm text-fg-body placeholder-fg-faint font-mono"
            spellCheck={false}
          />
          {availableModels.length > 0 && (
            <datalist id={`llm-models-${provider}`}>
              {availableModels.map((m) => <option key={m} value={m} />)}
            </datalist>
          )}
          {availableModels.length > 0 && (
            <div className="text-xs text-fg-faint mt-1">
              {availableModels.length} models available — type to filter.
            </div>
          )}
        </div>

        {current.needsKey && (
          <div>
            <label className="text-xs uppercase tracking-wider text-fg-subtle mb-1.5 block">API key</label>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={current.keyPlaceholder}
              className="w-full h-10 rounded-md border border-edge bg-raised/60 px-3 text-sm text-fg-body placeholder-fg-faint font-mono"
              autoComplete="off"
              spellCheck={false}
            />
            <div className="text-xs text-fg-faint mt-1">
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
            <span className="text-xs text-success">
              ✓ Connected ({test.latency} ms)
            </span>
          )}
          {test.kind === 'error' && (
            <span className="text-xs text-danger truncate max-w-[60%]">
              ✗ {test.message}
            </span>
          )}
        </div>

        {error && <div className="text-xs text-danger">{error}</div>}
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
    <div className="rounded-md border border-brand/30 bg-brand/5 p-3 text-sm text-fg-body">
      <div className="text-xs uppercase tracking-wider text-brand mb-2">
        Recommended — Install Ollama
      </div>
      <p className="text-sm text-fg-muted mb-3">
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
                  ? 'bg-brand/10 border border-brand/40'
                  : 'bg-raised/40 border border-edge')
              }
            >
              <span className="text-helper uppercase tracking-wider text-fg-subtle w-14 shrink-0">
                {label}
              </span>
              <code className="flex-1 font-mono text-xs text-fg-body truncate">
                {cmd}
              </code>
              <button
                type="button"
                onClick={() => onCopy(key, cmd)}
                className="text-helper uppercase tracking-wider text-fg-subtle hover:text-fg-body transition-colors shrink-0"
              >
                {copied === key ? 'Copied' : 'Copy'}
              </button>
            </div>
          );
        })}
      </div>
      <div className="text-xs text-fg-subtle mt-2">
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
    <div className="rounded-md border border-brand/30 bg-brand/5 p-3 text-sm text-fg-body">
      <div className="text-xs uppercase tracking-wider text-brand mb-2">
        {source === 'mlx' ? 'Built-in model — first run' : 'Recommended model not installed'}
      </div>
      <p className="text-sm text-fg-muted mb-3">
        {source === 'mlx'
          ? <>Download <code className="text-fg-body">{modelName}</code> from HuggingFace (~2-5 GB). One-time; cached locally for every future session.</>
          : <>Pull <code className="text-fg-body">{modelName}</code> for the fastest local voice loop on this machine. ~5.6 GB; takes a few minutes on a normal connection.</>}
      </p>
      {!pulling && !done && (
        <div className="flex items-center justify-between gap-3">
          <div className="text-xs text-fg-subtle">
            {error ? <span className="text-rose-400">{error}</span> : 'One-time download.'}
          </div>
          <Button onClick={start}>Pull {modelName}</Button>
        </div>
      )}
      {pulling && (
        <div className="space-y-2">
          <div className="flex items-center justify-between text-xs">
            <span className="text-fg-muted truncate pr-2">{status}</span>
            <span className="text-fg-subtle tabular-nums shrink-0">
              {total > 0 ? `${mb(completed)} / ${mb(total)} MB · ${pct}%` : '…'}
            </span>
          </div>
          <div className="h-1.5 rounded-full bg-edge overflow-hidden">
            <div
              className="h-full bg-brand/80 transition-[width] duration-200"
              style={{ width: `${pct}%` }}
            />
          </div>
          <div className="flex justify-end">
            <Button variant="ghost" onClick={cancel}>Cancel</Button>
          </div>
        </div>
      )}
      {done && (
        <div className="text-sm text-success">
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
  const [index, setIndex] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [committing, setCommitting] = useState(false);
  const dragXRef = useRef<number | null>(null);
  const snapRef = useRef<{ variantId: string; palette: string; params: Record<string, unknown> } | null>(null);
  const committedRef = useRef(false);

  useEffect(() => {
    api.starterOrbs()
      .then((r) => setStarters(r.starters))
      .catch((e) => setError(String((e as Error).message ?? e)));
  }, []);

  const current = starters && starters.length > 0 ? starters[index] : null;

  // Snapshot the orb store on mount; restore on unmount unless the user
  // committed a pick (Continue) — so backing out of this step doesn't
  // leave the background orb stuck on the last-browsed (un-chosen) look.
  useEffect(() => {
    const snap = orbStore.get().getSnapshot();
    snapRef.current = {
      variantId: snap.variantId,
      palette: snap.palette,
      params: { ...snap.params },
    };
    return () => {
      const prev = snapRef.current;
      if (committedRef.current || !prev) return;
      const r = orbStore.get();
      r.setVariant(prev.variantId);
      r.setPreset(prev.palette);
      for (const [k, v] of Object.entries(prev.params)) r.setParam(k, v);
    };
  }, []);

  // Live preview: drive the shared orb store to the focused starter so
  // <OrbPreview/> renders the real shader — not a swatch. This is the
  // carousel: the orb you see IS the orb you'd pick.
  useEffect(() => {
    if (!current) return;
    const s = orbStore.get();
    s.setVariant(current.variant);
    s.setPreset(current.palette);
    for (const [k, v] of Object.entries(current.params ?? {})) s.setParam(k, v);
  }, [current]);

  const go = (dir: number) => {
    if (!starters || starters.length === 0) return;
    setIndex((i) => (i + dir + starters.length) % starters.length);
  };

  const onConfirm = async () => {
    if (!current) return;
    setCommitting(true);
    setError(null);
    try {
      const res = await api.selectStarter(current.slug);
      // Push the pick into the orb store + backend so the hatch and the
      // rest of the app reflect it (the store reads localStorage once on
      // mount and never re-syncs). committedRef keeps the unmount cleanup
      // above from reverting it.
      committedRef.current = true;
      setVariant(res.starter.variant);
      applyPreset(res.starter.palette);
      onNext();
    } catch (e) {
      setCommitting(false);
      setError(String((e as Error).message ?? e));
    }
  };

  return (
    <div className="space-y-6">
      <div className="text-center space-y-2">
        <h2 className="text-lg text-fg-body">Pick your orb</h2>
        <p className="text-base text-fg-muted max-w-md mx-auto">
          Swipe or use the arrows to browse — this is the real thing,
          live. Drag the orb to rotate it.
        </p>
      </div>

      {error && <div className="text-xs text-danger text-center">{error}</div>}

      {starters === null ? (
        <div className="text-center text-fg-subtle text-sm">Loading pool…</div>
      ) : starters.length === 0 ? (
        <div className="text-center text-fg-subtle text-sm">
          No starters configured on the server.
        </div>
      ) : (
        <div className="flex flex-col items-center gap-3">
          <div className="relative flex w-full items-center justify-center">
            <button
              type="button"
              onClick={() => go(-1)}
              aria-label="Previous orb"
              className="absolute left-0 z-10 grid h-9 w-9 place-items-center rounded-full border border-edge bg-raised/70 text-lg text-fg-body hover:bg-edge"
            >‹</button>

            {/* The live orb. The variant handles drag-to-rotate itself;
                a deliberate horizontal flick (≥60px) flips starters. */}
            <div
              className="relative aspect-square w-56 overflow-hidden rounded-xl border border-edge bg-black"
              onPointerDown={(e) => { dragXRef.current = e.clientX; }}
              onPointerUp={(e) => {
                if (dragXRef.current === null) return;
                const dx = e.clientX - dragXRef.current;
                dragXRef.current = null;
                if (dx <= -60) go(1);
                else if (dx >= 60) go(-1);
              }}
            >
              <OrbPreview />
            </div>

            <button
              type="button"
              onClick={() => go(1)}
              aria-label="Next orb"
              className="absolute right-0 z-10 grid h-9 w-9 place-items-center rounded-full border border-edge bg-raised/70 text-lg text-fg-body hover:bg-edge"
            >›</button>
          </div>

          <div className="min-h-[2.75rem] text-center">
            <div className="font-mono text-sm uppercase tracking-wider text-fg-body">
              {current?.name}
            </div>
            <div className="mx-auto mt-0.5 max-w-xs text-xs text-fg-subtle">
              {current?.description}
            </div>
          </div>

          <div className="flex items-center justify-center gap-1.5">
            {starters.map((s, i) => (
              <button
                key={s.slug}
                type="button"
                aria-label={`Show ${s.name}`}
                onClick={() => setIndex(i)}
                className={
                  'h-1.5 rounded-full transition-all ' +
                  (i === index ? 'w-5 bg-brand' : 'w-1.5 bg-edge hover:bg-fg-faint')
                }
              />
            ))}
          </div>
        </div>
      )}

      <div className="flex items-center justify-between">
        <Button variant="ghost" onClick={onBack}>Back</Button>
        <Button onClick={onConfirm} disabled={!current || committing}>
          {committing ? 'Saving…' : 'Use this orb'}
        </Button>
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
  const [permission, setPermission] =
    useState<MicrophonePermissionStatus>('not_determined');
  const [audioInputMode, setAudioInputMode] =
    useState<AudioInputMode>('unsupported');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onChangeDevice = (name: string) => {
    setDevice(name);
    setPreferredAudioDeviceId(name);
  };

  const refreshDevices = async () => {
    const devs = await invoke<string[]>('list_audio_inputs');
    setDevices(devs);
    if (devs.length > 0) {
      const saved = getPreferredAudioDeviceId();
      setDevice(devs.includes(saved) ? saved : devs[0]);
    }
  };

  const refreshPermission = async () => {
    setError(null);
    const [status, mode] = await Promise.all([
      getMicrophonePermissionStatus(),
      getAudioInputMode(),
    ]);
    setPermission(status);
    setAudioInputMode(mode);
    if (isMicrophoneAuthorized(status) && usesSelectableInputDevice(mode)) {
      await refreshDevices();
    } else {
      setDevices([]);
      setDevice('');
    }
  };

  useEffect(() => {
    queueMicrotask(() => {
      refreshPermission().catch((e) => {
        setError(String((e as Error).message ?? e));
      });
    });
    // Initial Tauri permission probe runs once when this wizard step mounts.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onRequestPermission = async () => {
    setBusy(true);
    setError(null);
    try {
      const status = await requestMicrophonePermission();
      setPermission(status);
      const mode = await getAudioInputMode();
      setAudioInputMode(mode);
      if (isMicrophoneAuthorized(status) && usesSelectableInputDevice(mode)) {
        await refreshDevices();
      } else {
        setDevices([]);
        setDevice('');
      }
    } catch (e) {
      setError(String((e as Error).message ?? e));
    } finally {
      setBusy(false);
    }
  };

  const onOpenSettings = async () => {
    setBusy(true);
    setError(null);
    try {
      await openMicrophoneSettings();
    } catch (e) {
      setError(String((e as Error).message ?? e));
    } finally {
      setBusy(false);
    }
  };

  const onRefreshPermission = async () => {
    setBusy(true);
    try {
      await refreshPermission();
    } catch (e) {
      setError(String((e as Error).message ?? e));
    } finally {
      setBusy(false);
    }
  };

  const authorized = isMicrophoneAuthorized(permission);
  const selectableInput = usesSelectableInputDevice(audioInputMode);
  const canContinue = authorized && (!selectableInput || devices.length > 0);

  return (
    <div className="space-y-6">
      <div className="text-center space-y-2">
        <h2 className="text-lg text-fg-body">Microphone</h2>
        <p className="text-base text-fg-muted max-w-sm mx-auto">
          ORBIS needs microphone access before the native audio engine starts.
        </p>
      </div>

      {!authorized && (
        <div className="rounded-lg border border-edge bg-surface/60 p-4 space-y-3">
          <div>
            <div className="text-sm text-fg-body">Microphone access</div>
            <div className="text-xs text-fg-subtle mt-1">
              Current status: {permission.replace('_', ' ')}
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {canRequestMicrophone(permission) && (
              <Button onClick={onRequestPermission} disabled={busy}>
                {busy ? 'Waiting…' : 'Allow microphone'}
              </Button>
            )}
            {needsMicrophoneSettings(permission) && (
              <>
                <Button onClick={onOpenSettings} disabled={busy}>
                  Open Settings
                </Button>
                <Button variant="ghost" onClick={onRefreshPermission} disabled={busy}>
                  Recheck
                </Button>
              </>
            )}
          </div>
        </div>
      )}

      {authorized && !selectableInput && (
        <div className="rounded-lg border border-edge bg-surface/60 p-4 text-sm text-fg-muted">
          Input source: macOS system input
        </div>
      )}

      {authorized && selectableInput && devices.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-xs uppercase tracking-wider text-fg-subtle">
            Input device
          </p>
          <Select value={device} onValueChange={onChangeDevice}>
            <SelectTrigger className="w-full bg-raised border-edge">
              <SelectValue placeholder="System default" />
            </SelectTrigger>
            <SelectContent className="bg-raised border-edge">
              {devices.map((name) => (
                <SelectItem key={name} value={name}>
                  {name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}

      {authorized && selectableInput && devices.length === 0 && (
        <div className="rounded-lg border border-edge bg-surface/60 p-4 text-sm text-fg-muted">
          No microphone input device was found.
        </div>
      )}

      {authorized && <NativeLevelMeter deviceName={device || audioInputMode} />}

      {error && (
        <p className="text-xs text-danger text-center">
          {error}
        </p>
      )}

      <div className="flex justify-between pt-2">
        <Button variant="ghost" onClick={onBack}>Back</Button>
        <Button onClick={onNext} disabled={!canContinue}>Continue</Button>
      </div>
    </div>
  );
}

function DoneStep({ onFinish }: { onFinish: () => void }) {
  return (
    <div className="text-center space-y-6">
      <h2 className="text-lg text-fg-body">Ready.</h2>
      <p className="text-base text-fg-muted max-w-sm mx-auto">
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
      className="fixed inset-0 bg-surface flex items-center justify-center pointer-events-none orbis-hatch-fade"
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
          background: radial-gradient(circle, rgba(155,135,242,0.95) 0%, rgba(155,135,242,0.2) 55%, transparent 75%);
          animation: orbis-hatch-seed 3.2s cubic-bezier(.45,0,.25,1) forwards;
        }
        .orbis-hatch-flare {
          background: radial-gradient(circle, rgba(196,181,253,0.9) 0%, rgba(155,135,242,0.35) 40%, transparent 70%);
          animation: orbis-hatch-flare 3.2s cubic-bezier(.2,0,.2,1) forwards;
        }
      `}</style>
    </div>
  );
}
