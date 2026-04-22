# Switch TTS Backend

protoVoice ships two backends: **Fish Audio S2-Pro** (default) and **Kokoro 82M**. You pick one via `TTS_BACKEND`.

## Use Fish (default)

```bash
TTS_BACKEND=fish docker compose up -d
```

Fish runs in the `fish-speech` sidecar container. Requires:
- A second GPU (it's heavy; can't share with routing vLLM).
- `FISH_URL` reachable from the `protovoice` container (default `http://fish-speech:8092`).

See [TTS Backends → Fish](/reference/tts-backends#fish-audio-s2-pro) for tuning.

## Use Kokoro

```bash
TTS_BACKEND=kokoro docker compose up -d protovoice
```

Kokoro runs in-process inside the `protovoice` container. Benefits:
- No sidecar, no second GPU.
- ~50 ms/chunk latency — faster than Fish but less natural.

Pick a voice via `KOKORO_VOICE`:

```bash
TTS_BACKEND=kokoro KOKORO_VOICE=am_michael docker compose up -d protovoice
```

54 voices are available — see the [Kokoro model card](https://huggingface.co/hexgrad/Kokoro-82M) for the full list.

## Swap at runtime

Not supported yet. Restarting the container with a different `TTS_BACKEND` takes ~5 s (Whisper + vLLM + Kokoro warm) or ~2 min (Fish cold compile).

Planned for a later milestone: pick a backend per-skill (e.g. Fish for conversational, Kokoro for fast-turn acknowledgements).
