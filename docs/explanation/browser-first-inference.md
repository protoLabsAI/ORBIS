# Browser-first inference (deferred research)

Research snapshot from 2026-04-22. Deferred in favor of the R3F orb migration. When we come back to this, start here — every link and model identity was verified on that date.

## Headline

Moving protoVoice's inference stack into the browser via WebGPU is viable for the LLM on modern desktop hardware (Gemma 4 E2B, ~20 tok/s on M3), **marginal on iPhone 15 Pro, painful on mid-range Android** [speculative — no direct benchmarks at the time]. The full pipeline cannot go client-side without rewriting pipecat's processor primitives in TypeScript, which would forfeit the voice-pipeline moat (BackchannelController, DeliveryController, BargeInGate, MicroAckInjector, async-tool inbox). A **hybrid** where the LLM runs in the browser but STT/TTS/pipeline stay server-side is the pragmatic path.

## Models confirmed

### `google/gemma-4-E2B-it` — the headline candidate
- Released 2026-04-02, Apache-2
- 2.3 B effective / 5.1 B with embeddings, 128K context
- **Native text + image + audio** (audio ≤ 30 s) — audio modality means Whisper can be skipped on the happy path
- ~1.3–1.5 GB at q4; day-one `transformers.js` v3 support; LiteRT-LM web-optimized variant published
- Speculative throughput: ~20–25 tok/s M3 MacBook, ~10–15 tok/s iPhone 15 Pro, ~4–8 tok/s mid-range Android, ~50–80 tok/s laptop RTX 4060

### `prism-ml/Bonsai-1.7B-gguf` — 1-bit, not a router
- ~290 MB on disk (1-bit quantized), working WebGPU demo at [webml-community/bonsai-webgpu](https://huggingface.co/spaces/webml-community/bonsai-webgpu)
- 6–12 tok/s on integrated GPU
- Quality: simple Q&A / rephrase. **Not viable as a ReAct/tool-call router.** Good as an offline-fallback tier.

`deepgrove/Bonsai` (the original 0.5B ternary base) is a research artifact — base model only, no instruct. Skip.

## Browser runtimes surveyed

| Runtime | Best for | Strengths | Weaknesses |
|---|---|---|---|
| **WebLLM (MLC)** | LLM chat | Fastest WebGPU LLM (71–80% of native); OpenAI-compat API | Custom model format; per-arch recompile |
| **MediaPipe LiteRT-LM** | Gemma specifically | Google-authored, KV-cache tuned for web, Web Worker ergonomics | Google-model-centric |
| **transformers.js v3** | Audio/vision/multi-modal, STT, TTS | 100× over WASM on WebGPU; huge ONNX zoo | Slower LLM decode than WebLLM |
| **ONNX Runtime Web** | Custom pipelines | Max control, smallest runtime | Ergonomics poor vs transformers.js |

WebGPU shipped by default across Chrome/Firefox/Edge/Safari on 2025-11-25; coverage ~82.7% as of the 2026 inference analyses.

## STT / TTS in the browser

- **Moonshine Web** — ~60 MB, streaming-first, ~75 ms latency claim. Best for weak hardware.
- **Whisper large-v3-turbo via transformers.js** — ~800 MB; q8 decoder has a [known WebGPU bug](https://github.com/huggingface/transformers.js/issues/1317), fall back to q4 or WASM.
- **Kokoro.js** (`kokoro-js`, 82 M params, ~150 MB) — recommended default for TTS; matches server-side Kokoro voices.
- **Piper WASM** — WASM-only fallback, 904 voices.
- **`@ricky0123/vad`** — Silero VAD in the browser via onnxruntime-web + AudioWorklet. Same model pipecat uses server-side.
- **Fish S2 in browser — not viable.** 4.4B params, 22GB VRAM server-side with `--half --compile`; no browser port exists.

## Pipecat pipeline reality

`@pipecat-ai/client-js` is a **client transport SDK**, not a client-side pipeline engine. The processor primitives (`Pipeline([...])`, `FrameProcessor`, aggregators, observers, `BackchannelController`, `DeliveryController`, `BargeInGate`, `MicroAckInjector`, `EchoGuard*`, `ProsodyTagStripper`) live in the Python `pipecat-ai` package. **No pipecat-js exists.**

Implication: "full browser" requires rewriting those primitives in TypeScript. That's the moat. **Don't do it** for a marginal privacy/cost gain.

## Recommended architecture (when revisited)

**Option B — hybrid with client LLM** in three phases:

1. **Spike** (1–2w): standalone React route `/lab/browser-llm` running Gemma 4 E2B via WebLLM. Text-in/text-out harness against existing skill prompts. Measure tok/s + TTFT on target devices.
   - **Kill gate**: <15 tok/s on M3 or TTFT >1 s → abort.
2. **Relay** (3–4w): FastAPI WebSocket shim (`/api/local-llm/relay`) exposing an OpenAI-compatible API. Server's `OpenAILLMService` posts to the shim; shim forwards to the connected browser client over the existing RTVI data channel. New `plugins/local-llm/` plugin runs WebLLM, streams deltas back. Feature-flagged.
   - **De-risk**: inline pre-tool preambles depend on token-streaming fidelity through the relay. Prototype that first.
3. **STT/TTS swap** (2–3w, optional): Moonshine Web + Kokoro.js + `@ricky0123/vad`. Keep Fish + Whisper if voice-clone is the differentiator.

## Risks to de-risk early

- WebRTC data-channel bandwidth for LLM streaming (likely fine; RTVI already carries tens of KB/s).
- Mobile thermal throttling — plan a "server reclaim" hand-off when a hot client falls back.
- Gemma 4 E2B tool-calling quality vs Qwen-35B-A3B — run existing tool-schema fixtures in the spike.
- Voice-clone skills (Fish reference id) pin TTS server-side — auto-disable local-LLM mode when active skill uses a custom Fish reference.
- `role: developer` quirk — vLLM rejects; we set `llm.supports_developer_role = False`. Validate WebLLM's OpenAI shim accepts both.

## Open questions for when this restarts

1. Primary model: Gemma 4 E2B (recommended) vs Bonsai 1.7B?
2. Router quality floor — willing to accept slightly worse ReAct than Qwen-35B?
3. WebGPU-unavailable fallback — refuse / server-fall-through / WASM at 10×?
4. Desktop-first or mobile-first?
5. Privacy framing — is "conversations never leave your device" load-bearing marketing? (If yes, Option B isn't enough.)
6. Voice-clone deprecation appetite — Fish pinning keeps TTS server-side forever.

## Sources (all verified 2026-04-22)

- [google/gemma-4-E2B-it](https://huggingface.co/google/gemma-4-E2B-it)
- [prism-ml/Bonsai-1.7B-gguf](https://huggingface.co/prism-ml/Bonsai-1.7B-gguf)
- [Bonsai 1-bit WebGPU Space](https://huggingface.co/spaces/webml-community/bonsai-webgpu)
- [Transformers.js v3 — WebGPU](https://huggingface.co/blog/transformersjs-v3)
- [WebLLM (MLC)](https://github.com/mlc-ai/web-llm)
- [MediaPipe LLM Inference — Web](https://developers.google.com/mediapipe/solutions/genai/llm_inference/web_js)
- [Moonshine Web example](https://github.com/huggingface/transformers.js-examples/tree/main/moonshine-web)
- [kokoro-js](https://www.npmjs.com/package/kokoro-js)
- [@ricky0123/vad](https://docs.vad.ricky0123.com/user-guide/browser/)
- [pipecat-client-web](https://github.com/pipecat-ai/pipecat-client-web)
