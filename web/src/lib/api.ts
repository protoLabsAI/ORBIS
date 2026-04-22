/**
 * Typed fetch wrappers for protoVoice's /api/* endpoints. These hit
 * the server through the same origin — Vite proxies in dev, real
 * origin in the deployed SPA.
 */

export type SkillViz = {
  variant?: string;
  palette?: string;
  params?: Record<string, unknown>;
};
export type Skill = {
  slug: string;
  name: string;
  description?: string;
  viz?: SkillViz;
};
export type SkillsResponse = { active: string; locked?: boolean; skills: Skill[] };
export type Verbosity = 'silent' | 'brief' | 'narrated' | 'chatty';
export type VerbosityResponse = { verbosity: Verbosity };

async function get<T>(path: string): Promise<T> {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path} → HTTP ${r.status}`);
  return r.json() as Promise<T>;
}

async function postJSON<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${path} → HTTP ${r.status}`);
  return r.json() as Promise<T>;
}

export const api = {
  skills: () => get<SkillsResponse>('/api/skills'),
  setSkill: (slug: string) => postJSON<{ active?: string; error?: string }>('/api/skills', { slug }),
  verbosity: () => get<VerbosityResponse>('/api/verbosity'),
  setVerbosity: (level: Verbosity) =>
    postJSON<{ verbosity?: Verbosity; error?: string }>('/api/verbosity', { level }),
  cloneVoice: async (form: {
    slug: string;
    audio: File;
    name?: string;
    transcript?: string;
    description?: string;
  }): Promise<{
    error?: string;
    slug?: string;
    transcript?: string;
    auto_transcribed?: boolean;
  }> => {
    const fd = new FormData();
    fd.append('audio', form.audio);
    fd.append('slug', form.slug);
    if (form.name) fd.append('name', form.name);
    if (form.transcript) fd.append('transcript', form.transcript);
    if (form.description) fd.append('description', form.description);
    const r = await fetch('/api/voice/clone', { method: 'POST', body: fd });
    if (!r.ok) throw new Error(`voice/clone → HTTP ${r.status}`);
    return r.json();
  },
};
