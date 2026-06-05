import { useSyncExternalStore } from 'react';
import { Panel } from '@/components/ui/panel';
import { Button } from '@/components/ui/button';
import { variantRegistry } from '@/plugins/orb/variants/registry';
import { setVariant, applyPreset } from '@/plugins/orb/broadcast';
import { useActiveVariant } from '@/plugins/orb/useOrbState';

/**
 * Dev-only picker for the premium / beta-gated orb variants. They register
 * behind BETA_ORBS and aren't in the free starter pool, so this is how you
 * actually see + tune them in development. Self-hides when none are registered
 * (so it never shows in a shipped beta build).
 */
export function BetaOrbsPanel() {
  const all = useSyncExternalStore(variantRegistry.subscribe, variantRegistry.all);
  const active = useActiveVariant();
  const premium = all.filter((v) => v.premium);
  if (premium.length === 0) return null;

  const activeSpec = premium.find((v) => v.id === active?.id);

  return (
    <Panel title="Beta orbs">
      <div className="space-y-2.5">
        <div className="flex flex-wrap gap-1.5">
          {premium.map((v) => (
            <Button
              key={v.id}
              size="xs"
              variant={v.id === active?.id ? 'default' : 'secondary'}
              onClick={() => {
                setVariant(v.id);
                applyPreset(v.defaultPalette);
              }}
            >
              {v.name}
            </Button>
          ))}
        </div>
        {activeSpec && (
          <div className="flex flex-wrap gap-1">
            {Object.keys(activeSpec.palettes).map((p) => (
              <Button key={p} size="xs" variant="ghost" onClick={() => applyPreset(p)}>
                {p}
              </Button>
            ))}
          </div>
        )}
        <p className="text-helper text-fg-faint">
          Premium variants — gated behind the paywall + BETA_ORBS, never shown to free users.
        </p>
      </div>
    </Panel>
  );
}
