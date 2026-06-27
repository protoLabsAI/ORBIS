// WebGPU feature detection. The on-device LLM/STT/TTS all require it; when
// absent the demo falls back to a "download the app" message.
export function hasWebGPU(): boolean {
  return (
    typeof navigator !== 'undefined' &&
    'gpu' in navigator &&
    !!(navigator as unknown as { gpu?: unknown }).gpu
  );
}

interface GPUAdapterInfoLike {
  vendor?: string;
  architecture?: string;
  description?: string;
}

/** WebGPU support + a human adapter string for the system-info panel. */
export async function getWebGPUInfo(): Promise<{ supported: boolean; adapter?: string }> {
  if (!hasWebGPU()) return { supported: false };
  try {
    const gpu = (navigator as unknown as { gpu: { requestAdapter(): Promise<unknown> } }).gpu;
    const adapter = (await gpu.requestAdapter()) as { info?: GPUAdapterInfoLike } | null;
    const info = adapter?.info;
    const label = info
      ? [info.vendor, info.architecture, info.description].filter(Boolean).join(' ').trim()
      : '';
    return { supported: true, adapter: label || 'available' };
  } catch {
    return { supported: true };
  }
}
