import { useSyncExternalStore } from 'react';
import { apiKeyStore } from './apiKey';

/**
 * Subscribe to the owner API key. Returns null when none is set
 * (single-user fallback mode — server accepts requests anonymously).
 */
export function useApiKey(): string | null {
  return useSyncExternalStore(
    apiKeyStore.subscribe,
    apiKeyStore.get,
    () => null,
  );
}
