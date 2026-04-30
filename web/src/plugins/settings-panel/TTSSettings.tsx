import { useEffect, useRef, useState } from 'react';
import { Panel } from '@/components/ui/panel';
import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { api, type OrbisConfig } from '@/lib/api';

type TTSBackend = 'kokoro' | 'openai' | 'elevenlabs' | 'fish';
type VoicePayload = NonNullable<OrbisConfig['voice']>;
type Voice = { id: string; label: string; cached?: boolean };

const VALID_BACKENDS: readonly TTSBackend[] = [
  'kokoro', 'openai', 'elevenlabs', 'fish',
] as const;

const isValidBackend = (v: unknown): v is TTSBackend =>
  typeof v === 'string' && (VALID_BACKENDS as readonly string[]).includes(v);

type SaveState =
  | { kind: 'idle' }
  | { kind: 'saving' }
  | { kind: 'saved' }
  | { kind: 'error'; message: string };

const BACKEND_BLURB: Record<TTSBackend, string> = {
  kokoro: 'Local, CPU-friendly. No GPU required.',
  openai: 'Any OpenAI-compatible /v1/audio/speech endpoint.',
  elevenlabs: 'Highest quality via native WebSocket. Paid.',
  fish: 'Opt-in sidecar. Cloneable voices, needs GPU.',
};

const VOICE_PLACEHOLDER: Record<TTSBackend, string> = {
  kokoro: 'af_heart',
  openai: 'alloy',
  elevenlabs: '21m00Tcm4TlvDq8ikWAM',
  fish: 'reference id',
};

// Backends where we ship a known catalogue and want to surface a
// Select instead of free text. ElevenLabs voices are per-account so
// we keep that backend free-typeable.
const HAS_VOICE_LIST: Record<TTSBackend, boolean> = {
  kokoro: true,
  openai: true,
  fish: true,
  elevenlabs: false,
};

/**
 * TTS backend + voice selection. Writes persona.voice in config/orbis.yaml
 * via POST /api/config. Actual TTS-provider credentials (OpenAI key,
 * ElevenLabs key, Fish URL) stay env-only — surface them in docs not UI.
 */
export function TTSSettings() {
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [backend, setBackend] = useState<TTSBackend>('kokoro');
  const [voice, setVoice] = useState('');
  const [voices, setVoices] = useState<Voice[]>([]);
  const [voicesLoading, setVoicesLoading] = useState(false);
  const [save, setSave] = useState<SaveState>({ kind: 'idle' });

  // Tracks the "saved" → "idle" reset timer so it can be cancelled on
  // unmount (and before a new save re-arms it).
  const saveResetTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    return () => {
      if (saveResetTimerRef.current) clearTimeout(saveResetTimerRef.current);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    api.config()
      .then((r) => {
        if (cancelled) return;
        const v = r.config?.voice ?? {};
        // Only accept known backends — if the server ships a newer one
        // we haven't listed yet, fall back to kokoro rather than lying
        // to the user via a coerced cast.
        if (isValidBackend(v.tts_backend)) setBackend(v.tts_backend);
        if (v.voice) setVoice(v.voice);
      })
      .catch((e) => {
        if (!cancelled) setLoadError(String((e as Error).message ?? e));
        console.error('[settings/tts] failed to load config', e);
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  // Refresh the voice catalogue whenever the backend changes (and on
  // first mount). Empty list = free-text fallback in the UI.
  useEffect(() => {
    if (!HAS_VOICE_LIST[backend]) {
      setVoices([]);
      return;
    }
    let cancelled = false;
    setVoicesLoading(true);
    api.ttsVoices(backend)
      .then((r) => {
        if (cancelled) return;
        setVoices(r.voices ?? []);
      })
      .catch((e) => {
        if (cancelled) return;
        console.warn('[settings/tts] failed to list voices', e);
        setVoices([]);
      })
      .finally(() => {
        if (!cancelled) setVoicesLoading(false);
      });
    return () => { cancelled = true; };
  }, [backend]);

  const onSave = async () => {
    setSave({ kind: 'saving' });
    try {
      const v: VoicePayload = { tts_backend: backend };
      if (voice.trim()) v.voice = voice.trim();
      await api.putConfig({ voice: v });
      setSave({ kind: 'saved' });
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
      <Panel title="TTS">
        <div className="text-xs text-zinc-500">Loading…</div>
      </Panel>
    );
  }

  return (
    <Panel title="TTS">
      <div className="space-y-3">
        <p className="text-xs text-zinc-500 -mt-1">
          How the orb speaks. Provider credentials stay env-only —
          see <code>.env.example</code>.
        </p>

        <div>
          <label className="text-[11px] uppercase tracking-wider text-zinc-500 mb-1 block">
            Backend
          </label>
          <Select value={backend} onValueChange={(v) => setBackend(v as TTSBackend)}>
            <SelectTrigger className="h-9 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="kokoro">Kokoro (local, CPU)</SelectItem>
              <SelectItem value="openai">OpenAI-compatible</SelectItem>
              <SelectItem value="elevenlabs">ElevenLabs</SelectItem>
              <SelectItem value="fish">Fish S2-Pro (sidecar)</SelectItem>
            </SelectContent>
          </Select>
          <div className="text-[10px] text-zinc-600 mt-1">
            {BACKEND_BLURB[backend]}
          </div>
        </div>

        <div>
          <label className="text-[11px] uppercase tracking-wider text-zinc-500 mb-1 block">
            {backend === 'fish' ? 'Reference ID' : 'Voice'}
          </label>
          {HAS_VOICE_LIST[backend] && voices.length > 0 ? (
            <Select value={voice || voices[0]?.id} onValueChange={setVoice}>
              <SelectTrigger className="h-9 text-xs font-mono">
                <SelectValue />
              </SelectTrigger>
              <SelectContent className="max-h-72">
                {voices.map((v) => (
                  <SelectItem key={v.id} value={v.id}>
                    <span className="font-mono">{v.label}</span>
                    {v.cached === false && (
                      <span className="text-[10px] text-zinc-500 ml-2">
                        (downloads on use)
                      </span>
                    )}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <input
              value={voice}
              onChange={(e) => setVoice(e.target.value)}
              placeholder={VOICE_PLACEHOLDER[backend]}
              className="w-full h-9 rounded-md border border-zinc-800 bg-zinc-900/60 px-2.5 text-xs text-zinc-200 placeholder-zinc-600 font-mono"
              spellCheck={false}
            />
          )}
          {HAS_VOICE_LIST[backend] && voices.length === 0 && !voicesLoading && (
            <div className="text-[10px] text-amber-500/80 mt-1">
              {backend === 'fish'
                ? 'No Fish references found — is the sidecar running on the configured URL?'
                : `No ${backend} voices available.`}
            </div>
          )}
        </div>

        <div className="flex items-center gap-2 pt-1">
          <Button size="sm" onClick={onSave} disabled={save.kind === 'saving'}>
            {save.kind === 'saving' ? 'Saving…' : 'Save'}
          </Button>
          {save.kind === 'saved' && (
            <span className="text-[11px] text-emerald-400">✓ Saved</span>
          )}
          {save.kind === 'error' && (
            <span className="text-[11px] text-red-400 truncate">
              ✗ {save.message}
            </span>
          )}
        </div>

        {loadError && (
          <div className="text-[11px] text-red-400">
            Failed to load current config: {loadError}
          </div>
        )}
      </div>
    </Panel>
  );
}
