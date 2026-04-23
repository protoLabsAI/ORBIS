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
}

// Hosted + local + gateway presets. All OpenAI-protocol unless labeled
// otherwise. The actual URL/model/key fields remain editable after
// picking a preset — the preset just pre-fills them.
export const LLM_PRESETS: LLMPreset[] = [
  // --- Hosted cloud ---
  {
    id: 'openai', label: 'OpenAI',
    url: 'https://api.openai.com/v1', model: 'gpt-4o-mini',
    needsKey: true, keyPlaceholder: 'sk-...',
    blurb: 'Fast + cheap. A few cents per hour of chatter.',
  },
  {
    id: 'anthropic', label: 'Anthropic',
    url: 'https://api.anthropic.com/v1', model: 'claude-haiku-4-5',
    needsKey: true, keyPlaceholder: 'sk-ant-...',
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
  // --- Local / self-hosted ---
  {
    id: 'ollama', label: 'Ollama',
    url: 'http://127.0.0.1:11434/v1', model: 'llama3.2',
    needsKey: false,
    blurb: 'Local. Auto-detected if running.',
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
    url: '', model: '',
    needsKey: true, keyPlaceholder: 'api key (optional)',
    blurb: 'Paste your own OpenAI-compatible URL.',
  },
];

/** Best-effort match of a url+model back to a known preset id. Lets the
 * Settings panel highlight the active preset tile on open. */
export function matchPreset(url: string, model: string): string {
  const normalized = (s: string) => s.trim().replace(/\/$/, '').toLowerCase();
  const nu = normalized(url);
  const nm = normalized(model);
  // Exact URL match first — more reliable than model name.
  for (const p of LLM_PRESETS) {
    if (!p.url) continue;
    if (normalized(p.url) === nu) return p.id;
  }
  // Then a substring match (catches litellm-proxied urls like ava:4000).
  for (const p of LLM_PRESETS) {
    if (p.url && nu.includes(normalized(p.url))) return p.id;
    if (p.model && nm === normalized(p.model)) return p.id;
  }
  return 'custom';
}
