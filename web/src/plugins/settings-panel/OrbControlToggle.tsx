import { useEffect, useState } from 'react';
import { Switch } from '@/components/ui/switch';
import { api } from '@/lib/api';

/**
 * "Let the agent restyle the orb" — round-trips `agent.allow_orb_control`
 * via /api/config. When off, the voice agent's `set_orb_visual` tool refuses
 * (the gate is re-checked per call, so this takes effect without a restart).
 * Default-on.
 */
export function OrbControlToggle() {
  const [enabled, setEnabled] = useState(true);

  useEffect(() => {
    api.config()
      .then((r) => setEnabled(r.config.agent?.allow_orb_control ?? true))
      .catch(() => {
        /* config fetch failed — leave the optimistic default */
      });
  }, []);

  const onChange = async (next: boolean) => {
    setEnabled(next); // optimistic
    try {
      await api.putConfig({ agent: { allow_orb_control: next } });
    } catch {
      setEnabled(!next); // revert on failure
    }
  };

  return (
    <label className="flex items-start justify-between gap-4">
      <span>
        <span className="block text-sm text-fg-body">Let the agent restyle the orb</span>
        <span className="block text-helper text-fg-subtle">
          Allow voice commands like “use the nebula orb” or “more glow” to change its look.
        </span>
      </span>
      <Switch
        checked={enabled}
        onCheckedChange={onChange}
        aria-label="Let the agent restyle the orb"
        className="mt-0.5 shrink-0"
      />
    </label>
  );
}
