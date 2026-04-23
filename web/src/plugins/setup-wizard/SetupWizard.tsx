import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { apiKeyStore } from '@/auth/apiKey';
import { api, type StarterOrb, UnauthorizedError } from '@/lib/api';

const STORAGE_COMPLETE = 'orbis.setupComplete';

type Step = 'welcome' | 'auth' | 'pick' | 'done';

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
          {step === 'done' && <DoneStep onFinish={onFinish} />}
        </div>
      </div>
    </div>
  );
}

// ── Indicator ──────────────────────────────────────────────────────────────

function StepIndicator({ current }: { current: Step }) {
  const order: Step[] = ['welcome', 'auth', 'pick', 'done'];
  const idx = order.indexOf(current);
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
