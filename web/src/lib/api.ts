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
  orb?: {
    variant?: string;
    palette?: string;
    params?: Record<string, unknown>;
  };
};

export type EntitlementState = {
  customization: { active: boolean; configured: boolean };
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
};
