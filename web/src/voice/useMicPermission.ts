import { useEffect, useState } from 'react';

/**
 * Mic permission preflight — wraps ``navigator.permissions.query``
 * so callers can check whether the next ``getUserMedia()`` call will
 * fire the browser prompt, succeed silently, or be silently denied.
 *
 * Three real states + ``unsupported`` for browsers (Safari < 16,
 * older Firefox builds) where the Permissions API doesn't cover
 * microphone:
 *
 *   - ``granted`` — connect directly, no UX
 *   - ``prompt`` — first contact; show rationale before getUserMedia
 *     so the user understands what they're agreeing to before the OS
 *     dialog locks them into a deny
 *   - ``denied`` — browser will silently fail; route to the recovery
 *     copy in ConnectionBanner instead of triggering a silent stall
 *   - ``unsupported`` — assume ``prompt`` semantics (we don't know
 *     the state, so showing rationale before connect is the safer
 *     default)
 *
 * Listens for ``onchange`` so a user toggling the permission in
 * browser settings while the SPA is open updates the state without
 * a reload.
 */
export type MicPermissionState =
  | 'granted'
  | 'prompt'
  | 'denied'
  | 'unsupported';

export function useMicPermission(): MicPermissionState {
  const [state, setState] = useState<MicPermissionState>('unsupported');

  useEffect(() => {
    let cancelled = false;
    let status: PermissionStatus | null = null;

    const sync = () => {
      if (cancelled || !status) return;
      setState(status.state as MicPermissionState);
    };

    const probe = async () => {
      // The standard pattern. Some browsers don't expose 'microphone'
      // as a recognized permission name; the query rejects with
      // TypeError in that case and we fall through to 'unsupported'.
      try {
        status = await navigator.permissions.query({
          // 'microphone' isn't in the lib.dom typings for
          // PermissionName but is recognized by Chromium + Firefox.
          name: 'microphone' as PermissionName,
        });
        if (cancelled) return;
        setState(status.state as MicPermissionState);
        status.onchange = sync;
      } catch {
        if (!cancelled) setState('unsupported');
      }
    };

    void probe();
    return () => {
      cancelled = true;
      if (status) status.onchange = null;
    };
  }, []);

  return state;
}
