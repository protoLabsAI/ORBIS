/**
 * Typed fetch wrappers for ORBIS's /api/* endpoints.
 *
 * Routes through `@tauri-apps/plugin-http` — the Rust-backed fetch —
 * because native `window.fetch` POSTs silently drop their body / hang
 * forever in WKWebView on macOS arm64 production builds (open Tauri
 * issues #11854, #13166, #13878 — labeled `status: upstream`, the bug
 * is in WebKit's networking subprocess). Routing through Rust uses
 * reqwest under the hood and works reliably.
 *
 * The base URL is set to the document origin (the Python sidecar's
 * loopback HTTP origin after Tauri navigates to it) so existing
 * `/api/*` paths resolve the same way relative URLs would have.
 *
 * Auth: the owner API key (when configured) is attached as
 * ``X-API-Key``. Single-user fallback omits it.
 */

import { fetch as tauriFetch } from '@tauri-apps/plugin-http';
import { authHeaders } from '@/auth/apiKey';
import { logBus } from '@/shared/logBus';

/** Resolve a relative API path against the document origin. */
function url(path: string): string {
  return new URL(path, window.location.href).toString();
}

export type Verbosity = 'silent' | 'brief' | 'narrated' | 'chatty';
export type VerbosityResponse = { verbosity: Verbosity };

export type Whoami = {
  id: string;
  display_name: string;
  auth_source: 'infisical' | 'file' | 'empty';
};

export type StarterOrb = {
  slug: string;
  name: string;
  description: string;
  variant: string;
  palette: string;
  params: Record<string, unknown>;
};

export type StarterOrbsResponse = { starters: StarterOrb[] };

export type OrbisConfig = {
  persona?: {
    slug?: string;
    name?: string;
    user_name?: string;
    system_prompt?: string;
    system_prompt_file?: string;
    temperature?: number;
    max_tokens?: number;
    filler_verbosity?: Verbosity;
  };
  voice?: {
    tts_backend?: 'kokoro' | 'openai' | 'elevenlabs' | 'fish';
    voice?: string;
    // OpenAI-compatible endpoint overrides. Empty / undefined means
    // fall back to TTS_OPENAI_* env defaults at session startup.
    tts_url?: string;
    tts_model?: string;
    tts_api_key?: string;
  };
  llm?: {
    url?: string;
    model?: string;
    api_key?: string;
    api_key_env?: string;
    extra_body?: Record<string, unknown> | null;
  };
  stt?: {
    backend?: 'local' | 'openai' | 'sensevoice';
    // HF model id used by the local Whisper backend. Honored at boot
    // only; runtime changes warn and no-op until restart.
    whisper_model?: string;
    // OpenAI-compatible endpoint overrides.
    url?: string;
    model?: string;
    api_key?: string;
  };
  orb?: {
    variant?: string;
    palette?: string;
    params?: Record<string, unknown>;
    // Authoring deltas per voice state and mood dimension — see
    // DECISIONS.md (2026-04-23 amendment) and
    // web/src/plugins/orb/compose.ts for the composition math.
    // Values are additive (numbers) or replacement (strings like hex
    // colors). Absent entries compose to no-op.
    state_overrides?: Partial<
      Record<'idle' | 'listening' | 'thinking' | 'speaking',
        Record<string, number | string>>
    >;
    mood_overrides?: Partial<
      Record<'valence' | 'arousal' | 'guardedness',
        Record<string, number | string>>
    >;
  };
};

export type EntitlementState = {
  customization: { active: boolean; configured: boolean; gate_mode?: 'open' | 'closed' };
};

export type PersonalityAxis = {
  axis: string;
  value: number;
  updated_at: string;
};

export type Mood = {
  valence: number;
  arousal: number;
  guardedness: number;
  updated_at: string;
} | null;

export type PersonalityEvent = {
  axis: string;
  delta: number;
  reason: string;
  at: string;
};

export type PersonalityState = {
  axes: PersonalityAxis[];
  mood: Mood;
  recent_events: PersonalityEvent[];
  sessions: {
    count: number;
    last_ended_at: string | null;
  };
};

export interface DelegateA2A {
  name: string;
  type: 'a2a';
  description: string;
  url: string;
  auth?: { scheme?: 'apiKey' | 'bearer'; credentialsEnv?: string };
  headers?: Record<string, string>;
}

export interface DelegateOpenAI {
  name: string;
  type: 'openai';
  description: string;
  url: string;
  model: string;
  api_key_env?: string;
  system_prompt?: string;
  max_tokens?: number;
  temperature?: number;
}

export type Delegate = DelegateA2A | DelegateOpenAI;

export type DelegateHealth = {
  ok: boolean | null;
  latency_ms: number | null;
  last_checked: number | null;
  last_error?: string | null;
  consecutive_failures: number;
};

export type DelegateWithStatus = Delegate & {
  configured: boolean;
  health?: DelegateHealth | null;
};

export type DelegateTestResult = {
  ok: boolean;
  latency_ms?: number;
  error?: string;
  status?: number;
};

/** Thrown when a response carries HTTP 401 — signals the key is wrong/missing. */
export class UnauthorizedError extends Error {
  constructor(path: string) { super(`${path} → 401 unauthorized`); }
}

export class ApiError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function get<T>(path: string): Promise<T> {
  return request<T>('GET', path);
}

async function postJSON<T>(path: string, body: unknown): Promise<T> {
  return sendJSON<T>('POST', path, body);
}

async function putJSON<T>(path: string, body: unknown): Promise<T> {
  return sendJSON<T>('PUT', path, body);
}

async function deleteJSON<T>(path: string): Promise<T> {
  return sendJSON<T>('DELETE', path);
}

async function sendJSON<T>(method: string, path: string, body?: unknown): Promise<T> {
  return request<T>(method, path, body);
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const t0 = performance.now();
  let r: Awaited<ReturnType<typeof tauriFetch>>;
  try {
    r = await tauriFetch(url(path), {
      method,
      headers: body === undefined
        ? authHeaders()
        : authHeaders({ 'Content-Type': 'application/json' }),
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (e) {
    const elapsed = Math.round(performance.now() - t0);
    logBus.push({
      source: 'api',
      level: 'error',
      message: `${method} ${path} -> ${(e as Error).message} (${elapsed}ms)`,
    });
    throw e;
  }
  const elapsed = Math.round(performance.now() - t0);
  logBus.push({
    source: 'api',
    level: r.ok ? 'info' : 'warn',
    message: `${method} ${path} -> ${r.status} (${elapsed}ms)`,
  });
  if (r.status === 401) throw new UnauthorizedError(path);
  if (!r.ok) {
    let detail = `HTTP ${r.status}`;
    try {
      const parsed = await r.json();
      if (parsed?.error) detail = String(parsed.error);
    } catch {
      // Keep generic status detail.
    }
    throw new ApiError(`${path} → ${detail}`, r.status);
  }
  return r.json() as Promise<T>;
}

export const api = {
  whoami: () => get<Whoami>('/api/whoami'),
  verbosity: () => get<VerbosityResponse>('/api/verbosity'),
  setVerbosity: (level: Verbosity) =>
    postJSON<{ verbosity?: Verbosity; error?: string }>('/api/verbosity', { level }),
  starterOrbs: () => get<StarterOrbsResponse>('/api/starter_orbs'),
  config: () => get<{ config: OrbisConfig }>('/api/config'),
  putConfig: (patch: OrbisConfig) =>
    postJSON<{ ok?: boolean; config?: OrbisConfig; persona?: string }>('/api/config', patch),
  entitlement: () => get<EntitlementState>('/api/entitlement'),
  createCheckout: () => postJSON<{ url: string }>('/api/entitlement/checkout', {}),
  personality: () => get<PersonalityState>('/api/personality'),
  selectStarter: (slug: string) =>
    postJSON<{ ok: boolean; starter: StarterOrb }>('/api/orb/select_starter', { slug }),

  // LLM-provider probing for the setup wizard. These routes are
  // unauth — the wizard runs before the owner key is set and what's
  // really being validated is the user's LLM credentials.
  llmTest: (body: { url: string; model: string; api_key?: string }) =>
    postJSON<{ ok: boolean; latency_ms?: number; error?: string; status?: number }>(
      '/api/llm/test', body,
    ),
  llmModels: (body: { url: string; api_key?: string }) =>
    postJSON<{ ok: boolean; models: string[]; error?: string }>(
      '/api/llm/models', body,
    ),
  llmDetectLocal: () =>
    get<Partial<Record<'ollama' | 'lm_studio', { url: string; models: string[] }>>>(
      '/api/llm/detect_local',
    ),
  ttsVoices: (backend: string) =>
    get<{
      backend: string;
      voices: Array<{ id: string; label: string; cached?: boolean }>;
      fish_url?: string;
      error?: string;
    }>(`/api/tts/voices?backend=${encodeURIComponent(backend)}`),
  ttsDownloadVoice: (body: { backend: string; voice: string }) =>
    postJSON<{ ok: boolean; path?: string; error?: string }>(
      '/api/tts/voices/download', body,
    ),
  delegates: {
    list: () => get<{ delegates: DelegateWithStatus[] }>('/api/delegates'),
    create: (entry: Delegate) =>
      postJSON<{ ok: boolean; delegates: DelegateWithStatus[] }>(
        '/api/delegates', entry,
      ),
    update: (name: string, entry: Delegate) =>
      putJSON<{ ok: boolean; delegates: DelegateWithStatus[] }>(
        `/api/delegates/${encodeURIComponent(name)}`, entry,
      ),
    remove: (name: string) =>
      deleteJSON<{ ok: boolean; delegates: DelegateWithStatus[] }>(
        `/api/delegates/${encodeURIComponent(name)}`,
      ),
    test: (entry: Delegate) =>
      postJSON<DelegateTestResult>('/api/delegates/test', entry),
  },
};
