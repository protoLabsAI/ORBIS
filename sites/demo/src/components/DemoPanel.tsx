/**
 * The demo's single, minimal settings panel — replaces the full app drawer.
 *
 * Just what a browser taste needs: pick audio in/out devices, see system
 * info (WebGPU + which on-device models are loaded), and a CTA to the full,
 * more powerful version (download or build from source).
 */
import { useEffect, useState, useSyncExternalStore } from 'react';
import { Settings2, X } from 'lucide-react';
import { deviceStore, listAudioDevices, canRouteOutput } from '../engine/devices';
import { getWebGPUInfo } from '../engine/webgpu';
import { voiceEngine } from '../engine/voiceEngine';
import { gemmaEngine } from '../engine/gemmaEngine';

const REPO_URL = 'https://github.com/protoLabsAI/ORBIS';

export function DemoPanel() {
  const [open, setOpen] = useState(false);
  const [inputs, setInputs] = useState<MediaDeviceInfo[]>([]);
  const [outputs, setOutputs] = useState<MediaDeviceInfo[]>([]);
  const [gpu, setGpu] = useState<{ supported: boolean; adapter?: string }>({ supported: false });
  const choice = useSyncExternalStore(deviceStore.subscribe, deviceStore.getSnapshot, deviceStore.getSnapshot);

  useEffect(() => {
    void getWebGPUInfo().then(setGpu);
  }, []);

  useEffect(() => {
    if (!open) return;
    const refresh = () =>
      listAudioDevices().then(({ inputs, outputs }) => {
        setInputs(inputs);
        setOutputs(outputs);
      });
    void refresh();
    navigator.mediaDevices?.addEventListener?.('devicechange', refresh);
    return () => navigator.mediaDevices?.removeEventListener?.('devicechange', refresh);
  }, [open]);

  const brainReady = gemmaEngine.isLoaded;
  const voiceReady = voiceEngine.voiceReady;
  const haveLabels = inputs.some((d) => d.label);

  return (
    <>
      <button
        onClick={() => setOpen((o) => !o)}
        aria-label="Settings"
        className="fixed right-4 top-4 z-50 rounded-full border border-white/10 bg-black/50 p-2 text-zinc-300 backdrop-blur transition-colors hover:text-white"
      >
        <Settings2 className="h-4 w-4" />
      </button>

      {open && (
        <div className="fixed right-4 top-16 z-50 w-[min(20rem,92vw)] rounded-2xl border border-white/10 bg-zinc-950/90 p-4 text-sm text-zinc-200 shadow-2xl backdrop-blur-xl">
          <div className="mb-3 flex items-center justify-between">
            <span className="font-medium">Settings</span>
            <button onClick={() => setOpen(false)} className="text-zinc-500 hover:text-white">
              <X className="h-4 w-4" />
            </button>
          </div>

          {/* Audio devices */}
          <div className="space-y-3">
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-zinc-500">Microphone</label>
              <select
                value={choice.inputId ?? ''}
                onChange={(e) => deviceStore.setInput(e.target.value || null)}
                className="w-full rounded-lg border border-white/10 bg-black/40 px-2 py-1.5 text-sm"
              >
                <option value="">System default</option>
                {inputs.map((d) => (
                  <option key={d.deviceId} value={d.deviceId}>
                    {d.label || 'Microphone'}
                  </option>
                ))}
              </select>
            </div>

            {canRouteOutput() && (
              <div>
                <label className="mb-1 block text-xs uppercase tracking-wide text-zinc-500">Output</label>
                <select
                  value={choice.outputId ?? ''}
                  onChange={(e) => deviceStore.setOutput(e.target.value || null)}
                  className="w-full rounded-lg border border-white/10 bg-black/40 px-2 py-1.5 text-sm"
                >
                  <option value="">System default</option>
                  {outputs.map((d) => (
                    <option key={d.deviceId} value={d.deviceId}>
                      {d.label || 'Speaker'}
                    </option>
                  ))}
                </select>
              </div>
            )}

            {!haveLabels && (
              <p className="text-xs text-zinc-600">Grant mic access (tap to talk) to see device names.</p>
            )}
          </div>

          {/* System info */}
          <div className="mt-4 space-y-1 border-t border-white/5 pt-3 text-xs text-zinc-400">
            <Row label="WebGPU" value={gpu.supported ? 'on' : 'off'} good={gpu.supported} />
            {gpu.adapter && (
              <div className="flex justify-between gap-2">
                <span>GPU</span>
                <span className="truncate text-right text-zinc-500">{gpu.adapter}</span>
              </div>
            )}
            <Row label="Brain · Gemma" value={brainReady ? 'loaded' : 'on demand'} good={brainReady} />
            <Row label="Voice · Moonshine + Kokoro" value={voiceReady ? 'loaded' : 'on demand'} good={voiceReady} />
            <p className="pt-1 text-zinc-600">Everything runs on your device — nothing leaves it.</p>
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
