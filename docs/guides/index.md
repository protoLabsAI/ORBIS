# How-To Guides

Task-oriented. Each page assumes you already know what you want to do — if you're looking for orientation, start with [Tutorials](/tutorials/).

## TTS

- [Switch TTS Backend](./switch-tts-backend) — Fish ↔ Kokoro
- [Clone a Voice](./clone-a-voice) — upload a reference clip and speak in that voice
- [Run Without the Fish Sidecar](./no-fish) — Kokoro-only, single container

## LLM

- [Use an External LLM](./external-llm) — point at a gateway, a remote vLLM, or OpenAI

## All-API setup

- [Use LocalAI (all-API)](./use-localai) — swap STT + TTS + LLM to OpenAI-compat endpoints; run protoVoice on a CPU box

## Voice agent behaviour

- [Configure Verbosity](./verbosity) — tune filler chattiness (silent / brief / narrated / chatty)
- [Backchannels](./backchannels) — listener-acks ("mm-hmm") during long user turns
- [Delivery Policies](./delivery-policies) — `now` / `next_silence` / `when_asked` for async tool results
- [Personas & Skills](./personas-and-skills) — swap voice + system prompt per skill YAML
- [Users & API Keys](./users) — configure the auth roster (Infisical or `config/users.yaml`)
- [Audio Handling](./audio-handling) — echo guard, half-duplex, noise filter, smart-turn

## Extending the agent

- [Build a Tool](./build-tools) — sync vs async patterns, latency tiers, the result_callback gotcha

## Fleet integration

- [A2A Integration](./a2a-integration) — inbound JSON-RPC + callback webhook + outbound dispatch

## Ops

- [Benchmarking](./benchmarking) — measure LLM / TTS / STT / A2A latency with `scripts/bench.py`
