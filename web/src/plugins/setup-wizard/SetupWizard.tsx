import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { api, type StarterOrb } from '@/lib/api';
import { OrbPreviewModal } from './OrbPreviewModal';
import { paletteColors } from './paletteColors';

const STORAGE_COMPLETE = 'orbis.setupComplete';

type Step = 'welcome' | 'names' | 'llm' | 'pick' | 'done' | 'hatching';

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
              onNext={() => setStep('done')}
              onBack={() => setStep('llm')}
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
  const order: Step[] = ['welcome', 'names', 'llm', 'pick', 'done'];
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

type LLMProvider = 'openai' | 'anthropic' | 'groq' | 'gateway' | 'local' | 'custom';

interface LLMPreset {
  id: LLMProvider;
  label: string;
  url: string;
  model: string;
  needsKey: boolean;
  keyPlaceholder?: string;
  blurb: string;
}

const LLM_PRESETS: LLMPreset[] = [
  {
    id: 'openai',
    label: 'OpenAI',
    url: 'https://api.openai.com/v1',
    model: 'gpt-4o-mini',
    needsKey: true,
    keyPlaceholder: 'sk-...',
    blurb: 'Fast + cheap. A few cents per hour of chatter.',
  },
  {
    id: 'anthropic',
    label: 'Anthropic',
    url: 'https://api.anthropic.com/v1',
    model: 'claude-haiku-4-5',
    needsKey: true,
    keyPlaceholder: 'sk-ant-...',
    blurb: 'Claude Haiku. Great personality, slightly pricier.',
  },
  {
    id: 'groq',
    label: 'Groq',
    url: 'https://api.groq.com/openai/v1',
    model: 'llama-3.1-8b-instant',
    needsKey: true,
    keyPlaceholder: 'gsk_...',
    blurb: 'Blazing fast, near-free. Smaller model.',
  },
  {
    id: 'gateway',
    label: 'LiteLLM / gateway',
    url: 'http://localhost:4000/v1',
    model: 'gpt-4o-mini',
    needsKey: true,
    keyPlaceholder: 'gateway master key',
    blurb: 'Any OpenAI-compatible gateway you run.',
  },
  {
    id: 'local',
    label: 'Local (vLLM)',
    url: 'http://127.0.0.1:8100/v1',
    model: 'Qwen/Qwen3.5-4B',
    needsKey: false,
    blurb: 'Offline. Run your own vLLM / LM Studio / ollama.',
  },
  {
    id: 'custom',
    label: 'Custom',
    url: '',
    model: '',
    needsKey: true,
    keyPlaceholder: 'api key (optional)',
    blurb: 'Paste your own URL + model.',
  },
];

function LLMStep({ onNext, onBack }: { onNext: () => void; onBack: () => void }) {
  const [provider, setProvider] = useState<LLMProvider>('openai');
  const [url, setUrl] = useState(LLM_PRESETS[0].url);
  const [model, setModel] = useState(LLM_PRESETS[0].model);
  const [apiKey, setApiKey] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const current = LLM_PRESETS.find((p) => p.id === provider) ?? LLM_PRESETS[0];

  const pickProvider = (next: LLMProvider) => {
    setProvider(next);
    const preset = LLM_PRESETS.find((p) => p.id === next) ?? LLM_PRESETS[0];
    setUrl(preset.url);
    setModel(preset.model);
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

  return (
    <div className="space-y-6">
      <div className="text-center space-y-2">
        <h2 className="text-lg text-zinc-200">Router brain</h2>
        <p className="text-sm text-zinc-500 max-w-md mx-auto">
          Small + fast is the right pick — this LLM handles
          conversation + routing decisions. Heavy reasoning comes from
          whatever agent you delegate to.
        </p>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
        {LLM_PRESETS.map((p) => (
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
          <label className="text-xs uppercase tracking-wider text-zinc-500 mb-1.5 block">Model</label>
          <input
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder="gpt-4o-mini"
            className="w-full h-10 rounded-md border border-zinc-800 bg-zinc-900/60 px-3 text-sm text-zinc-200 placeholder-zinc-600 font-mono"
            spellCheck={false}
          />
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
      await api.selectStarter(slug);
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
          click away via the preview button. */}
      <div
        aria-hidden
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
