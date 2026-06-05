import { useState } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Hint } from '@/components/ui/hint';
import { SectionLabel } from '@/components/ui/section-label';
import { api, type EntitlementState } from '@/lib/api';

type Customization = EntitlementState['customization'];

// Where "Get a license" sends the buyer — the marketing unlock page, which
// fronts the Stripe checkout. A stable, brandable URL so the app build never
// needs to know the Stripe link directly.
const PURCHASE_URL = 'https://orbis.protolabs.studio/unlock';

/**
 * Paywall surface for orb customization.
 *
 * - Not licensed → unlock CTA ("Get a license — $9") + paste-key activation.
 * - Licensed → a compact "unlocked / deactivate" footer (so a paying user can
 *   move the license to another machine).
 *
 * `onChange` is the parent's entitlement refresh — called after activate /
 * deactivate so the editor re-renders against the new gate state.
 */
export function UnlockCustomization({
  customization,
  onChange,
}: {
  customization: Customization | null;
  onChange: () => void | Promise<void>;
}) {
  const [key, setKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const licensed = customization?.licensed === true;

  const activate = async () => {
    const trimmed = key.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.activateLicense(trimmed);
      setKey('');
      await onChange();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'That key could not be activated.');
    } finally {
      setBusy(false);
    }
  };

  const deactivate = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.deactivateLicense();
      await onChange();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not deactivate.');
    } finally {
      setBusy(false);
    }
  };

  if (licensed) {
    return (
      <div className="rounded-xl border border-edge bg-raised/40 p-4 space-y-2">
        <div className="flex items-center gap-2">
          <span className="text-success" aria-hidden>✓</span>
          <span className="text-sm font-medium text-fg">Customization unlocked</span>
        </div>
        {customization?.sub && <Hint>Licensed to {customization.sub}</Hint>}
        <button
          type="button"
          onClick={() => void deactivate()}
          disabled={busy}
          className="text-helper text-fg-subtle hover:text-fg-body underline-offset-2 hover:underline transition-colors disabled:opacity-50"
        >
          Deactivate on this machine (to move the license elsewhere)
        </button>
        {error && <p className="text-helper text-danger">{error}</p>}
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-edge bg-raised/40 p-5 space-y-4">
      <div className="space-y-1.5">
        <div className="flex items-center gap-2">
          <span aria-hidden>🔒</span>
          <span className="text-sm font-medium text-fg">Unlock orb customization</span>
          <span className="ml-auto text-micro uppercase tracking-wider text-brand">
            $9 · one-time
          </span>
        </div>
        <Hint>
          Variants, palettes, shader params, and saved presets are a one-time
          unlock. Your starter orb stays free.
        </Hint>
      </div>

      <Button
        type="button"
        className="w-full"
        onClick={() => void invoke('open_url', { url: PURCHASE_URL }).catch(() => {})}
      >
        Get a license — $9
      </Button>

      <div className="space-y-2">
        <SectionLabel>Already have a key?</SectionLabel>
        <div className="flex gap-2">
          <Input
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder="ORBIS-…"
            spellCheck={false}
            autoCapitalize="off"
            autoCorrect="off"
            disabled={busy}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void activate();
            }}
          />
          <Button
            type="button"
            variant="outline"
            onClick={() => void activate()}
            disabled={busy || !key.trim()}
          >
            {busy ? 'Activating…' : 'Activate'}
          </Button>
        </div>
        <Hint>Paste the key from your purchase email.</Hint>
        {error && <p className="text-helper text-danger">{error}</p>}
      </div>
    </div>
  );
}
