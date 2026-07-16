import { useEffect, useRef, useState } from 'react';
import { Panel } from '@/components/ui/panel';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { api, type OrbisConfig } from '@/lib/api';

interface TTSEndpointPreset {
  id: string;
  label: string;
  url: string;
  model: string;
  voice: string;
  blurb: string;
}

const TTS_OPENAI_PRESETS: TTSEndpointPreset[] = [
  {
    id: 'openai',
    label: 'OpenAI',
    url: 'https://api.openai.com/v1',
    model: 'tts-1',
    voice: 'alloy',
    blurb: 'OpenAI TTS.',
  },
  {
    id: 'protolabs',
    label: 'protoLabs (Fish)',
    url: 'https://api.proto-labs.ai/v1',
    model: 'fish-s2-pro',
    voice: 'protolabs/fish',
    blurb: 'Fish S2-Pro via protoLabs gateway. Same key as LLM.',
  },
];

function matchTtsPreset(url: string): string {
  if (!url) return 'openai';
  const u = url.toLowerCase();
  if (u.includes('proto-labs.ai')) return 'protolabs';
  if (u.includes('openai.com')) return 'openai';
  return 'custom';
}

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

type DownloadState =
  | { kind: 'idle' }
  | { kind: 'downloading' }
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

// How the voice field renders per backend:
//   select   — strict dropdown; backend has a finite known catalogue
//              the user can't extend (kokoro voices are baked into the
//              model, fish refs live on the sidecar).
//   datalist — input with autocomplete suggestions from the catalogue;
//              user can also type a custom voice. Used for openai-
//              compatible because gateways like the protoLabs gateway
//              expose their own voices on top of /v1/audio/speech and
//              we can't enumerate them from this side.
//   input    — plain text only; no known list (elevenlabs is per-
//              account and we don't ship the user's key).
type VoiceUI = 'select' | 'datalist' | 'input';
const VOICE_UI: Record<TTSBackend, VoiceUI> = {
  kokoro: 'select',
  openai: 'datalist',
  fish: 'select',
  elevenlabs: 'input',
};
const HAS_VOICE_LIST: Record<TTSBackend, boolean> = {
  kokoro: true,
  openai: true,
  fish: true,
  elevenlabs: false,
};

/**
 * TTS backend + voice selection. Writes persona.voice in config/orbis.yaml
 * via POST /api/config. For the openai backend the URL/Model/API-key
 * are also editable here so a user with a custom gateway (the protoLabs
 * gateway, LocalAI, etc.) doesn't have to drop into .env to repoint TTS.
 */
export function TTSSettings() {
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [backend, setBackend] = useState<TTSBackend>('kokoro');
  const [voice, setVoice] = useState('');
  // Listener-ack ("mm-hmm") toggle — only surfaced for the Fish backend
  // (the pipeline caps backchannels to Fish). Defaults on, matching the
  // pipeline's Fish default.
  const [backchannel, setBackchannel] = useState(true);
  const [voices, setVoices] = useState<Voice[]>([]);
  const [voicesLoading, setVoicesLoading] = useState(false);
  const [save, setSave] = useState<SaveState>({ kind: 'idle' });
  const [download, setDownload] = useState<DownloadState>({ kind: 'idle' });
  // OpenAI-compat endpoint overrides. ttsApiKey isn't preloaded — show
  // a placeholder hint instead so a saved key isn't echoed in the DOM.
  const [ttsUrl, setTtsUrl] = useState('');
  const [ttsModel, setTtsModel] = useState('');
  const [ttsApiKey, setTtsApiKey] = useState('');
  const [ttsKeyIsSet, setTtsKeyIsSet] = useState(false);
  const [ttsPreset, setTtsPreset] = useState<string>('openai');

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
        if (typeof v.backchannel === 'boolean') setBackchannel(v.backchannel);
        if (v.voice) setVoice(v.voice);
        if (v.tts_url) { setTtsUrl(v.tts_url); setTtsPreset(matchTtsPreset(v.tts_url)); }
        if (v.tts_model) setTtsModel(v.tts_model);
        // Don't preload the key into the input — show a "key is set"
        // hint instead so a saved key isn't echoed back into the DOM.
        const hasKey = typeof v.tts_api_key === 'string' && v.tts_api_key.length > 0;
        setTtsKeyIsSet(hasKey);
        setTtsApiKey('');
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

  const pickTtsPreset = (id: string) => {
    setTtsPreset(id);
    const p = TTS_OPENAI_PRESETS.find((x) => x.id === id);
    if (p) { setTtsUrl(p.url); setTtsModel(p.model); setVoice(p.voice); }
  };

  const onSave = async () => {
    setSave({ kind: 'saving' });
    try {
      const v: VoicePayload = { tts_backend: backend };
      if (voice.trim()) v.voice = voice.trim();
      // OpenAI-compat endpoint overrides — only included when the
      // openai backend is selected. Empty fields submit through as
      // "" → the server normalizes to null → loader falls back to env.
      if (backend === 'openai') {
        v.tts_url = ttsUrl.trim();
        v.tts_model = ttsModel.trim();
        // Only include the key when the user typed something — empty
        // string would clear it, which they likely don't want when a
        // key is already saved.
        if (ttsApiKey.trim()) v.tts_api_key = ttsApiKey.trim();
      }
      // Backchannel is Fish-only — only persist the toggle when Fish is the
      // active backend so we don't stamp a value the user never saw.
      if (backend === 'fish') v.backchannel = backchannel;
      await api.putConfig({ voice: v });
      setSave({ kind: 'saved' });
      if (ttsApiKey.trim()) {
        setTtsKeyIsSet(true);
        setTtsApiKey('');
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

  const onDownload = async () => {
    if (!voice.trim()) return;
    setDownload({ kind: 'downloading' });
    try {
      const r = await api.ttsDownloadVoice({ backend, voice: voice.trim() });
      if (!r.ok) {
        setDownload({ kind: 'error', message: r.error ?? 'download failed' });
        return;
      }
      setDownload({ kind: 'idle' });
      // Refresh the voice catalogue so the UI reflects the new cached
      // state and the download button disappears.
      try {
        const list = await api.ttsVoices(backend);
        setVoices(list.voices ?? []);
      } catch {
        /* non-fatal — next backend change will refetch */
      }
    } catch (e) {
      setDownload({ kind: 'error', message: String((e as Error).message ?? e) });
    }
  };

  // Voice currently selected (or first in list when no explicit choice).
  const selectedVoice = voices.find((v) => v.id === (voice || voices[0]?.id));
  // Only Kokoro supports an eager download — fish references are
  // server-managed, OpenAI/ElevenLabs are hosted.
  const canDownload =
    backend === 'kokoro' && selectedVoice?.cached === false &&
    download.kind !== 'downloading';

  if (loading) {
    return (
      <Panel title="Voice">
        <div className="text-xs text-fg-subtle">Loading…</div>
      </Panel>
    );
  }

  return (
    <Panel title="Voice">
      <div className="space-y-3">
        <p className="text-sm text-fg-muted -mt-1">
          How the orb speaks. Provider credentials stay env-only —
          see <code>.env.example</code>.
        </p>

        <div>
          <label className="text-xs uppercase tracking-wider text-fg-subtle mb-1 block">
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
          <div className="text-helper text-fg-faint mt-1">
            {BACKEND_BLURB[backend]}
          </div>
        </div>

        <div>
          <label className="text-xs uppercase tracking-wider text-fg-subtle mb-1 block">
            {backend === 'fish' ? 'Reference ID' : 'Voice'}
          </label>
          {VOICE_UI[backend] === 'select' && voices.length > 0 ? (
            <div className="flex items-center gap-2">
              <div className="flex-1 min-w-0">
                <Select value={voice || voices[0]?.id} onValueChange={setVoice}>
                  <SelectTrigger className="h-9 text-xs font-mono">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent className="max-h-72">
                    {voices.map((v) => (
                      <SelectItem key={v.id} value={v.id} className="font-mono">
                        {v.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              {canDownload && (
                <Button size="sm" variant="secondary" onClick={onDownload}>
                  Download
                </Button>
              )}
              {download.kind === 'downloading' && (
                <Button size="sm" variant="secondary" disabled>
                  Downloading…
                </Button>
              )}
            </div>
          ) : (
            <>
              <input
                list={
                  VOICE_UI[backend] === 'datalist' && voices.length > 0
                    ? `tts-voices-${backend}`
                    : undefined
                }
                value={voice}
                onChange={(e) => setVoice(e.target.value)}
                placeholder={VOICE_PLACEHOLDER[backend]}
                className="w-full h-9 rounded-md border border-edge bg-raised/60 px-2.5 text-xs text-fg-body placeholder-fg-muted font-mono"
                spellCheck={false}
              />
              {VOICE_UI[backend] === 'datalist' && voices.length > 0 && (
                <datalist id={`tts-voices-${backend}`}>
                  {voices.map((v) => <option key={v.id} value={v.id} />)}
                </datalist>
              )}
            </>
          )}
          {/* Helper text outside the dropdown — describes the selected
              voice's local cache state, fallback errors, etc. Kept
              compact so the panel doesn't grow vertically per voice. */}
          {VOICE_UI[backend] === 'select' && voices.length > 0 && download.kind === 'idle' && (
            <div className="text-helper text-fg-subtle mt-1">
              {selectedVoice?.cached === false
                ? 'Not yet downloaded — first synthesis will fetch it.'
                : selectedVoice?.cached === true
                  ? 'Cached locally.'
                  : null}
            </div>
          )}
          {VOICE_UI[backend] === 'datalist' && (
            <div className="text-helper text-fg-subtle mt-1">
              Suggestions are OpenAI's canonical 6 — type any voice your
              gateway accepts (e.g. <code>protolabs/fish</code>).
            </div>
          )}
          {download.kind === 'error' && (
            <div className="text-helper text-danger mt-1">
              Download failed: {download.message}
            </div>
          )}
          {VOICE_UI[backend] === 'select' && voices.length === 0 && !voicesLoading && (
            <div className="text-helper text-brand/80 mt-1">
              {backend === 'fish'
                ? 'No Fish references found — is the sidecar running on the configured URL?'
                : `No ${backend} voices available.`}
            </div>
          )}
        </div>

        {/* Listener acks — Fish-only. Kokoro's short-clip synthesis makes
            "mm-hmm" backchannels sound wrong, so the pipeline caps the
            feature to Fish and we only surface the toggle here for Fish. */}
        {backend === 'fish' && (
          <label className="flex items-start justify-between gap-4 pt-1">
            <span>
              <span className="block text-sm text-fg-body">Listener acks</span>
              <span className="block text-helper text-fg-subtle">
                Brief “mm-hmm” backchannels while you speak. Fish only —
                Kokoro’s short clips make them sound off.
              </span>
            </span>
            <Switch
              checked={backchannel}
              onCheckedChange={setBackchannel}
              aria-label="Listener acks"
              className="mt-0.5 shrink-0"
            />
          </label>
        )}

        {/* OpenAI-compat endpoint overrides — surface URL/Model/Key
            inline so a user with a custom gateway (the protoLabs gateway,
            LocalAI, vllm-omni) can drive TTS without an env edit and
            sidecar restart. Empty fields fall back to TTS_OPENAI_* env
            defaults at the server. */}
        {backend === 'openai' && (
          <div className="space-y-3 pt-1">
            <div className="grid grid-cols-2 gap-2">
              {TTS_OPENAI_PRESETS.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  onClick={() => pickTtsPreset(p.id)}
                  className={
                    'p-2 text-left rounded-md border transition-colors ' +
                    (ttsPreset === p.id
                      ? 'border-brand/60 bg-brand/5'
                      : 'border-edge bg-raised/40 hover:bg-raised/70')
                  }
                >
                  <div className="text-xs text-fg-body">{p.label}</div>
                  <div className="text-helper text-fg-subtle mt-0.5 line-clamp-2">{p.blurb}</div>
                </button>
              ))}
            </div>
            <div>
              <label className="text-xs uppercase tracking-wider text-fg-subtle mb-1 block">
                URL
              </label>
              <input
                value={ttsUrl}
                onChange={(e) => { setTtsUrl(e.target.value); setTtsPreset(matchTtsPreset(e.target.value)); }}
                placeholder="https://api.openai.com/v1"
                className="w-full h-9 rounded-md border border-edge bg-raised/60 px-2.5 text-xs text-fg-body placeholder-fg-muted font-mono"
                spellCheck={false}
              />
            </div>
            <div>
              <label className="text-xs uppercase tracking-wider text-fg-subtle mb-1 block">
                Model
              </label>
              <input
                value={ttsModel}
                onChange={(e) => setTtsModel(e.target.value)}
                placeholder="tts-1"
                className="w-full h-9 rounded-md border border-edge bg-raised/60 px-2.5 text-xs text-fg-body placeholder-fg-muted font-mono"
                spellCheck={false}
              />
            </div>
            <div>
              <label className="text-xs uppercase tracking-wider text-fg-subtle mb-1 block">
                API key
              </label>
              <input
                type="password"
                value={ttsApiKey}
                onChange={(e) => setTtsApiKey(e.target.value)}
                placeholder={
                  ttsKeyIsSet
                    ? 'key is already set — leave blank to keep'
                    : 'sk-...'
                }
                className="w-full h-9 rounded-md border border-edge bg-raised/60 px-2.5 text-xs text-fg-body placeholder-fg-muted font-mono"
                autoComplete="off"
                spellCheck={false}
              />
              <div className="text-helper text-fg-faint mt-1">
                {ttsKeyIsSet
                  ? 'A key is saved in config/orbis.yaml. Leave blank to keep it.'
                  : 'Stored in config/orbis.yaml on your machine.'}
              </div>
            </div>
          </div>
        )}

        <div className="flex items-center gap-2 pt-1">
          <Button size="sm" onClick={onSave} disabled={save.kind === 'saving'}>
            {save.kind === 'saving' ? 'Saving…' : 'Save'}
          </Button>
          {save.kind === 'saved' && (
            <span className="text-xs text-success">✓ Saved</span>
          )}
          {save.kind === 'error' && (
            <span className="text-xs text-danger truncate">
              ✗ {save.message}
            </span>
          )}
        </div>

        {loadError && (
          <div className="text-xs text-danger">
            Failed to load current config: {loadError}
          </div>
        )}
      </div>
    </Panel>
  );
}
