/**
 * Typed fetch wrappers for ORBIS's /api/* endpoints.
 *
 * Routes through the Rust `api_request` IPC command (reqwest) rather
 * than WKWebView's fetch. On macOS (and especially Tahoe) WKWebView
 * silently drops POST bodies and hangs reading response bodies (open
 * Tauri issues #11854, #13166, #13878 — the bug is in WebKit's
 * networking subprocess). Doing the HTTP in Rust sidesteps WebKit's
 * network stack entirely and works reliably.
 *
 * The base URL is the sidecar's loopback origin, injected by the Rust
 * shell once the backend is ready (the bundled UI loads from
 * tauri://localhost and never navigates) — see `backendBase()`.
 *
 * Auth: the owner API key (when configured) is attached as
 * ``X-API-Key``. Single-user fallback omits it.
 */

import { invoke } from '@tauri-apps/api/core';
import { authHeaders } from '@/auth/apiKey';
import { logBus } from '@/shared/logBus';

/**
 * Resolve the sidecar's loopback base URL.
 *
 * The bundled native UI loads from the Tauri custom scheme
 * (tauri://localhost) and never navigates — so `window.location` is NOT
 * the backend. The Rust shell injects `window.__ORBIS_BACKEND__` (and
 * fires `orbis-backend-ready`) once the Python sidecar prints its ready
 * line. In plain http(s) dev contexts the page origin is the backend.
 *
 * Callers `await backendBase()`, so API/SSE calls issued before the
 * sidecar is ready queue until the URL arrives instead of resolving
 * against the wrong (tauri://) origin.
 */
let _backendBase: string | null = resolveInitialBase();
const _baseWaiters: Array<(b: string) => void> = [];

function resolveInitialBase(): string | null {
  const injected = (window as unknown as { __ORBIS_BACKEND__?: string }).__ORBIS_BACKEND__;
  if (typeof injected === 'string' && injected) return injected;
  const proto = window.location.protocol;
  if (proto === 'http:' || proto === 'https:') return window.location.origin;
  return null;
}

function setBackendBase(b: string): void {
  _backendBase = b;
  while (_baseWaiters.length) _baseWaiters.shift()!(b);
}

if (typeof window !== 'undefined') {
  window.addEventListener('orbis-backend-ready', () => {
    const injected = (window as unknown as { __ORBIS_BACKEND__?: string }).__ORBIS_BACKEND__;
    if (typeof injected === 'string' && injected) setBackendBase(injected);
  });
}

/** Synchronous peek at the sidecar base URL — null until the backend is ready. */
export function backendBaseSync(): string | null {
  return _backendBase;
}

/** Awaitable sidecar base URL — resolves once the backend is ready. */
export async function backendBase(): Promise<string> {
  if (_backendBase) return _backendBase;
  try {
    const fromShell = await invoke<string | null>('backend_url');
    if (fromShell) {
      setBackendBase(fromShell);
      return fromShell;
    }
  } catch {
    // `backend_url` command unavailable (e.g. plain browser dev) — fall
    // through and wait for the injection event instead.
  }
  return new Promise<string>((resolve) => {
    // Re-check: the injection event may have fired during the await above.
    if (_backendBase) resolve(_backendBase);
    else _baseWaiters.push(resolve);
  });
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

// Persona catalog (epic #611). `meta`/`prompt` are the raw persona-file
// contents for the manager dialog; the composed identity lives server-side.
export type PersonaEntry = {
  slug: string;
  name: string;
  description: string;
  source: 'config' | 'bundled' | 'user';
  editable: boolean;
  meta?: Record<string, unknown>;
  prompt?: string;
};

export type PersonasResponse = { active: string; personas: PersonaEntry[] };

export type PersonaSwitchResult = {
  ok: boolean;
  active: string;
  name: string;
  applies: 'live' | 'restart';
  notes: string[];
  viz: { variant?: string; palette?: string; params?: Record<string, unknown> };
};

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
    // First-run wizard's voice-models choice: download on-device STT+TTS
    // ('on_device') or bring-your-own backend ('byo'). Gates the eager
    // prewarm in app.py.
    local_models?: 'on_device' | 'byo';
  };
  llm?: {
    url?: string;
    model?: string;
    api_key?: string;
    api_key_env?: string;
    extra_body?: Record<string, unknown> | null;
  };
  stt?: {
    backend?: 'local' | 'openai' | 'sensevoice' | 'parakeet';
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
  wakeword?: {
    /** Hands-free wake-word listening on/off. */
    enabled?: boolean;
    /** Active wake-word model id (catalog id, e.g. 'hey_orbis'). */
    model?: string;
    /** Detection threshold 0..1 — higher = fewer false triggers. */
    threshold?: number;
  };
  /** First-run wizard completion — the durable source of truth (the
   *  localStorage flag is just a fast-path cache that rebuilds wipe). */
  setup?: {
    complete?: boolean;
  };
  /** User-toggleable agent capabilities. */
  agent?: {
    /** Let the voice agent restyle the orb (the `set_orb_visual` tool). */
    allow_orb_control?: boolean;
  };
};

/** One entry in the wake-word model catalog (GET /api/wakeword/models). */
export interface WakeModel {
  id: string;
  name: string;
  description: string;
  filename: string;
  url: string;
  size_kb: number;
  /** 'shared' = melspec/embedding dep every wake word needs; 'wake' = a phrase. */
  kind: 'shared' | 'wake';
  recommended: boolean;
  downloaded: boolean;
}

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

export interface DelegateACP {
  name: string;
  type: 'acp';
  description: string;
  /** The agent binary ORBIS launches (e.g. "proto", "opencode", "npx"). */
  command: string;
  /** Args that start it in ACP mode (e.g. ["--acp"], ["acp"]). */
  args?: string[];
  /** The directory the agent is responsible for — its session working dir. */
  workdir: string;
}

export type Delegate = DelegateA2A | DelegateOpenAI | DelegateACP;

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

/** One field in a delegate type's config form (from GET /api/delegate-types).
 *  Dotted `key` (e.g. "auth.scheme") maps to a nested path on the entry. */
export interface DelegateFieldSpec {
  key: string;
  label: string;
  kind: 'text' | 'secret-env' | 'args' | 'path' | 'number' | 'textarea' | 'select';
  required: boolean;
  placeholder?: string;
  help?: string;
  options?: string[];
  default?: unknown;
}

export interface DelegateCapabilities {
  stream: boolean;
  inject: boolean;
  session: boolean;
  oneshot: boolean;
}

/** A registered delegate type — drives the generic New/Edit form + tile.
 *  Adding a backend adapter makes a new entry appear here automatically. */
export interface DelegateTypeSpec {
  type: string;
  label: string;
  blurb: string;
  capabilities: DelegateCapabilities;
  fields: DelegateFieldSpec[];
}

/** An A2A agent found broadcasting on the network (mDNS `_protoagent._tcp`,
 *  local port-scan, or tailnet probe) — a candidate to add as an a2a delegate. */
export interface DiscoveredAgent {
  name: string;
  description: string;
  /** Base URL of the agent (its A2A endpoint is `url + '/a2a'`). */
  url: string;
  host: string;
  port: number;
}

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
  // Gate until the sidecar URL is known (the Rust proxy needs it). Calls
  // issued during the ~20s sidecar boot queue here instead of erroring.
  await backendBase();
  let res: { status: number; body: string };
  try {
    // Route through the Rust IPC proxy rather than WKWebView fetch —
    // Tahoe's WKWebView drops/hangs HTTP bodies (see backendBase docs).
    res = await invoke<{ status: number; body: string }>('api_request', {
      method,
      path,
      body: body === undefined ? null : JSON.stringify(body),
      headers: body === undefined
        ? authHeaders()
        : authHeaders({ 'Content-Type': 'application/json' }),
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
  const ok = res.status >= 200 && res.status < 300;
  logBus.push({
    source: 'api',
    level: ok ? 'info' : 'warn',
    message: `${method} ${path} -> ${res.status} (${elapsed}ms)`,
  });
  if (res.status === 401) throw new UnauthorizedError(path);
  if (!ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const parsed = JSON.parse(res.body);
      if (parsed?.error) detail = String(parsed.error);
    } catch {
      // Keep generic status detail.
    }
    throw new ApiError(`${path} → ${detail}`, res.status);
  }
  return JSON.parse(res.body) as T;
}

export interface ReminderItem {
  id: number;
  text: string;
  fire_at: string;
  recurring: boolean;
  repeat_secs: number | null;
}

export const api = {
  whoami: () => get<Whoami>('/api/whoami'),
  reminders: {
    list: () => get<{ ok: boolean; reminders: ReminderItem[] }>('/api/reminders'),
    cancel: (body: { id?: number; match?: string; all?: boolean }) =>
      postJSON<{ ok: boolean; cancelled: number }>('/api/reminders/cancel', body),
  },
  verbosity: () => get<VerbosityResponse>('/api/verbosity'),
  setVerbosity: (level: Verbosity) =>
    postJSON<{ verbosity?: Verbosity; error?: string }>('/api/verbosity', { level }),
  starterOrbs: () => get<StarterOrbsResponse>('/api/starter_orbs'),
  config: () => get<{ config: OrbisConfig }>('/api/config'),
  putConfig: (patch: OrbisConfig) =>
    postJSON<{ ok?: boolean; config?: OrbisConfig; persona?: string; llm_applied_live?: boolean }>('/api/config', patch),
  personality: () => get<PersonalityState>('/api/personality'),
  selectStarter: (slug: string) =>
    postJSON<{ ok: boolean; starter: StarterOrb }>('/api/orb/select_starter', { slug }),
  personas: () => get<PersonasResponse>('/api/personas'),
  setActivePersona: (slug: string) =>
    postJSON<PersonaSwitchResult>('/api/personas/active', { slug }),
  putPersona: (slug: string, body: Record<string, unknown>) =>
    putJSON<{ ok: boolean; slug: string; path: string; shadows_bundled: boolean }>(
      `/api/personas/${encodeURIComponent(slug)}`, body,
    ),
  deletePersona: (slug: string) =>
    deleteJSON<{ ok: boolean }>(`/api/personas/${encodeURIComponent(slug)}`),
  orbs: {
    // Imported `.orbis` definitions — typed loosely here; the orb-runtime
    // validator is the real contract (validateOrbDefinition on load).
    list: () => get<{ orbs: unknown[] }>('/api/orbs'),
    import: (definition: unknown) =>
      postJSON<{ ok?: boolean; id?: string; replaced?: boolean; error?: string; errors?: string[] }>(
        '/api/orbs', definition,
      ),
    remove: (id: string) =>
      deleteJSON<{ ok?: boolean; error?: string }>(`/api/orbs/${encodeURIComponent(id)}`),
  },

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
    /** Registered delegate types + their capabilities/field schema. Drives the
     *  generic New/Edit form, so a new backend adapter needs no UI change. */
    types: () => get<{ types: DelegateTypeSpec[] }>('/api/delegate-types'),
    /** Scan for A2A agents broadcasting nearby (box + LAN + tailnet); already-
     *  configured delegates and ORBIS itself are excluded. Takes a few seconds
     *  (the mDNS browse waits out its timeout). */
    discover: () => get<{ discovered: DiscoveredAgent[] }>('/api/delegates/discover'),
  },
  wakeword: {
    /** Catalog with live download status. */
    models: () => get<{ models: WakeModel[] }>('/api/wakeword/models'),
    /** Download one model. Resolves when complete; progress arrives on the
     *  `orbis-sse` `wakeword-download` event (see useWakewordDownloads). */
    download: (id: string) =>
      postJSON<{ ok?: boolean; error?: string }>(
        `/api/wakeword/models/${encodeURIComponent(id)}/download`, {},
      ),
    remove: (id: string) =>
      deleteJSON<{ ok: boolean }>(
        `/api/wakeword/models/${encodeURIComponent(id)}`,
      ),
  },
};
