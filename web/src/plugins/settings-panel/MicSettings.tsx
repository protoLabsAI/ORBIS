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
import {
  getPreferredAudioDeviceId,
  setPreferredAudioDeviceId,
} from '@/shared/audio/preferredDevice';
import { NativeLevelMeter } from '@/shared/audio/NativeLevelMeter';

/**
 * Microphone selector + live level meter.
 *
 * Pre-2026-04-28 this had two paths — a getUserMedia / WebRTC mic
 * picker and a native CPAL one. The web/PWA path was dropped
 * (DECISIONS.md amendment of that date), so only the native path
 * remains.
 */
export function MicSettings() {
  const [devices, setDevices] = useState<string[]>([]);
  const [device, setDevice] = useState<string>('');

  useEffect(() => {
    invoke<string[]>('list_audio_inputs')
      .then((devs) => {
        setDevices(devs);
        const saved = getPreferredAudioDeviceId();
        setDevice(devs.includes(saved) ? saved : devs[0] ?? '');
      })
      .catch(() => {});
  }, []);

  const onChange = (name: string) => {
    setDevice(name);
    setPreferredAudioDeviceId(name);
  };

  return (
    <Panel title="Microphone">
      <div className="space-y-3">
        {devices.length > 0 && (
          <div className="space-y-1">
            <p className="text-xs uppercase tracking-wider text-zinc-500">
              Input device
            </p>
            <Select value={device} onValueChange={onChange}>
              <SelectTrigger className="w-full bg-zinc-900 border-zinc-800">
                <SelectValue placeholder="System default" />
              </SelectTrigger>
              <SelectContent className="bg-zinc-900 border-zinc-800">
                {devices.map((name) => (
                  <SelectItem key={name} value={name}>
                    {name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}

        <NativeLevelMeter deviceName={device} />

        <p className="text-[11px] text-zinc-600 leading-relaxed">
          Device selection is passed to the native audio engine. Changes take effect on the next voice session.
        </p>
      </div>
    </Panel>
  );
}
