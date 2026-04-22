# TTS Backends

protoVoice has three pluggable backends — `fish` (sidecar w/ cloning), `kokoro` (in-process, low-latency), and `openai` (any compat endpoint: LocalAI / OpenRouter / OpenAI itself). They all subclass the same Pipecat `TTSService` base — the pipeline is identical; only the backend swaps.

## Fish Audio S2-Pro

Our default. 4.4 B parameters, 44.1 kHz output, 80+ languages, voice cloning, prosody control tags.

### Deployment

Runs as the `fish-speech` sidecar container. Build context is `../fish-speech` (a checkout with `.venv` + `checkpoints/s2-pro/`).

### Launch flags (mandatory for Blackwell)

```bash
.venv/bin/python -m tools.api_server \
  --listen 0.0.0.0:8092 \
  --llama-checkpoint-path checkpoints/s2-pro \
  --decoder-checkpoint-path checkpoints/s2-pro/codec.pth \
  --decoder-config-name modded_dac_vq \
  --half --compile
```

`--half` and `--compile` take RTF from ~3.0 to **~0.40** on RTX PRO 6000 Blackwell. First call after start triggers a ~2-minute `torch.compile` codegen; subsequent calls are steady-state fast.

### Streaming quirks

- `POST /v1/tts` with `streaming=true, format=wav` returns **raw int16 LE PCM** — not actual WAV with a header, despite the `format` field. Our client treats the stream as bare PCM at 44.1 kHz mono.
- Chunk sizes land on arbitrary byte boundaries. Our client carries a 1-byte odd-chunk buffer so `TTSAudioRawFrame.audio` is always int16-aligned (soxr rejects otherwise).
- `POST /v1/references/{add,list,delete}` return MsgPack by default. Send `Accept: application/json` to get JSON.

### Voice cloning

See [Clone a Voice](/guides/clone-a-voice).

### Env

| Variable | Default | Purpose |
|:---|:---|:---|
| `FISH_URL` | `http://fish-speech:8092` | Sidecar endpoint |
| `FISH_REFERENCE_ID` | — | Saved voice reference to use |
| `FISH_SAMPLE_RATE` | `44100` | Native output SR |
| `FISH_TIMEOUT` | `180` | Per-call timeout (covers cold compile) |

## Kokoro 82M

Local, low-latency fallback. 82 M parameters, 24 kHz output, 54 preset voices, no cloning.

### Deployment

Runs in-process inside the `protovoice` container via the `kokoro` PyPI package. Uses the PyTorch runtime — not the `kokoro-onnx` one Pipecat's bundled `[kokoro]` extra uses.

### Latency

~50 ms/chunk steady-state. Cold-start ~2 s (loads fast).

### Env

| Variable | Default | Purpose |
|:---|:---|:---|
| `KOKORO_VOICE` | `af_heart` | Preset voice id |
| `KOKORO_LANG` | `a` | Language — `a` American, `b` British, `j` Japanese, etc. |

### Available voices

See the [Kokoro HF card](https://huggingface.co/hexgrad/Kokoro-82M). Quick reference:

- **American English**: `af_heart af_bella af_nicole af_sarah af_alloy af_aoede af_jessica af_kore af_nova af_river af_sky am_adam am_michael am_echo am_eric am_liam am_onyx`
- **British English**: `bf_emma bf_isabella bf_alice bf_lily bm_george bm_lewis bm_daniel bm_fable`

Note that prefixes mean: `af` = American female, `am` = American male, `bf` / `bm` = British female / male.

## OpenAI-compatible

Hits any `POST /v1/audio/speech` endpoint — OpenAI, LocalAI, OpenRouter, vllm-omni, etc.

### Env

| Variable | Default | Purpose |
|:---|:---|:---|
| `TTS_OPENAI_URL` | `https://api.openai.com/v1` | Base URL |
| `TTS_OPENAI_MODEL` | `tts-1` | Model id |
| `TTS_OPENAI_VOICE` | `alloy` | Voice id |
| `TTS_OPENAI_API_KEY` | `not-needed` | Bearer for auth |
| `TTS_OPENAI_SAMPLE_RATE` | `24000` | Output SR claim |

### Latency

Network-dependent. Local LAN endpoint: ~200-400 ms TTFA. Cloud (OpenAI): ~400-800 ms TTFA for `tts-1`, more for `tts-1-hd`.

## Choosing between them

| | Fish | Kokoro | OpenAI-compat |
|:---|:---|:---|:---|
| Latency | 400-800 ms TTFA | ~50 ms/chunk | network-dependent |
| Quality | Excellent, natural | Good, slightly robotic | depends on model behind |
| Cloning | ✓ | ✗ | ✗ |
| Prosody tags | ✓ (15 k+) | ✗ | depends on model |
| VRAM (this host) | ~22 GB on a separate GPU | ~2 GB in-process | 0 (remote) |
| Cold compile | ~2 min | ~2 s | n/a |
| Extra container | Yes (sidecar) | No | No |
