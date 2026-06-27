/**
 * Audio device selection for the demo (browser MediaDevices).
 *
 * Holds the chosen input (mic) + output (speaker) deviceIds; audio.ts reads
 * them for capture/playback. Device labels are only populated after mic
 * permission is granted, so the panel re-enumerates once we have a stream.
 */
export interface DeviceChoice {
  inputId: string | null;
  outputId: string | null;
}

const choice: DeviceChoice = { inputId: null, outputId: null };
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

export const deviceStore = {
  get: (): DeviceChoice => choice,
  setInput(id: string | null) {
    choice.inputId = id;
    emit();
  },
  setOutput(id: string | null) {
    choice.outputId = id;
    emit();
  },
  subscribe(l: () => void): () => void {
    listeners.add(l);
    return () => listeners.delete(l);
  },
  getSnapshot: (): DeviceChoice => choice,
};

export async function listAudioDevices(): Promise<{
  inputs: MediaDeviceInfo[];
  outputs: MediaDeviceInfo[];
}> {
  try {
    const all = await navigator.mediaDevices.enumerateDevices();
    return {
      inputs: all.filter((d) => d.kind === 'audioinput'),
      outputs: all.filter((d) => d.kind === 'audiooutput'),
    };
  } catch {
    return { inputs: [], outputs: [] };
  }
}

/** Whether output-device routing is supported (AudioContext.setSinkId). */
export function canRouteOutput(): boolean {
  return typeof AudioContext !== 'undefined' && 'setSinkId' in AudioContext.prototype;
}
