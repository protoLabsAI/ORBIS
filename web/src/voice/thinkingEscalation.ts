import { useEffect, useState } from 'react';

/**
 * Client-side escalation of the "thinking" status so a slow LLM never leaves a
 * static `thinking…` on screen forever. Purely ambient text — no new chrome.
 */
export const THINKING_STILL_WORKING_MS = 8_000;
export const THINKING_NO_RESPONSE_MS = 20_000;

export function thinkingText(elapsedMs: number): string {
  if (elapsedMs >= THINKING_NO_RESPONSE_MS) return 'no response';
  if (elapsedMs >= THINKING_STILL_WORKING_MS) return 'still working…';
  return 'thinking…';
}

/** Text for the pill while `active` (voiceState === 'thinking'); resets when it ends. */
export function useThinkingText(active: boolean): string {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    setElapsed(0);
    if (!active) return;
    const start = Date.now();
    const timers = [THINKING_STILL_WORKING_MS, THINKING_NO_RESPONSE_MS].map((ms) =>
      window.setTimeout(() => setElapsed(Date.now() - start), ms),
    );
    return () => timers.forEach((t) => window.clearTimeout(t));
  }, [active]);
  return thinkingText(active ? elapsed : 0);
}
