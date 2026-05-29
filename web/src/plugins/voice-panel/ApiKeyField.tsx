import { useState } from 'react';
import { Field } from '@/components/ui/field';
import { Button } from '@/components/ui/button';
import { apiKeyStore } from '@/auth/apiKey';
import { useApiKey } from '@/auth/useApiKey';
import { api, UnauthorizedError } from '@/lib/api';

/**
 * Paste-in field for the owner API key. Stored in localStorage +
 * attached to every /api/* fetch. Empty
 * = single-user fallback mode (no auth required server-side).
 *
 * After save, we ping /api/whoami to give immediate feedback — the
 * key either resolves or the server returns 401.
 */
export function ApiKeyField() {
  const stored = useApiKey();
  const [draft, setDraft] = useState<string>(stored ?? '');
  const [status, setStatus] = useState<'idle' | 'checking' | 'valid' | 'invalid'>('idle');
  const [error, setError] = useState<string | null>(null);

  const onSave = async () => {
    setError(null);
    setStatus('checking');
    apiKeyStore.set(draft);   // persist before the check so authHeaders picks it up
    try {
      await api.whoami();
      setStatus('valid');
    } catch (e) {
      if (e instanceof UnauthorizedError) {
        setStatus('invalid');
        setError('Key rejected — check the value in config/users.yaml on the server.');
      } else {
        setStatus('idle');
        setError(String((e as Error).message ?? e));
      }
    }
  };

  const onClear = () => {
    setDraft('');
    apiKeyStore.clear();
    setStatus('idle');
    setError(null);
  };

  const hint =
    status === 'checking' ? 'Checking...' :
    status === 'valid' ? 'Authenticated.' :
    status === 'invalid' ? 'Not authenticated.' :
    stored ? 'Saved. Reload to re-authenticate the voice session.' :
    'Leave empty on single-user installs. Required when running on tailnet.';

  return (
    <Field label="Owner API key" hint={hint} error={error}>
      <div className="flex items-stretch gap-2">
        <input
          type="password"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="pv_ak_..."
          className="flex-1 h-9 rounded-md border border-zinc-800 bg-zinc-900/60 px-3 text-sm text-zinc-200 placeholder-zinc-600"
          autoComplete="off"
          spellCheck={false}
        />
        <Button onClick={onSave} variant="secondary" size="sm" disabled={status === 'checking'}>
          Save
        </Button>
        {stored && (
          <Button onClick={onClear} variant="ghost" size="sm">
            Clear
          </Button>
        )}
      </div>
    </Field>
  );
}
