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

type STTBackend = 'local' | 'openai' | 'sensevoice';
type STTPayload = NonNullable<OrbisConfig['stt']>;

const VALID_BACKENDS: readonly STTBackend[] = ['local', 'openai', 'sensevoice'] as const;
const isValidBackend = (v: unknown): v is STTBackend =>
  typeof v === 'string' && (VALID_BACKENDS as readonly string[]).includes(v);

const BACKEND_BLURB: Record<STTBackend, string> = {
  local: 'In-process Whisper on Apple Silicon (MPS). Segmented per VAD turn, no streaming. Requires the [whisper] pip extra.',
  openai: 'OpenAI-compatible /v1/audio/transcriptions — point at OpenAI proper, the protoLabs gateway, LocalAI, etc. Default when [whisper] is not installed.',
  sensevoice: 'FunAudioLLM SenseVoice (opt-in via [sensevoice] extra). Transcription + emotion + audio events in one pass.',
};

interface STTEndpointPreset {
  id: string;
  label: string;
  url: string;
  model: string;
  blurb: string;
}

const STT_OPENAI_PRESETS: STTEndpointPreset[] = [
  {
    id: 'openai',
    label: 'OpenAI',
    url: 'https://api.openai.com/v1',
    model: 'whisper-1',
    blurb: 'OpenAI Whisper endpoint.',
  },
  {
    id: 'protolabs',
    label: 'protoLabs',
    url: 'https://api.proto-labs.ai/v1',
    model: 'whisper-1',
    blurb: 'faster-whisper on the protoLabs gateway. Same key as LLM.',
  },
];

function matchSttPreset(url: string): string {
  if (!url) return 'openai';
  const u = url.toLowerCase();
  if (u.includes('proto-labs.ai')) return 'protolabs';
  if (u.includes('openai.com')) return 'openai';
  return 'custom';
}

type SaveState =
  | { kind: 'idle' }
  | { kind: 'saving' }
  | { kind: 'saved' }
  | { kind: 'error'; message: string };

/**
 * STT backend + per-backend overrides. Saves to ``stt`` in
 * config/orbis.yaml. The ``local`` backend's whisper_model takes
 * effect at server boot only (module-cached pipe); ``openai``
 * url/model/api_key take effect on the next session.
 */
export function STTSettings() {
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [backend, setBackend] = useState<STTBackend>('local');
  const [whisperModel, setWhisperModel] = useState('');
  const [sttUrl, setSttUrl] = useState('');
  const [sttModel, setSttModel] = useState('');
  const [sttApiKey, setSttApiKey] = useState('');
  const [keyIsSet, setKeyIsSet] = useState(false);
  const [sttPreset, setSttPreset] = useState<string>('openai');
  const [save, setSave] = useState<SaveState>({ kind: 'idle' });

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
        const s = r.config?.stt ?? {};
        if (isValidBackend(s.backend)) setBackend(s.backend);
        if (s.whisper_model) setWhisperModel(s.whisper_model);
        if (s.url) { setSttUrl(s.url); setSttPreset(matchSttPreset(s.url)); }
        if (s.model) setSttModel(s.model);
        const hasKey = typeof s.api_key === 'string' && s.api_key.length > 0;
        setKeyIsSet(hasKey);
        setSttApiKey('');
      })
      .catch((e) => {
        if (!cancelled) setLoadError(String((e as Error).message ?? e));
        console.error('[settings/stt] failed to load config', e);
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const pickSttPreset = (id: string) => {
    setSttPreset(id);
    const p = STT_OPENAI_PRESETS.find((x) => x.id === id);
    if (p) { setSttUrl(p.url); setSttModel(p.model); }
  };

  const onSave = async () => {
    setSave({ kind: 'saving' });
    try {
      const s: STTPayload = { backend };
      if (backend === 'local') {
        s.whisper_model = whisperModel.trim();
      } else if (backend === 'openai') {
        s.url = sttUrl.trim();
        s.model = sttModel.trim();
        if (sttApiKey.trim()) s.api_key = sttApiKey.trim();
      }
      await api.putConfig({ stt: s });
      setSave({ kind: 'saved' });
      if (sttApiKey.trim()) {
        setKeyIsSet(true);
        setSttApiKey('');
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
      <Panel title="STT">
        <div className="text-xs text-zinc-500">Loading…</div>
      </Panel>
    );
  }

  return (
    <Panel title="STT">
      <div className="space-y-3">
        <p className="text-xs text-zinc-500 -mt-1">
          Speech to text. The backend below decides whether the loop is
          segmented (Whisper / OpenAI) or streaming (future providers).
        </p>

        <div>
          <label className="text-[11px] uppercase tracking-wider text-zinc-500 mb-1 block">
            Backend
          </label>
          <Select value={backend} onValueChange={(v) => setBackend(v as STTBackend)}>
            <SelectTrigger className="h-9 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="local">Local Whisper (MPS)</SelectItem>
              <SelectItem value="openai">OpenAI-compatible</SelectItem>
              <SelectItem value="sensevoice">SenseVoice</SelectItem>
            </SelectContent>
          </Select>
          <div className="text-[10px] text-zinc-600 mt-1">
            {BACKEND_BLURB[backend]}
          </div>
        </div>

        {backend === 'local' && (
          <div>
            <label className="text-[11px] uppercase tracking-wider text-zinc-500 mb-1 block">
              Whisper model (HF id)
            </label>
            <input
              value={whisperModel}
              onChange={(e) => setWhisperModel(e.target.value)}
              placeholder="openai/whisper-large-v3-turbo"
              className="w-full h-9 rounded-md border border-zinc-800 bg-zinc-900/60 px-2.5 text-xs text-zinc-200 placeholder-zinc-600 font-mono"
              spellCheck={false}
            />
            <div className="text-[10px] text-amber-500/70 mt-1">
              Model loads at server boot — restart the sidecar after
              changing. Requires <code>pip install -e ".[whisper]"</code>.
            </div>
          </div>
        )}

        {backend === 'openai' && (
          <>
            <div className="grid grid-cols-2 gap-2">
              {STT_OPENAI_PRESETS.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  onClick={() => pickSttPreset(p.id)}
                  className={
                    'p-2 text-left rounded-md border transition-colors ' +
                    (sttPreset === p.id
                      ? 'border-amber-500/60 bg-amber-500/5'
                      : 'border-zinc-800 bg-zinc-900/40 hover:bg-zinc-900/70')
                  }
                >
                  <div className="text-xs text-zinc-200">{p.label}</div>
                  <div className="text-[10px] text-zinc-500 mt-0.5 line-clamp-2">{p.blurb}</div>
                </button>
              ))}
            </div>
            <div>
              <label className="text-[11px] uppercase tracking-wider text-zinc-500 mb-1 block">
                URL
              </label>
              <input
                value={sttUrl}
                onChange={(e) => { setSttUrl(e.target.value); setSttPreset(matchSttPreset(e.target.value)); }}
                placeholder="https://api.openai.com/v1"
                className="w-full h-9 rounded-md border border-zinc-800 bg-zinc-900/60 px-2.5 text-xs text-zinc-200 placeholder-zinc-600 font-mono"
                spellCheck={false}
              />
            </div>
            <div>
              <label className="text-[11px] uppercase tracking-wider text-zinc-500 mb-1 block">
                Model
              </label>
              <input
                value={sttModel}
                onChange={(e) => setSttModel(e.target.value)}
                placeholder="whisper-1"
                className="w-full h-9 rounded-md border border-zinc-800 bg-zinc-900/60 px-2.5 text-xs text-zinc-200 placeholder-zinc-600 font-mono"
                spellCheck={false}
              />
            </div>
            <div>
              <label className="text-[11px] uppercase tracking-wider text-zinc-500 mb-1 block">
                API key
              </label>
              <input
                type="password"
                value={sttApiKey}
                onChange={(e) => setSttApiKey(e.target.value)}
                placeholder={
                  keyIsSet
                    ? 'key is already set — leave blank to keep'
                    : 'sk-...'
                }
                className="w-full h-9 rounded-md border border-zinc-800 bg-zinc-900/60 px-2.5 text-xs text-zinc-200 placeholder-zinc-600 font-mono"
                autoComplete="off"
                spellCheck={false}
              />
              <div className="text-[10px] text-zinc-600 mt-1">
                {keyIsSet
                  ? 'A key is saved in config/orbis.yaml. Leave blank to keep it.'
                  : 'Stored in config/orbis.yaml on your machine.'}
              </div>
            </div>
          </>
        )}

        {backend === 'sensevoice' && (
          <div className="text-[10px] text-zinc-500">
            SenseVoice runs in-process via funasr. Install with{' '}
            <code className="text-zinc-400">pip install -e ".[sensevoice]"</code>{' '}
            and restart the sidecar.
          </div>
        )}

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
