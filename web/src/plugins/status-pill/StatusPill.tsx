import { useEffect, useRef, useState } from 'react';
import { RTVIEvent } from '@pipecat-ai/client-js';
import { useRTVIClientEvent, usePipecatClientTransportState } from '@pipecat-ai/client-react';

const IDLE_HINT = 'double-click the orb to start';
const CONNECTED_HINT = 'connected — speak';
const FADE_MS = 3000;

export function StatusPill() {
  const transport = usePipecatClientTransportState();
  const [transient, setTransient] = useState<string | null>(null);
  const timerRef = useRef<number | null>(null);

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

  useEffect(() => {
    return () => {
      if (timerRef.current != null) window.clearTimeout(timerRef.current);
    };
  }, []);

  const disconnected = transport === 'disconnected' || transport === 'initialized' || transport === 'error';
  const text = transient ?? (disconnected ? IDLE_HINT : null);

  if (!text) return null;

  return (
    <div
      className="pointer-events-none fixed left-1/2 -translate-x-1/2 z-10 text-zinc-400 text-xs font-mono tracking-wide text-center px-4"
      style={{ bottom: 'calc(2rem + env(safe-area-inset-bottom, 0px))' }}
    >
      {text}
    </div>
  );
}
