/**
 * Typed fetch wrappers for ORBIS's /api/* endpoints. These hit the
 * server through the same origin — Vite proxies in dev, real origin
 * in the deployed SPA. All calls attach the owner API key (when set)
 * as ``X-API-Key``.
 */

import { authHeaders } from '@/auth/apiKey';

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
  };
  llm?: {
    url?: string;
    model?: string;
    api_key?: string;
    api_key_env?: string;
    extra_body?: Record<string, unknown> | null;
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
  customization: { active: boolean; configured: boolean };
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

/** Thrown when a response carries HTTP 401 — signals the key is wrong/missing. */
export class UnauthorizedError extends Error {
  constructor(path: string) { super(`${path} → 401 unauthorized`); }
}

async function get<T>(path: string): Promise<T> {
  const r = await fetch(path, { headers: authHeaders() });
  if (r.status === 401) throw new UnauthorizedError(path);
  if (!r.ok) throw new Error(`${path} → HTTP ${r.status}`);
  return r.json() as Promise<T>;
}

async function postJSON<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(path, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
  });
  if (r.status === 401) throw new UnauthorizedError(path);
  if (!r.ok) throw new Error(`${path} → HTTP ${r.status}`);
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
};
