/**
 * LLM provider presets — shared by the setup wizard and the runtime
 * Settings panel so "Ollama" / "OpenAI" / etc. stay one list, one blurb,
 * one default URL. Adding a new provider here lights it up everywhere.
 */

export interface LLMPreset {
  id: string;
  label: string;
  url: string;
  model: string;
  needsKey: boolean;
  keyPlaceholder?: string;
  blurb: string;
  /** Show in the always-visible top row of the wizard's LLM picker.
   * Non-featured presets live behind a "Show all providers" expander
   * so the default surface stays scannable — most users want one of
   * the local options or one of the two big cloud labels, not the
   * full 15-tile grid. */
  featured?: boolean;
}

// Local-first + hosted + gateway presets. All OpenAI-protocol unless
// labeled otherwise. The actual URL/model/key fields remain editable
// after picking a preset — the preset just pre-fills them.
//
// Ordering: Ollama first because the bundled desktop app targets
// users running a local LLM. Cloud providers follow for users who
// prefer hosted inference; `custom` stays last for full freedom.
export const LLM_PRESETS: LLMPreset[] = [
  // --- Local / self-hosted (the desktop-app happy path) ---
  {
    id: 'mlx', label: 'Built-in (MLX)',
    url: 'mlx://mlx-community/Qwen3.5-4B-MLX-4bit', model: 'mlx-community/Qwen3.5-4B-MLX-4bit',
    needsKey: false, featured: true,
    blurb: 'Apple Silicon native — Qwen 3.5 4B running in-process via Apple’s MLX framework. Best quality on M-series. First-time download ~2.5GB; cached after that.',
  },
  {
    id: 'ollama', label: 'Ollama',
    url: 'http://127.0.0.1:11434/v1', model: 'gemma3n:e2b',
    needsKey: false, featured: true,
    blurb: 'Local separate process. Use if you already run Ollama or want to share a model with other tools.',
  },
  {
    id: 'lm_studio', label: 'LM Studio',
    url: 'http://127.0.0.1:1234/v1', model: '',
    needsKey: false,
    blurb: 'Local. Start the server in LM Studio first.',
  },
  {
    id: 'vllm', label: 'vLLM (local)',
    url: 'http://127.0.0.1:8100/v1', model: 'Qwen/Qwen3.5-4B',
    needsKey: false,
    blurb: 'Your own vLLM server.',
  },
  // --- Hosted cloud ---
  {
    id: 'openai', label: 'OpenAI',
    url: 'https://api.openai.com/v1', model: 'gpt-4o-mini',
    needsKey: true, keyPlaceholder: 'sk-...', featured: true,
    blurb: 'Fast + cheap. A few cents per hour of chatter.',
  },
  {
    id: 'anthropic', label: 'Anthropic',
    url: 'https://api.anthropic.com/v1', model: 'claude-haiku-4-5',
    needsKey: true, keyPlaceholder: 'sk-ant-...', featured: true,
    blurb: 'Claude Haiku. Great personality, slightly pricier.',
  },
  {
    id: 'groq', label: 'Groq',
    url: 'https://api.groq.com/openai/v1', model: 'llama-3.1-8b-instant',
    needsKey: true, keyPlaceholder: 'gsk_...',
    blurb: 'Blazing fast, near-free.',
  },
  {
    id: 'deepseek', label: 'DeepSeek',
    url: 'https://api.deepseek.com/v1', model: 'deepseek-chat',
    needsKey: true, keyPlaceholder: 'sk-...',
    blurb: 'Cheap + surprisingly capable.',
  },
  {
    id: 'openrouter', label: 'OpenRouter',
    url: 'https://openrouter.ai/api/v1', model: 'openai/gpt-4o-mini',
    needsKey: true, keyPlaceholder: 'sk-or-...',
    blurb: 'One key, every model.',
  },
  {
    id: 'together', label: 'Together AI',
    url: 'https://api.together.xyz/v1', model: 'meta-llama/Llama-3.3-70B-Instruct-Turbo',
    needsKey: true, keyPlaceholder: 'api key',
    blurb: 'Open-weight models, fast inference.',
  },
  {
    id: 'mistral', label: 'Mistral',
    url: 'https://api.mistral.ai/v1', model: 'mistral-small-latest',
    needsKey: true, keyPlaceholder: 'api key',
    blurb: 'European, hosted, capable.',
  },
  {
    id: 'fireworks', label: 'Fireworks AI',
    url: 'https://api.fireworks.ai/inference/v1', model: 'accounts/fireworks/models/llama-v3p1-8b-instruct',
    needsKey: true, keyPlaceholder: 'api key',
    blurb: 'Low-latency inference cloud.',
  },
  {
    id: 'moonshot', label: 'Moonshot / Kimi',
    url: 'https://api.moonshot.ai/v1', model: 'moonshot-v1-8k',
    needsKey: true, keyPlaceholder: 'api key',
    blurb: 'Long-context specialist.',
  },
  {
    id: 'xai', label: 'xAI',
    url: 'https://api.x.ai/v1', model: 'grok-2',
    needsKey: true, keyPlaceholder: 'xai-...',
    blurb: 'Grok, via OpenAI-compat API.',
  },
  // --- Gateway / proxy ---
  {
    id: 'litellm', label: 'LiteLLM gateway',
    url: 'http://localhost:4000/v1', model: 'gpt-4o-mini',
    needsKey: true, keyPlaceholder: 'master key',
    blurb: 'Route to any provider through one URL.',
  },
  // --- Custom ---
  {
    id: 'custom', label: 'Custom',
    url: 'https://api.proto-labs.ai/v1', model: 'protolabs/fast',
    needsKey: true, keyPlaceholder: 'api key (optional)', featured: true,
    blurb: 'Paste your own OpenAI-compatible URL — works for any gateway, proxy, or custom endpoint.',
  },
];

/** Best-effort match of a url+model back to a known preset id. Lets the
 * Settings panel highlight the active preset tile on open.
 *
 * Three explicit stages so the precedence stays readable:
 *   1. exact URL equality — most trustworthy
 *   2. substring URL match — catches paths like `.../v1/chat/completions`
 *      appended to a preset base URL
 *   3. model-name equality — last-ditch signal when URL tells us nothing
 */
export function matchPreset(url: string, model: string): string {
  const normalized = (s: string) => s.trim().replace(/\/$/, '').toLowerCase();
  const nu = normalized(url);
  const nm = normalized(model);

  // 1 — exact URL match.
  for (const p of LLM_PRESETS) {
    if (p.url && normalized(p.url) === nu) return p.id;
  }
  // 2 — substring URL match.
  for (const p of LLM_PRESETS) {
    if (p.url && nu.includes(normalized(p.url))) return p.id;
  }
  // 3 — model name match.
  for (const p of LLM_PRESETS) {
    if (p.model && nm === normalized(p.model)) return p.id;
  }
  return 'custom';
}
