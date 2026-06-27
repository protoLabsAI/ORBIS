/**
 * The in-browser "voice engine" — PR1 mock.
 *
 * It publishes the exact orbis-sse events the real app's useVoiceBridge
 * consumes (session / bot-state / transcript / tool-call), so the mirrored
 * orb, status pill, and transcripts animate through a real-looking
 * conversation with no backend. A gentle scripted loop runs as an ambient
 * backdrop; window.orbisDemo.say(text) drives a single turn manually.
 *
 * PR2 replaces this with on-device Gemma (typed turns); PR3 adds Whisper
 * STT + Kokoro TTS for the full voice loop — all behind this same surface.
 */
import { emitSse } from '../tauri-shim/bus';

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

interface Turn {
  user: string;
  bot: string;
  tool?: { name: string; args?: Record<string, unknown> };
}

const SCRIPT: Turn[] = [
  {
    user: 'Hey Orbis, what is this?',
    bot: "This is ORBIS running in your browser — the real app, no install. The brain, speech, and voice all run on your device.",
  },
  {
    user: 'What can you actually do?',
    bot: 'I sit between you and your agents — listening, routing to the right model or tool, and answering out loud.',
    tool: { name: 'route_to_delegate', args: { agent: 'research' } },
  },
  {
    user: 'Nice. How do I get the full version?',
    bot: 'Download the Mac app for the complete experience — wake word, real delegates, and your own models.',
  },
];

let started = false;

async function playTurn(turn: Turn): Promise<void> {
  emitSse('bot-state', { state: 'listening' });
  await sleep(1400);
  emitSse('transcript', { source: 'user', text: turn.user, final: true });
  await sleep(500);

  emitSse('bot-state', { state: 'thinking' });
  if (turn.tool) {
    emitSse('tool-call', { event: 'start', name: turn.tool.name, args: JSON.stringify(turn.tool.args ?? {}) });
    await sleep(1600);
    emitSse('tool-call', { event: 'end', name: turn.tool.name, outcome: 'success' });
  } else {
    await sleep(1400);
  }

  emitSse('bot-state', { state: 'speaking' });
  emitSse('transcript', { source: 'bot', text: turn.bot, final: true });
  // ~Speaking duration scaled to text length.
  await sleep(Math.min(7000, 1800 + turn.bot.length * 45));

  emitSse('bot-state', { state: 'idle' });
}

async function say(text: string): Promise<void> {
  await playTurn({ user: '(you)', bot: text });
}

async function loop(): Promise<void> {
  // Brief settle so the app has mounted its bridge listener.
  await sleep(1200);
  emitSse('session', { event: 'start', session_id: 'demo' });
  await sleep(800);

  for (;;) {
    for (const turn of SCRIPT) {
      await playTurn(turn);
      await sleep(3500);
    }
    await sleep(6000);
  }
}

export function startDemoEngine(): void {
  if (started) return; // guard StrictMode double-mount
  started = true;
  void loop();
  (window as unknown as { orbisDemo?: unknown }).orbisDemo = { say };
}
