# Running with Docker Compose

protoVoice ships two services:

- **`protovoice`** — WebRTC + Whisper + routing vLLM + Kokoro fallback (GPU 0)
- **`fish-speech`** — Fish Audio S2-Pro TTS sidecar (GPU 1)

Default config assumes two GPUs on one host. Adjust via env if you have fewer.

## Basic start

```bash
docker compose up -d
docker compose logs -f protovoice
```

Health check: `curl http://localhost:7867/healthz`.

## Common overrides

All env vars are documented in the [Environment Variables](/reference/environment-variables) reference. The ones you'll reach for most:

```bash
# Pin to specific GPUs
PROTOVOICE_GPU=0 FISH_GPU=1 docker compose up -d

# Use only Kokoro (no Fish sidecar)
TTS_BACKEND=kokoro docker compose up -d protovoice

# Point at an external LLM instead of starting our own vllm
START_VLLM=0 LLM_URL=http://10.0.0.10:8000/v1 docker compose up -d
```

## Volume mounts

- `HF_HOME` → `/models` — HuggingFace cache. Default: `/mnt/models/huggingface`.
- `FISH_REFERENCES_DIR` → `/app/references` — persists saved voice references across restarts.

## GPU allocation

| Service | Default GPU | VRAM |
|---------|:---:|:---:|
| protovoice (Whisper + routing vLLM + Kokoro) | 0 | ~23 GB |
| fish-speech (S2-Pro with `--half --compile`) | 1 | ~22 GB |

On a single-GPU host, set `TTS_BACKEND=kokoro` to skip Fish entirely and keep everything on GPU 0.

## Cold start

Fish Audio's first call triggers a ~2-minute `torch.compile` codegen. protoVoice's `prewarm()` on startup sends a single silent utterance to absorb this — you should never see that hit in a real turn.

Whisper takes ~2 s to load + warm.

vLLM takes 30-60 s to load + warm depending on model size.

## Stopping cleanly

```bash
docker compose down
```

`down` tears down both containers. The Fish `torch.compile` cache lives in-container and will re-run on next start — mount `/tmp/torchinductor_*` to persist it if you want a faster restart, though this is currently undocumented upstream.
