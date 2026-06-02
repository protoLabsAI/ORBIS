# Configure the LLM

Choose (or switch) the model that powers the orb's routing and replies. For the
full list of providers and advanced options, see the
[Agent reference](/reference/agent#the-llm).

## Pick a provider

1. Open **Settings → Agent → LLM**.
2. Choose a **provider**:
   - **Local** (private, no key) — *Built-in (MLX)* is the best on-device option
     on Apple Silicon; *Ollama*, *LM Studio*, and *vLLM* use a local server you
     run.
   - **Hosted** — *protoLabs*, *OpenAI*, *Anthropic*, and others. These need an
     **API key**.
3. If the provider needs one, paste the **API key**.
4. Save. Changes take effect on the next conversation.

## Confirm it works

Just talk to the orb — if it replies, the model is reachable. If it hears you
but never answers, the LLM is the usual culprit; re-check the URL and key, and
see [Voice isn't working](/how-to/voice-not-working#orbis-hears-me-but-doesn-t-reply).

## Advanced (optional)

These live in [`orbis.yaml`](/reference/config) (or via env, no rebuild needed):

- **Failover backup** — add a `fallback:` model (a local one is ideal) so the
  orb keeps talking if your primary errors.
- **Two-model routing** — `router_model` (stronger, for the decision turn) +
  `content_model` (faster, for narration). OpenAI-compatible gateways only.
- **Micro tier** — `micro_model` for the cheap throwaway generation (fillers,
  acknowledgements).

See [Agent reference → Advanced LLM options](/reference/agent#advanced-llm-options).
