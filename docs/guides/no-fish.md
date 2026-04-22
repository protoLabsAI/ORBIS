# Run Without the Fish Sidecar

For single-GPU hosts, or when you don't need voice cloning, skip the Fish Audio container entirely.

## One-service start

```bash
TTS_BACKEND=kokoro docker compose up -d protovoice
```

- `protovoice` alone. No `fish-speech`.
- Kokoro runs in-process inside the `protovoice` container (~2 GB VRAM, no GPU sharing penalty).
- `FISH_URL` is ignored.

## VRAM budget (single GPU)

| Component | VRAM |
|:---|:---:|
| Whisper large-v3-turbo | ~6 GB |
| Routing vLLM (Qwen 4B) | ~15 GB |
| Kokoro 82M | ~2 GB |
| **Total** | **~23 GB** |

Fits on a 24 GB+ card (RTX 3090, 4090, PRO 6000, A100, H100, etc.).

## Picking a Kokoro voice

```bash
TTS_BACKEND=kokoro KOKORO_VOICE=am_michael docker compose up -d protovoice
```

Common voices:

- `af_heart` *(default, American female)*
- `af_bella`, `af_nicole`, `af_sarah`
- `am_adam`, `am_michael`
- `bf_emma`, `bf_isabella` *(British female)*
- `bm_george`, `bm_lewis` *(British male)*

Full list on the [Kokoro HF card](https://huggingface.co/hexgrad/Kokoro-82M).

## Trade-offs

| | Fish S2-Pro | Kokoro 82M |
|:---|:---|:---|
| First-token latency | ~400-800 ms steady-state | ~50 ms |
| Voice cloning | ✓ 10-30 s reference | ✗ fixed voices |
| Prosody control tags | ✓ 15 k+ tags | ✗ |
| Naturalness | Excellent | Good |
| Cold-start compile | ~2 min | ~2 s |
| GPU footprint | +22 GB on a separate GPU | +2 GB in-process |
| Extra container | Yes | No |

If you care about turn latency and don't need cloning, Kokoro is often the better choice.
