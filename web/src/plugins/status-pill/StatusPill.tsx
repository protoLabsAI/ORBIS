import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { RTVIEvent } from '@pipecat-ai/client-js';
import { useRTVIClientEvent, usePipecatClientTransportState } from '@pipecat-ai/client-react';
import { useVoiceStateSelector } from '@/voice/hooks';
import { statusPillStore } from './store';

const IDLE_HINT = 'double-click the orb to start';
const CONNECTING_HINT = 'connecting…';
const CONNECTED_HINT = 'connected — speak';
const FADE_MS = 3000;

export function StatusPill() {
  const transport = usePipecatClientTransportState();
  const [transient, setTransient] = useState<string | null>(null);
  const timerRef = useRef<number | null>(null);
  const externalTransient = useSyncExternalStore(
    statusPillStore.subscribe,
    statusPillStore.getSnapshot,
  );
  // Mid-flight delegation surfaces a clear "Asking ava…" hint above
  // every other pill state — it represents work happening *now* that
  // the user should know about. Picked up via voiceStore.activeToolCall,
  // which the bridge populates from RTVIEvent.LLMFunctionCallStarted.
  const activeToolCall = useVoiceStateSelector((s) => s.activeToolCall);
  const delegationProgress = useVoiceStateSelector((s) => s.delegationProgress);
  const delegationOutcome = useVoiceStateSelector((s) => s.delegationOutcome);
  const delegationText = activeToolCall
    ? formatActiveToolCall(activeToolCall, delegationProgress)
    : null;

  // When a delegation resolves with an error outcome, surface a brief
  // transient so the user sees the failure visually — the verbal
  // channel ("Couldn't reach ava") plays once but is easy to miss if
  // the user wasn't listening. Only fires on the *transition* into
  // error so we don't re-toast on every render.
  const lastOutcomeRef = useRef<typeof delegationOutcome>(null);
  useEffect(() => {
    if (delegationOutcome === 'error' && lastOutcomeRef.current !== 'error') {
      showTransient('delegation failed', 4000);
    }
    lastOutcomeRef.current = delegationOutcome;
    // showTransient identity is stable enough — it reads from refs +
    // setState which never trigger this effect's re-run.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [delegationOutcome]);

  const showTransient = (text: string, ms = FADE_MS) => {
    if (timerRef.current != null) window.clearTimeout(timerRef.current);
    setTransient(text);
    timerRef.current = window.setTimeout(() => setTransient(null), ms);
  };

  useRTVIClientEvent(RTVIEvent.BotReady, () => showTransient(CONNECTED_HINT));
  useRTVIClientEvent(RTVIEvent.Error, (m: unknown) => {
    const data = m as { data?: { error?: string } } | undefined;
    showTransient(`error: ${data?.data?.error ?? 'unknown'}`, 4000);
  });

  // Auto-expire the externally-pushed transient once its TTL hits.
  useEffect(() => {
    if (!externalTransient || externalTransient.expiresAt === 0) return;
    const remaining = externalTransient.expiresAt - Date.now();
    if (remaining <= 0) {
      statusPillStore.clear();
      return;
    }
    const id = window.setTimeout(() => statusPillStore.clear(), remaining);
    return () => window.clearTimeout(id);
  }, [externalTransient]);

  useEffect(() => {
    return () => {
      if (timerRef.current != null) window.clearTimeout(timerRef.current);
    };
  }, []);

  // External (connect-error pushed via store) wins over RTVI-driven
  // transients so an error surfacing during connect doesn't get
  // immediately overwritten by a stale BotReady toast. Delegation
  // sits *between* external and internal transients: an error banner
  // pushed via the store still wins (so a connection drop mid-
  // delegation surfaces immediately), but a stale BotReady toast
  // shouldn't paper over an in-flight tool call.
  const overlay = externalTransient?.text ?? delegationText ?? transient;

  // R11: "connecting…" while the transport is mid-handshake. Pulled
  // from transport state directly so it's a derived state, not a
  // transient — it stays on screen as long as the handshake is in
  // flight, and disappears the moment BotReady fires (which itself
  // pushes the 3s "connected — speak" toast).
  const connecting =
    transport === 'connecting' ||
    transport === 'authenticating' ||
    transport === 'connected';

  const disconnected = transport === 'disconnected' || transport === 'initialized' || transport === 'error';

  let text: string | null;
  if (overlay) {
    text = overlay;
  } else if (connecting) {
    text = CONNECTING_HINT;
  } else if (disconnected) {
    text = IDLE_HINT;
  } else {
    text = null;
  }

  if (!text) return null;

  // Delegation pill gets a subtle pulse so passive hints and active
  // work read differently at a glance. Anything overlay-wins (errors)
  // also gets the pulse only when it IS the delegation hint.
  const isDelegationHint = text === delegationText;

  return (
    <div
      className={
        'pointer-events-none fixed left-1/2 -translate-x-1/2 z-10 text-zinc-400 text-xs font-mono tracking-wide text-center px-4 ' +
        (isDelegationHint ? 'animate-pulse' : '')
      }
      style={{ bottom: 'calc(2rem + env(safe-area-inset-bottom, 0px))' }}
    >
      {text}
    </div>
  );
}

/**
 * Render a compact, human-readable hint for the in-flight tool call.
 *
 * For ``delegate_to`` we extract ``args.target`` (the chosen sub-agent
 * name) and prefer it over the raw tool name so the user reads
 * "Asking ava…" rather than "Running delegate_to…". When B2 lands a
 * progress narration channel we show the latest progress line beneath
 * the hint.
 *
 * Other tools (e.g. ``adjust_personality``) fall through to a
 * lower-key generic hint — they're fast, but a momentary "Adjusting
 * personality…" is still useful feedback if the LLM is mid-call.
 */
function formatActiveToolCall(
  call: { name: string; args: unknown },
  progress: string | null,
): string {
  if (call.name === 'delegate_to') {
    const target = readDelegateTarget(call.args);
    const base = target ? `asking ${target}…` : 'delegating…';
    return progress ? `${base} ${progress}` : base;
  }
  if (call.name === 'adjust_personality') {
    return 'adjusting personality…';
  }
  return `${call.name}…`;
}

function readDelegateTarget(args: unknown): string | null {
  if (typeof args !== 'object' || args === null) return null;
  const target = (args as { target?: unknown }).target;
  return typeof target === 'string' && target.length > 0 ? target : null;
}
