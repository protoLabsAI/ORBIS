// WebGPU feature detection. The on-device LLM/STT/TTS all require it; when
// absent the demo falls back to a "download the app" message.
export function hasWebGPU(): boolean {
  return (
    typeof navigator !== 'undefined' &&
    'gpu' in navigator &&
    !!(navigator as unknown as { gpu?: unknown }).gpu
  );
}
