import { useEffect, useState } from 'react';
import { Panel } from '@/components/ui/panel';
import { MicTest } from '@/shared/audio/MicTest';

/**
 * Mic tab for the settings drawer — re-runs the same `getUserMedia`
 * gate the wizard does so the user can verify input levels any time.
 * Device enumeration is gated on already-granted permission (browsers
 * only expose labeled `audioinput` devices after a successful grant),
 * so we hide the device picker until the user has tested once.
 */
export function MicSettings() {
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [deviceId, setDeviceId] = useState<string>('');

  const refreshDevices = async () => {
    try {
      const all = await navigator.mediaDevices.enumerateDevices();
      setDevices(all.filter((d) => d.kind === 'audioinput'));
    } catch {
      // Permission not yet granted — the MicTest inside handles that
      // path; we just won't have a useful device list until after.
    }
  };

  useEffect(() => {
    void refreshDevices();
  }, []);

  return (
    <Panel title="Microphone">
      <div className="space-y-3">
        {devices.length > 0 && (
          <label className="block">
            <div className="text-xs uppercase tracking-wider text-zinc-500 mb-1">
              Input device
            </div>
            <select
              value={deviceId}
              onChange={(e) => setDeviceId(e.target.value)}
              className="w-full bg-zinc-900 border border-zinc-800 rounded px-2 py-1.5 text-sm"
            >
              <option value="">System default</option>
              {devices.map((d) => (
                <option key={d.deviceId} value={d.deviceId}>
                  {d.label || `Device ${d.deviceId.slice(0, 6)}`}
                </option>
              ))}
            </select>
          </label>
        )}
        <MicTest
          key={deviceId /* rebuild stream when device changes */}
          deviceId={deviceId || undefined}
          onVerified={refreshDevices}
        />
        <p className="text-[11px] text-zinc-600 leading-relaxed">
          Device selection is UI-only in this version; ORBIS still routes
          through the system-default input during a voice session.
        </p>
      </div>
    </Panel>
  );
}
