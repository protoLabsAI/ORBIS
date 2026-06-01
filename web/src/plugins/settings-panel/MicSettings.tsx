import { useEffect, useState } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { Button } from '@/components/ui/button';
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
import {
  getAudioInputMode,
  usesSelectableInputDevice,
  type AudioInputMode,
} from '@/shared/audio/nativeAudio';
import {
  canRequestMicrophone,
  getMicrophonePermissionStatus,
  isMicrophoneAuthorized,
  needsMicrophoneSettings,
  openMicrophoneSettings,
  requestMicrophonePermission,
  type MicrophonePermissionStatus,
} from '@/shared/audio/microphonePermission';
import { NativeLevelMeter } from '@/shared/audio/NativeLevelMeter';

/**
 * Microphone selector + live level meter.
 *
 * Pre-2026-04-28 this had both browser-capture and native CPAL mic
 * paths. The web/PWA path was dropped (DECISIONS.md amendment of that
 * date), so only native desktop audio remains.
 */
export function MicSettings() {
  const [devices, setDevices] = useState<string[]>([]);
  const [device, setDevice] = useState<string>('');
  const [permission, setPermission] =
    useState<MicrophonePermissionStatus>('not_determined');
  const [audioInputMode, setAudioInputMode] =
    useState<AudioInputMode>('unsupported');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadDevices = async () => {
    const devs = await invoke<string[]>('list_audio_inputs');
    setDevices(devs);
    const saved = getPreferredAudioDeviceId();
    setDevice(devs.includes(saved) ? saved : devs[0] ?? '');
  };

  const refreshPermission = async () => {
    setError(null);
    const [status, mode] = await Promise.all([
      getMicrophonePermissionStatus(),
      getAudioInputMode(),
    ]);
    setPermission(status);
    setAudioInputMode(mode);
    if (isMicrophoneAuthorized(status) && usesSelectableInputDevice(mode)) {
      await loadDevices();
    } else {
      setDevices([]);
      setDevice('');
    }
  };

  useEffect(() => {
    queueMicrotask(() => {
      refreshPermission().catch((e) => {
        setError(String((e as Error).message ?? e));
      });
    });
    // Initial Tauri permission probe runs once when the settings panel mounts.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onRequest = async () => {
    setBusy(true);
    setError(null);
    try {
      const status = await requestMicrophonePermission();
      setPermission(status);
      const mode = await getAudioInputMode();
      setAudioInputMode(mode);
      if (isMicrophoneAuthorized(status) && usesSelectableInputDevice(mode)) {
        await loadDevices();
      } else {
        setDevices([]);
        setDevice('');
      }
    } catch (e) {
      setError(String((e as Error).message ?? e));
    } finally {
      setBusy(false);
    }
  };

  const onOpenSettings = async () => {
    setBusy(true);
    setError(null);
    try {
      await openMicrophoneSettings();
    } catch (e) {
      setError(String((e as Error).message ?? e));
    } finally {
      setBusy(false);
    }
  };

  const onRefresh = async () => {
    setBusy(true);
    try {
      await refreshPermission();
    } catch (e) {
      setError(String((e as Error).message ?? e));
    } finally {
      setBusy(false);
    }
  };

  const authorized = isMicrophoneAuthorized(permission);
  const selectableInput = usesSelectableInputDevice(audioInputMode);

  const onChange = (name: string) => {
    setDevice(name);
    setPreferredAudioDeviceId(name);
  };

  return (
    <Panel title="Microphone">
      <div className="space-y-3">
        {!authorized && (
          <div className="rounded-lg border border-edge bg-base/60 p-3 space-y-3">
            <div>
              <div className="text-sm text-fg-body">Microphone access</div>
              <div className="text-xs text-fg-subtle mt-1">
                Current status: {permission.replace('_', ' ')}
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              {canRequestMicrophone(permission) && (
                <Button onClick={onRequest} disabled={busy}>
                  {busy ? 'Waiting…' : 'Allow microphone'}
                </Button>
              )}
              {needsMicrophoneSettings(permission) && (
                <>
                  <Button onClick={onOpenSettings} disabled={busy}>
                    Open Settings
                  </Button>
                  <Button variant="ghost" onClick={onRefresh} disabled={busy}>
                    Recheck
                  </Button>
                </>
              )}
            </div>
          </div>
        )}

        {authorized && !selectableInput && (
          <div className="rounded-lg border border-edge bg-base/60 p-3 text-sm text-fg-muted">
            Input source: macOS system input
          </div>
        )}

        {authorized && selectableInput && devices.length > 0 && (
          <div className="space-y-1">
            <p className="text-xs uppercase tracking-wider text-fg-subtle">
              Input device
            </p>
            <Select value={device} onValueChange={onChange}>
              <SelectTrigger className="w-full bg-raised border-edge">
                <SelectValue placeholder="System default" />
              </SelectTrigger>
              <SelectContent className="bg-raised border-edge">
                {devices.map((name) => (
                  <SelectItem key={name} value={name}>
                    {name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}

        {authorized && selectableInput && devices.length === 0 && (
          <div className="rounded-lg border border-edge bg-base/60 p-3 text-sm text-fg-muted">
            No microphone input device was found.
          </div>
        )}

        {authorized && <NativeLevelMeter deviceName={device || audioInputMode} />}

        {error && (
          <p className="text-xs text-danger">
            {error}
          </p>
        )}

        <p className="text-xs text-fg-faint leading-relaxed">
          {selectableInput
            ? 'Device selection is passed to the native audio engine. Changes take effect on the next voice session.'
            : 'Voice processing follows the current macOS system input.'}
        </p>
      </div>
    </Panel>
  );
}
