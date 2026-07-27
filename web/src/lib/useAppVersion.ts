import { useEffect, useState } from 'react';
import { getVersion } from '@tauri-apps/api/app';

/**
 * Shipped app version, read from the Tauri bundle (`tauri.conf.json`, stamped
 * per-tag by the release automation). Deliberately NOT a frontend constant —
 * a hardcoded version silently drifts from what actually shipped.
 *
 * Returns null off the Tauri host (browser-only throwaway testing), so callers
 * render the attribution without a version rather than a bogus one.
 */
export function useAppVersion(): string | null {
  const [version, setVersion] = useState<string | null>(null);

  useEffect(() => {
    getVersion()
      .then(setVersion)
      .catch(() => setVersion(null));
  }, []);

  return version;
}
