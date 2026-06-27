/**
 * In-browser stand-in for the Python sidecar's /api/* surface.
 *
 * The real ORBIS frontend (web/src) only ever talks to its backend through
 * invoke('api_request', …) — never fetch (a deliberate Tauri-only choice;
 * see web/src/lib/api.ts). Here we answer those calls with local, canned
 * data so the mirrored app boots straight into the live UI with no server.
 *
 * PR1 keeps this read-mostly: GETs return sensible defaults, writes are
 * accepted and echoed. The conversational brain is scripted (mockEngine);
 * PR2 wires the on-device LLM in front of these same routes.
 */
import type { OrbisConfig } from '@/lib/api';

export interface ApiRequestArgs {
  method: string;
  path: string;
  body: string | null;
  headers?: Record<string, string>;
}

export interface ApiResponse {
  status: number;
  body: string;
}

function ok(data: unknown): ApiResponse {
  return { status: 200, body: JSON.stringify(data) };
}

// The setup-wizard orb pool, mirrored from config/starter_orbs.yaml. Each
// is a complete (variant, palette) pair — the palette's defaults render a
// visible orb, so params can stay empty.
const STARTERS = [
  { slug: 'aurora', name: 'Aurora', description: 'Shifting green-blue light, patient and wide.', variant: 'fractal', palette: 'Aurora', params: {} },
  { slug: 'ember', name: 'Ember', description: 'Warm crackling light with hot core.', variant: 'fractal', palette: 'Ember', params: {} },
  { slug: 'forest', name: 'Forest', description: 'Green-gold canopy light, deep and settled.', variant: 'fractal', palette: 'Forest', params: {} },
  { slug: 'andromeda', name: 'Andromeda', description: 'Deep cosmic clouds with drifting dust.', variant: 'nebula', palette: 'Andromeda', params: {} },
  { slug: 'helios', name: 'Helios', description: 'Solar and radiant, humming with energy.', variant: 'nebula', palette: 'Helios', params: {} },
  { slug: 'prism', name: 'Prism', description: 'Faceted refracted light, crystalline and bright.', variant: 'crystal', palette: 'Prism', params: {} },
  { slug: 'obsidian', name: 'Obsidian', description: 'Sharp and dark with hidden edges.', variant: 'crystal', palette: 'Obsidian', params: {} },
  { slug: 'constellation', name: 'Constellation', description: 'Scattered stars in slow orbit.', variant: 'particles', palette: 'Constellation', params: {} },
];

// The demo's persistent-enough config. setup.complete=true skips the
// first-run wizard; orb seeds the visible orb. Edits made in Settings are
// merged here for the session (lost on reload — no server, by design).
let config: OrbisConfig = {
  persona: { slug: 'orbis', name: 'Orbis', user_name: 'You', filler_verbosity: 'narrated' },
  voice: { tts_backend: 'kokoro', voice: 'af_heart', local_models: 'on_device' },
  llm: { model: 'gemma-4-e2b (on-device)' },
  stt: { backend: 'local', whisper_model: 'whisper-base' },
  // variant + palette are BOTH required — applyPreset(palette) is what
  // fills the shader params; without a palette the orb renders invisible.
  orb: { variant: 'fractal', palette: 'Aurora' },
  wakeword: { enabled: false },
  setup: { complete: true },
  agent: { allow_orb_control: true },
};

function mergeConfig(patch: Partial<OrbisConfig>): void {
  config = { ...config, ...patch };
}

export async function httpRequest(args: ApiRequestArgs): Promise<ApiResponse> {
  const { method, path, body } = args;
  const p = path.split('?')[0];

  if (method === 'GET') {
    switch (p) {
      case '/api/whoami':
        return ok({ id: 'you', display_name: 'You', auth_source: 'empty' });
      case '/api/config':
        return ok({ config });
      case '/api/verbosity':
        return ok({ verbosity: config.persona?.filler_verbosity ?? 'narrated' });
      case '/api/personality':
        return ok({
          axes: [],
          mood: null,
          recent_events: [],
          sessions: { count: 0, last_ended_at: null },
        });
      case '/api/starter_orbs':
        return ok({ starters: STARTERS });
      case '/api/orbs':
        return ok({ orbs: [] });
      case '/api/reminders':
        return ok({ ok: true, reminders: [] });
      case '/api/delegates':
        return ok({ delegates: [] });
      case '/api/delegate-types':
        return ok({ types: [] });
      case '/api/wakeword/models':
        return ok({ models: [] });
      case '/api/llm/detect_local':
        return ok({});
      default:
        return ok({});
    }
  }

  // Writes: accept and echo so the UI's optimistic flows succeed.
  if (method === 'POST' || method === 'PUT') {
    let parsed: unknown = undefined;
    if (body) {
      try {
        parsed = JSON.parse(body);
      } catch {
        /* ignore */
      }
    }
    if (p === '/api/config') {
      if (parsed && typeof parsed === 'object') mergeConfig(parsed as Partial<OrbisConfig>);
      return ok({ ok: true, config });
    }
    if (p === '/api/orb/select_starter') {
      const slug = (parsed as { slug?: string } | undefined)?.slug;
      const starter = STARTERS.find((s) => s.slug === slug);
      if (starter) {
        config = { ...config, orb: { variant: starter.variant, palette: starter.palette } };
        return ok({ ok: true, starter });
      }
      return { status: 404, body: JSON.stringify({ ok: false, error: 'unknown starter' }) };
    }
    return ok({ ok: true });
  }

  if (method === 'DELETE') {
    return ok({ ok: true });
  }

  return ok({});
}
