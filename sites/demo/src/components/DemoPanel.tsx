/**
 * The demo's single minimal panel — system info + links.
 *
 * No audio-device pickers: the browser/OS already routes to the default mic
 * and speaker, and in-page routing (getUserMedia deviceId / setSinkId) is
 * flaky across browsers, so we defer to the system default. This panel just
 * surfaces what's running and where to get the full app.
 */
import { useEffect, useState } from 'react';
import { Settings2, X } from 'lucide-react';
import { getWebGPUInfo } from '../engine/webgpu';
import { voiceEngine } from '../engine/voiceEngine';
import { gemmaEngine } from '../engine/gemmaEngine';

const REPO_URL = 'https://github.com/protoLabsAI/ORBIS';
const ORG_URL = 'https://protolabs.studio';

export function DemoPanel() {
  const [open, setOpen] = useState(false);
  const [gpu, setGpu] = useState<{ supported: boolean; adapter?: string }>({ supported: false });

  useEffect(() => {
    void getWebGPUInfo().then(setGpu);
  }, []);

  const brainReady = gemmaEngine.isLoaded;
  const voiceReady = voiceEngine.voiceReady;

  return (
    <>
      <button
        onClick={() => setOpen((o) => !o)}
        aria-label="System info"
        className="fixed right-4 top-4 z-50 rounded-full border border-white/10 bg-black/50 p-2 text-zinc-300 backdrop-blur transition-colors hover:text-white"
      >
        <Settings2 className="h-4 w-4" />
      </button>

      {open && (
        <div className="fixed right-4 top-16 z-50 w-[min(20rem,92vw)] rounded-2xl border border-white/10 bg-zinc-950/90 p-4 text-sm text-zinc-200 shadow-2xl backdrop-blur-xl">
          <div className="mb-3 flex items-center justify-between">
            <span className="font-medium">System</span>
            <button onClick={() => setOpen(false)} className="text-zinc-500 hover:text-white">
              <X className="h-4 w-4" />
            </button>
          </div>

          <div className="space-y-1 text-xs text-zinc-400">
            <Row label="WebGPU" value={gpu.supported ? 'on' : 'off'} good={gpu.supported} />
            {gpu.adapter && (
              <div className="flex justify-between gap-2">
                <span>GPU</span>
                <span className="truncate text-right text-zinc-500">{gpu.adapter}</span>
              </div>
            )}
            <Row label="Brain · Gemma" value={brainReady ? 'loaded' : 'on demand'} good={brainReady} />
            <Row label="Voice · Moonshine + Kokoro" value={voiceReady ? 'loaded' : 'on demand'} good={voiceReady} />
            <p className="pt-1 text-zinc-600">
              Everything runs on your device — nothing leaves it. Audio uses your system's default
              mic &amp; speaker. Speed depends on your GPU; this in-browser preview isn't
              representative of the native app.
            </p>
          </div>

          {/* CTA — the full, more powerful version */}
          <div className="mt-4 border-t border-white/5 pt-3">
            <p className="mb-2 text-xs text-zinc-500">Want the full, more powerful version?</p>
            <div className="flex flex-col gap-2">
              <a
                href="/download"
                className="rounded-lg bg-indigo-500/90 px-3 py-2 text-center text-sm font-medium text-white transition-colors hover:bg-indigo-500"
              >
                Download for Mac
              </a>
              <a
                href={REPO_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="rounded-lg border border-white/10 px-3 py-2 text-center text-sm text-zinc-300 transition-colors hover:border-white/20"
              >
                Build from source →
              </a>
            </div>
          </div>

          {/* About */}
          <div className="mt-4 border-t border-white/5 pt-3 text-center">
            <a
              href={ORG_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-zinc-500 transition-colors hover:text-zinc-300"
            >
              Built by protoLabs.studio ↗
            </a>
          </div>
        </div>
      )}
    </>
  );
}

function Row({ label, value, good }: { label: string; value: string; good: boolean }) {
  return (
    <div className="flex justify-between">
      <span>{label}</span>
      <span className={good ? 'text-emerald-400' : 'text-zinc-600'}>{value}</span>
    </div>
  );
}
