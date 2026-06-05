import { useCallback, useEffect, useState } from 'react';
import { api, type EntitlementState } from '@/lib/api';

export type Customization = EntitlementState['customization'];

export type UseEntitlement = {
  loading: boolean;
  customization: Customization | null;
  /** Closed gate + no valid license on file → orb editing is paywalled. */
  locked: boolean;
  refresh: () => Promise<void>;
};

/**
 * Read the customization entitlement (GET /api/entitlement). The backend 403
 * on the `orb` config block is the authoritative gate; this just lets the UI
 * present the right surface (editor vs unlock CTA) and reflect activation.
 */
export function useEntitlement(): UseEntitlement {
  const [customization, setCustomization] = useState<Customization | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const res = await api.entitlement();
      setCustomization(res.customization);
    } catch {
      // Fail open: a transient fetch error shouldn't lock a paying user out
      // of the editor — the server 403 still protects the actual write.
      setCustomization(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const locked = customization?.gate_mode === 'closed' && !customization.active;

  return { loading, customization, locked, refresh };
}
