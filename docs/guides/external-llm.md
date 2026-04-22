# Use an External LLM

By default, protoVoice starts its own vLLM subprocess. For anything beyond a small local router model, point at an external OpenAI-compatible endpoint instead.

## Opt out of the built-in vLLM

```bash
START_VLLM=0 \
LLM_URL=http://10.0.0.10:8000/v1 \
LLM_SERVED_NAME=local \
docker compose up -d protovoice
```

- **`START_VLLM=0`** — skip the subprocess entirely.
- **`LLM_URL`** — any OpenAI-compat `/v1`: a LiteLLM gateway, an external vLLM, OpenAI itself, Groq, Anthropic-via-proxy, anything.
- **`LLM_SERVED_NAME`** — the model name as served by that endpoint.
- **`LLM_API_KEY`** — if the endpoint requires it.

## Through the protoLabs gateway

If you're running the protoLabs fleet, point at the LiteLLM gateway on `pve01`:

```bash
START_VLLM=0 \
LLM_URL=http://gateway:4000/v1 \
LLM_SERVED_NAME=claude-opus-4-6 \
LLM_API_KEY=$GATEWAY_API_KEY \
docker compose up -d protovoice
```

This is the intended path for "router + thinker" splits later on: a small local model handles routing and filler, while heavy reasoning punts through the gateway to Opus/Gemini/GPT.

## Thinking-mode models (Qwen3.5+, DeepSeek R1, etc.)

These emit `reasoning_content` deltas instead of `content` deltas by default — pipecat will see zero text tokens.

protoVoice already sends `extra_body={"chat_template_kwargs":{"enable_thinking":False}}` in `app.py`, which turns off the thinking template on vLLM. If your endpoint uses a different switch, edit the `extra` payload on `OpenAILLMService.Settings(...)`.

## Latency note

TTFB through a LAN gateway is typically 100-300 ms. Through a cloud API (OpenAI, Anthropic) it's 400 ms-2 s. Your total turn latency is roughly `STT + LLM_TTFB + first_sentence_TTS + network` — see [Pipeline Shape](/reference/pipeline-shape) for how to reason about it.
