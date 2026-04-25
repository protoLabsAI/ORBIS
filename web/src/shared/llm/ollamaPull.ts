/**
 * Streaming Ollama-pull client.
 *
 * Wraps the backend's `/api/llm/pull` SSE endpoint as an
 * AsyncIterable so React components can consume progress with a
 * simple `for await` loop, no EventSource event-handler boilerplate.
 *
 * Backend pipes Ollama's native `/api/pull` NDJSON through as SSE
 * `data:` events; each line is the raw chunk
 *     {"status": "pulling abc...", "completed": 1234, "total": 5678}
 * with a final `event: done` once the model is installed (or
 * `event: error` if the pull failed).
 */

export interface PullProgress {
  status: string;
  completed?: number;
  total?: number;
  digest?: string;
  error?: string;
}

export async function* pullOllamaModel(
  name: string,
  url: string = 'http://127.0.0.1:11434',
  init?: { signal?: AbortSignal },
): AsyncGenerator<PullProgress> {
  const resp = await fetch('/api/llm/pull', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, url }),
    signal: init?.signal,
  });
  if (!resp.body) {
    yield { status: 'error', error: 'no response body' };
    return;
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      // SSE messages are separated by blank lines. Walk the buffer
      // for complete events; partials stay in `buf` for the next
      // chunk.
      let idx: number;
      while ((idx = buf.indexOf('\n\n')) !== -1) {
        const raw = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        const evt = parseSseFrame(raw);
        if (!evt) continue;
        if (evt.event === 'done') return;
        if (evt.event === 'error') {
          yield { status: 'error', error: evt.data };
          return;
        }
        try {
          const payload = JSON.parse(evt.data) as PullProgress;
          yield payload;
        } catch {
          // Ignore malformed lines — Ollama very occasionally emits
          // a non-JSON status string; downstream UI just stays on
          // its previous progress reading.
        }
      }
    }
  } finally {
    try {
      reader.releaseLock();
    } catch {
      // releaseLock can throw if the reader is already detached —
      // safe to ignore on cleanup.
    }
  }
}

interface ParsedSseFrame {
  event: string;
  data: string;
}

function parseSseFrame(raw: string): ParsedSseFrame | null {
  let event = 'message';
  const dataLines: string[] = [];
  for (const line of raw.split(/\r?\n/)) {
    if (line.startsWith('event:')) {
      event = line.slice(6).trim();
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trimStart());
    }
  }
  if (dataLines.length === 0) return null;
  return { event, data: dataLines.join('\n') };
}
