# protoVoice

Full-duplex voice agent. Speak, get spoken replies fast; the agent speaks *before* it's done thinking, can push async results back mid-conversation, and can delegate to other agents or bigger LLMs when it needs to.

```
browser mic → WebRTC → STT → router LLM → TTS → speaker
                              │
                              └─ calls a tool → speaks a preamble inline →
                                 (sync: web_search, calculator, datetime)
                                 (delegate: ava, opus, any OpenAI-compat)
                                 (async: slow_research — you keep talking;
                                  agent drops the answer in at next silence)
```

Built on [Pipecat](https://docs.pipecat.ai) 1.0.

## Quick start

```bash
git clone https://github.com/protoLabsAI/protoVoice.git
cd protoVoice
cp .env.example .env      # edit for any secrets (AVA_API_KEY, LITELLM_MASTER_KEY, etc.)
docker compose up -d
```

UI at `http://localhost:7866`. Browsers require HTTPS for mic access on non-localhost — use `tailscale serve 7866` (tailnet-only HTTPS) or a reverse proxy with TLS. Headphones recommended — see [audio handling](https://protolabsai.github.io/protoVoice/guides/audio-handling/) for speaker-echo mitigations.

## What you get

- **Pipecat pipeline** — WebRTC, VAD, streaming STT → LLM → TTS.
- **Adaptive barge-in** — VAD-fired interrupts go through a 350 ms grace window that rejects coughs / backchannels / brief noise; real interruptions still fire, just confirmed. Based on LiveKit production data: ~51 % of raw VAD-triggered barges are false positives.
- **Natural fillers layered** — inline LLM-emitted preamble ("hmm, let me check") before every tool call, two-tier "still working" cadence (~2 s / ~8 s) for slow tools, generative mid-user backchannels ("mm-hmm"), micro-ack injector ("mm") if the pipeline hasn't produced audio within 500 ms. Details in [natural-fillers](https://protolabsai.github.io/protoVoice/explanation/natural-fillers/).
- **Fish prosody pipeline** — Fish S2-Pro consumes `[softly]` / `[pause:300]` / `[hmm]` tags natively as prosody control; Kokoro / OpenAI strip via pipecat's `text_filters=`. Context stays clean via a tail-end `ProsodyTagStripper`. Research-backed: fillers alone read fake, fillers + ~300 ms pauses cross the uncanny valley (Sesame CSM).
- **Delegates** — unified `delegate_to(target, query)` tool. A2A delegates get SSE streaming via `message/stream` with progress narration back through the voice pipeline; OpenAI-compat endpoints use non-streaming chat completions. Configured in `config/delegates.yaml`; the LLM picks targets by their descriptions.
- **Delivery policies** — async-tool results route via `Priority` (critical / time_sensitive / active / passive) → `NOW` / `NEXT_SILENCE` / `WHEN_ASKED`. Bid-then-drain when ≥ 2 queued ("I've got updates from ava and slow_research — want them?"). Utility-gated drop past 3 items. Source attribution ("ava says — …").
- **A2A push-back** — spec-compliant `/a2a/push` webhook accepts callbacks from delegated agents; outbound `pushNotificationConfig` (env: `A2A_PUSH_URL` + `A2A_PUSH_TOKEN`) attached on each dispatch so remote agents can push progress / terminal state back even if the SSE stream drops. Priority-mapped by event type (`input-required` → interrupt, terminal → next-silence, mid-task status → wait-for-ask).
- **Reconnect replay** — if `slow_research` finishes after disconnect, or an A2A push arrives while you're offline, payloads stash under the skill slug and replay on the next connect via the bid-then-drain UX.
- **Context summarization** — pipecat's built-in `LLMContextSummarizer` auto-compresses once token (default 8 k) or message (20) thresholds hit.
- **Session-open memory callback** — rolling summary persists across WebRTC disconnects; next session may open with "hey, last time we were working through X…" if it fits naturally. Sesame CSM pattern.
- **Prompt-driven agentic behaviour** — ≥ 3-step requests get a spoken plan preamble (CHI 2025); user pushback triggers acknowledge → reframe → offer repair (ACL 2025); tool results target 18-25 words + follow-up offer, not prose dumps (CHI 2025).
- **Voice cloning in-browser** — upload a 10-30 s clip, auto-transcribed by Whisper, saved on Fish Audio, registered as a new skill. Instant new voice, no restart.
- **Personas & skills** — `config/SOUL.md` + `config/skills/*.yaml` for swappable personas with per-skill TTS voice, LLM tuning, and tool restrictions.
- **A2A inbound** — `/a2a` JSON-RPC supports both `message/send` (sync) and `message/stream` (SSE) per spec. Inbound requests run a bounded ReAct loop so external agents can use our tool registry (`calculator`, `get_datetime`, `web_search`, `delegate_to`). `/a2a/push` accepts spec-conformant callback receipts.
- **Langfuse tracing** — every user turn is a trace spanning STT → router LLM → tools → TTS → delivery, with filler / backchannel / progress generations auto-captured via `langfuse.openai`. Cross-fleet propagation via `Langfuse-Trace-Id` / `Langfuse-Session-Id` headers so traces stitch across protoLabs agents. Fail-open when Langfuse env is unset. See [tracing](https://protolabsai.github.io/protoVoice/reference/tracing/) and the [cross-fleet contract](https://protolabsai.github.io/protoVoice/reference/tracing-contract/).
- **RTVI** — server-side pipecat RTVI processor + observer emit structured state events (`bot-llm-started/stopped`, `bot-tts-started/stopped`, `user-transcription`, `function-call-*`) over the WebRTC data channel. Client consumption lands with the React frontend migration.
- **Pluggable backends** — STT and TTS both swappable via env (`STT_BACKEND=local|openai`, `TTS_BACKEND=fish|kokoro|openai`). Run fully API-backed via LocalAI, LiteLLM, OpenAI — no GPU on the protovoice container needed. See the [use-localai guide](https://protolabsai.github.io/protoVoice/guides/use-localai/).

## Stack defaults

| Layer | Default |
|:---|:---|
| STT | HF Whisper large-v3-turbo (GPU, in-process) |
| Router LLM | local vLLM (Qwen3.5-4B — any OpenAI-compat works) |
| TTS | Fish Audio S2-Pro sidecar (`--half --compile`; ~400-800 ms TTFA); Kokoro 82M (~50 ms) and OpenAI-compat endpoints also selectable |
| Transport | Pipecat `SmallWebRTCTransport` |
| Delegates | ava (at `https://ava.proto-labs.ai/v1`) — add more in `config/delegates.yaml` |

## Configuration

Everything's env-driven. `cp .env.example .env` and edit — all 44+ env vars are documented there and in [Environment Variables](https://protolabsai.github.io/protoVoice/reference/environment-variables/). Defaults work for a single-GPU homelab install.

For production boxes, inject secrets via Infisical / Vault / k8s Secret + envFrom — the app reads `os.environ` and doesn't care where values came from.

## Docs

Full site: **https://protolabsai.github.io/protoVoice/** — Diátaxis-organized (tutorials / guides / reference / explanation).

Common starting points:

- [First Voice Session](https://protolabsai.github.io/protoVoice/tutorials/first-voice-session/) — clone, configure, talk
- [Build a Tool](https://protolabsai.github.io/protoVoice/guides/build-tools/) — sync vs async, the `result_callback` gotcha
- [Delegates](https://protolabsai.github.io/protoVoice/reference/delegates/) — add an A2A agent or OpenAI endpoint
- [Audio Handling](https://protolabsai.github.io/protoVoice/guides/audio-handling/) — echo guard, half-duplex, noise filter, smart-turn
- [Two-Model Split](https://protolabsai.github.io/protoVoice/explanation/two-model-split/) — router LLM vs. delegated thinker pattern

## Release pipeline

- Push to `main` → GHCR `:latest` + `sha-<short>` images via `.github/workflows/docker-publish.yml`
- `vX.Y.Z` tag → stable semver images + GitHub release via `.github/workflows/release.yml`
- Manual `workflow_dispatch` on `prepare-release.yml` → bumps version, opens + auto-merges the release PR, tags

## License

MIT
