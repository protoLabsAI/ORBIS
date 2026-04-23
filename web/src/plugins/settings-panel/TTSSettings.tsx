import { useEffect, useState } from 'react';
import { Panel } from '@/components/ui/panel';
import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { api } from '@/lib/api';

type TTSBackend = 'kokoro' | 'openai' | 'elevenlabs' | 'fish';

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

/**
 * TTS backend + voice selection. Writes persona.voice in config/orbis.yaml
 * via POST /api/config. Actual TTS-provider credentials (OpenAI key,
 * ElevenLabs key, Fish URL) stay env-only — surface them in docs not UI.
 */
export function TTSSettings() {
  const [loading, setLoading] = useState(true);
  const [backend, setBackend] = useState<TTSBackend>('kokoro');
  const [voice, setVoice] = useState('');
  const [save, setSave] = useState<SaveState>({ kind: 'idle' });

  useEffect(() => {
    let cancelled = false;
    api.config()
      .then((r) => {
        if (cancelled) return;
        const v = r.config?.voice ?? {};
        if (v.tts_backend) setBackend(v.tts_backend as TTSBackend);
        if (v.voice) setVoice(v.voice);
      })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const onSave = async () => {
    setSave({ kind: 'saving' });
    try {
      const v: Record<string, string> = { tts_backend: backend };
      if (voice.trim()) v.voice = voice.trim();
      await api.putConfig({ voice: v as never });
      setSave({ kind: 'saved' });
      window.setTimeout(() => setSave({ kind: 'idle' }), 2000);
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
          <input
            value={voice}
            onChange={(e) => setVoice(e.target.value)}
            placeholder={VOICE_PLACEHOLDER[backend]}
            className="w-full h-9 rounded-md border border-zinc-800 bg-zinc-900/60 px-2.5 text-xs text-zinc-200 placeholder-zinc-600 font-mono"
            spellCheck={false}
          />
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
      </div>
    </Panel>
  );
}
