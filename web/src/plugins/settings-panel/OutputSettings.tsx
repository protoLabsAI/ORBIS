import { useEffect, useState } from 'react';
import { Panel } from '@/components/ui/panel';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  getPreferredAudioOutputDeviceId,
  setPreferredAudioOutputDeviceId,
} from '@/shared/audio/preferredDevice';
import { listAudioOutputs, setOutputDevice } from '@/shared/audio/nativeAudio';

// Radix Select can't use an empty-string value, so the "follow the system
// default" choice gets a sentinel that maps back to '' for the Rust side.
const SYSTEM_DEFAULT = '__system_default__';

/**
 * Output-device selector — where ORBIS plays its voice.
 *
 * Simpler than {@link MicSettings}: output needs no permission and is always
 * selectable. Choosing the built-in speakers here (while your *system* default
 * stays, say, a USB interface) is also what lets the voice-processing echo
 * canceller work — VPIO can only build its aggregate from built-in/aggregatable
 * output, so a USB system default otherwise forces the half-duplex fallback.
 */
export function OutputSettings() {
  const [devices, setDevices] = useState<string[]>([]);
  const [device, setDevice] = useState<string>(SYSTEM_DEFAULT);
  const [needsRelaunch, setNeedsRelaunch] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listAudioOutputs()
      .then((devs) => {
        setDevices(devs);
        const saved = getPreferredAudioOutputDeviceId();
        setDevice(saved && devs.includes(saved) ? saved : SYSTEM_DEFAULT);
      })
      .catch((e) => setError(String((e as Error).message ?? e)));
  }, []);

  const onChange = (value: string) => {
    setDevice(value);
    const name = value === SYSTEM_DEFAULT ? '' : value;
    setPreferredAudioOutputDeviceId(name);
    setOutputDevice(name)
      .then(() => setNeedsRelaunch(true))
      .catch((e) => setError(String((e as Error).message ?? e)));
  };

  return (
    <Panel title="Output">
      <div className="space-y-3">
        <div className="space-y-1">
          <p className="text-xs uppercase tracking-wider text-fg-subtle">
            Output device
          </p>
          <Select value={device} onValueChange={onChange}>
            <SelectTrigger className="w-full bg-raised border-edge">
              <SelectValue placeholder="System default" />
            </SelectTrigger>
            <SelectContent className="bg-raised border-edge">
              <SelectItem value={SYSTEM_DEFAULT}>System default</SelectItem>
              {devices.map((name) => (
                <SelectItem key={name} value={name}>
                  {name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {needsRelaunch && (
            <p className="text-helper text-fg-muted">
              Takes effect when ORBIS next launches.
            </p>
          )}
        </div>

        {error && <p className="text-xs text-danger">{error}</p>}

        <p className="text-xs text-fg-faint leading-relaxed">
          Where ORBIS plays its voice. Pick the built-in speakers to keep echo
          cancellation working even when your system output is a USB interface —
          without changing your system default.
        </p>
      </div>
    </Panel>
  );
}
