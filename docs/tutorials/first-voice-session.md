# First Voice Session

End-to-end: from zero to talking to protoVoice. About 5 minutes plus first-time model downloads.

## Prerequisites

- Linux host with an NVIDIA GPU (tested on RTX PRO 6000 Blackwell)
- Docker + `nvidia-container-toolkit`
- ~15 GB free disk for HuggingFace model cache
- For remote browsers: HTTPS is required for mic access. Easiest on a tailnet: `tailscale serve --bg 7867`.
- **Headphones recommended.** Speaker → mic feedback can cause the bot to interrupt itself or hear its own voice. Headphones eliminate the acoustic loop entirely. If you can't use them, see [Audio Handling](/guides/audio-handling) for the echo-guard / half-duplex / noise-filter / smart-turn options.

## 1. Clone and configure

```bash
git clone https://github.com/protoLabsAI/protoVoice.git
cd protoVoice
cp .env.example .env
# Edit .env to set any secrets + overrides you need. At minimum,
# AVA_API_KEY if you plan to use the ava delegate, or LITELLM_MASTER_KEY
# if you'll point at a LiteLLM gateway. The file is gitignored.
```

Defaults in the code are fine for a first run — you only need `.env` for secrets and non-default values.

## 2. Boot

```bash
docker compose up -d
```

First boot downloads Whisper large-v3-turbo (~2 GB) and Qwen (depends on the `LLM_MODEL` you set). Fish Audio checkpoints come from the sidecar image.

## 3. Open the browser

Go to `http://localhost:7867` (or your tailnet URL over HTTPS). You'll see a single **Start** button and a verbosity dropdown.

## 4. Talk

Click **Start** — the browser will request microphone access. Once the page shows `connected — speak`, go ahead and ask something.

Try these to exercise different pieces of the stack:

- **Direct chat** — "what's your favorite color?" (routes through the LLM only, no tool)
- **Research dispatch** — "what was the weather in Tokyo today?" (triggers the `deep_research` tool; you'll hear a filler phrase while it runs)
- **Verbosity** — flip the dropdown to `narrated` and ask a research question; you'll hear periodic progress phrases

## 5. Stop

Click **Stop** (or close the tab). The WebRTC connection tears down; the pipeline cancels.

## What actually happened

```
Browser mic
   │  (WebRTC)
   ▼
SmallWebRTCTransport  ◄── /api/offer POST + PATCH (trickle ICE)
   │
   ▼
Silero VAD → Whisper large-v3-turbo  (STT, GPU)
   │
   ▼
OpenAILLMService → vLLM (Qwen)  (LLM, GPU)
   │
   ▼
FishAudioTTS → Fish sidecar  (TTS, separate GPU)
   │
   ▼
SmallWebRTCTransport → Browser speaker
```

## Troubleshooting

- **Mic blocked** — browsers block `getUserMedia` on plain HTTP for non-localhost. Use `tailscale serve` for tailnet HTTPS, or a reverse proxy.
- **Connection dies after 7-10 seconds** — usually a WebRTC media path problem. Make sure both browser and server are on the same network (tailnet works) or set up TURN.
- **Silence after LLM finishes** — check that the LLM isn't stuck in reasoning mode emitting `reasoning_content` instead of `content`. The stack sets `enable_thinking=false` for Qwen; other models may need different flags.

## Next

- [Running with Docker Compose](./docker-compose) — more detail on GPU allocation and volume mounts
- [Switch TTS Backend](/guides/switch-tts-backend) — swap Fish ↔ Kokoro
