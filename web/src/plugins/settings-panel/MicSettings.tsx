import { useEffect, useState } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { Panel } from '@/components/ui/panel';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { MicTest } from '@/shared/audio/MicTest';
import {
  getPreferredAudioDeviceId,
  setPreferredAudioDeviceId,
  subscribePreferredAudioDeviceId,
} from '@/shared/audio/preferredDevice';
import { useVoiceStateSelector } from '@/voice/hooks';
import { NativeLevelMeter } from '@/shared/audio/NativeLevelMeter';

export function MicSettings() {
  const audioTransport = useVoiceStateSelector((s) => s.audioTransport);
  const isNative = audioTransport === 'native';

  // --- native state ---
  const [nativeDevices, setNativeDevices] = useState<string[]>([]);
  const [nativeDevice, setNativeDevice] = useState<string>('');

  // --- webrtc state ---
  const [browserDevices, setBrowserDevices] = useState<MediaDeviceInfo[]>([]);
  const [browserDeviceId, setBrowserDeviceId] = useState<string>(() =>
    getPreferredAudioDeviceId(),
  );

  // --- shared init ---
  useEffect(() => {
    if (isNative) {
      invoke<string[]>('list_audio_inputs')
        .then((devs) => {
          setNativeDevices(devs);
          const saved = getPreferredAudioDeviceId();
          setNativeDevice(devs.includes(saved) ? saved : devs[0] ?? '');
        })
        .catch(() => {});
    } else {
      void refreshBrowserDevices();
    }
    return subscribePreferredAudioDeviceId(setBrowserDeviceId);
  }, [isNative]);

  const refreshBrowserDevices = async () => {
    try {
      const all = await navigator.mediaDevices.enumerateDevices();
      setBrowserDevices(all.filter((d) => d.kind === 'audioinput'));
    } catch {
      // permission not yet granted
    }
  };

  const onChangeNative = (name: string) => {
    setNativeDevice(name);
    setPreferredAudioDeviceId(name);
  };

  const onChangeBrowser = (id: string) => {
    setBrowserDeviceId(id);
    setPreferredAudioDeviceId(id);
  };

  return (
    <Panel title="Microphone">
      <div className="space-y-3">
        {/* Device picker */}
        {isNative ? (
          nativeDevices.length > 0 && (
            <div className="space-y-1">
              <p className="text-xs uppercase tracking-wider text-zinc-500">
                Input device
              </p>
              <Select value={nativeDevice} onValueChange={onChangeNative}>
                <SelectTrigger className="w-full bg-zinc-900 border-zinc-800">
                  <SelectValue placeholder="System default" />
                </SelectTrigger>
                <SelectContent className="bg-zinc-900 border-zinc-800">
                  {nativeDevices.map((name) => (
                    <SelectItem key={name} value={name}>
                      {name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )
        ) : (
          browserDevices.length > 0 && (
            <div className="space-y-1">
              <p className="text-xs uppercase tracking-wider text-zinc-500">
                Input device
              </p>
              <Select value={browserDeviceId} onValueChange={onChangeBrowser}>
                <SelectTrigger className="w-full bg-zinc-900 border-zinc-800">
                  <SelectValue placeholder="System default" />
                </SelectTrigger>
                <SelectContent className="bg-zinc-900 border-zinc-800">
                  <SelectItem value="">System default</SelectItem>
                  {browserDevices.map((d) => (
                    <SelectItem key={d.deviceId} value={d.deviceId}>
                      {d.label || `Device ${d.deviceId.slice(0, 6)}`}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )
        )}

        {/* Level meter */}
        {isNative ? (
          <NativeLevelMeter deviceName={nativeDevice} />
        ) : (
          <MicTest
            key={browserDeviceId}
            deviceId={browserDeviceId || undefined}
            onPermissionGranted={refreshBrowserDevices}
            onVerified={refreshBrowserDevices}
          />
        )}

        <p className="text-[11px] text-zinc-600 leading-relaxed">
          {isNative
            ? 'Device selection is passed to the native audio engine. Changes take effect on the next voice session.'
            : 'The selection persists across reloads and is plumbed into the voice client. "System default" means whatever macOS picks at the time of the session.'}
        </p>
      </div>
    </Panel>
  );
}
