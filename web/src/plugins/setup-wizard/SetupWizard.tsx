import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { apiKeyStore } from '@/auth/apiKey';
import { api, type StarterOrb, UnauthorizedError } from '@/lib/api';

const STORAGE_COMPLETE = 'orbis.setupComplete';

type Step = 'welcome' | 'auth' | 'pick' | 'done' | 'hatching';

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
          {step === 'welcome' && <WelcomeStep onNext={() => setStep('auth')} />}
          {step === 'auth' && (
            <AuthStep
              onNext={() => setStep('pick')}
              onBack={() => setStep('welcome')}
            />
          )}
          {step === 'pick' && (
            <PickStep
              onNext={() => setStep('done')}
              onBack={() => setStep('auth')}
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
  const order: Step[] = ['welcome', 'auth', 'pick', 'done'];
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

function AuthStep({ onNext, onBack }: { onNext: () => void; onBack: () => void }) {
  const [key, setKey] = useState<string>(apiKeyStore.get() ?? '');
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  const onContinue = async () => {
    setError(null);
    if (!key.trim()) {
      // Allow skipping on single-user installs. Server will accept
      // anonymously when config/users.yaml is absent.
      apiKeyStore.clear();
      onNext();
      return;
    }
    setChecking(true);
    apiKeyStore.set(key);
    try {
      await api.whoami();
      onNext();
    } catch (e) {
      setChecking(false);
      if (e instanceof UnauthorizedError) {
        setError(
          'That key doesn\'t match. Check config/users.yaml on the server.'
        );
      } else {
        // Network errors etc. — let the user proceed; they can fix
        // the key later from the drawer.
        setError(
          'Could not reach the server to verify. Saved locally; continue anyway or go back.'
        );
      }
    }
  };

  return (
    <div className="space-y-6">
      <div className="text-center space-y-2">
        <h2 className="text-lg text-zinc-200">Access</h2>
        <p className="text-sm text-zinc-500 max-w-md mx-auto">
          If you're hosting on a tailnet, paste your owner API key from
          <code className="mx-1 text-xs text-zinc-400">config/users.yaml</code>.
          Leave empty if you're running standalone.
        </p>
      </div>
      <div className="space-y-2">
        <input
          type="password"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          placeholder="pv_ak_... (optional)"
          className="w-full h-10 rounded-md border border-zinc-800 bg-zinc-900/60 px-3 text-sm text-zinc-200 placeholder-zinc-600"
          autoComplete="off"
          spellCheck={false}
        />
        {error && <div className="text-xs text-red-400">{error}</div>}
      </div>
      <div className="flex items-center justify-between">
        <Button variant="ghost" onClick={onBack}>Back</Button>
        <Button onClick={onContinue} disabled={checking}>
          {checking ? 'Checking…' : key.trim() ? 'Continue' : 'Skip'}
        </Button>
      </div>
    </div>
  );
}

function PickStep({ onNext, onBack }: { onNext: () => void; onBack: () => void }) {
  const [starters, setStarters] = useState<StarterOrb[] | null>(null);
  const [chosen, setChosen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [committing, setCommitting] = useState(false);

  useEffect(() => {
    api.starterOrbs()
      .then((r) => setStarters(r.starters))
      .catch((e) => setError(String((e as Error).message ?? e)));
  }, []);

  const onConfirm = async () => {
    if (!chosen) return;
    setCommitting(true);
    setError(null);
    try {
      await api.selectStarter(chosen);
      onNext();
    } catch (e) {
      setCommitting(false);
      setError(String((e as Error).message ?? e));
    }
  };

  return (
    <div className="space-y-6">
      <div className="text-center space-y-2">
        <h2 className="text-lg text-zinc-200">Pick your orb</h2>
        <p className="text-sm text-zinc-500 max-w-md mx-auto">
          This is your starter. Its look is yours until you unlock the
          full editor — it becomes part of how the orb feels.
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
            <button
              key={s.slug}
              type="button"
              onClick={() => setChosen(s.slug)}
              className={
                'flex flex-col items-start text-left p-3 rounded-lg border transition-colors ' +
                (chosen === s.slug
                  ? 'border-amber-500/60 bg-amber-500/5'
                  : 'border-zinc-800 bg-zinc-900/40 hover:bg-zinc-900/70')
              }
            >
              <div className="text-sm text-zinc-200">{s.name}</div>
              <div className="text-[11px] text-zinc-500 mt-1 line-clamp-3">
                {s.description}
              </div>
              <div className="text-[10px] text-zinc-600 mt-2 uppercase tracking-wider">
                {s.variant} · {s.palette}
              </div>
            </button>
          ))}
        </div>
      )}

      <div className="flex items-center justify-between">
        <Button variant="ghost" onClick={onBack}>Back</Button>
        <Button onClick={onConfirm} disabled={!chosen || committing}>
          {committing ? 'Saving…' : 'Continue'}
        </Button>
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
