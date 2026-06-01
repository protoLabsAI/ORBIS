# Voice

Everything that controls how ORBIS **hears** you and how it **speaks** back.
These live in **Settings → Voice** (mic, speech-to-text, text-to-speech), and
the same options can be set in [`orbis.yaml`](./config) for headless setups.

For the *why* behind the pipeline, see [The voice loop](/explanation/the-voice-loop).

## Microphone

The **Voice → Microphone** panel manages audio input.

| Control | What it does |
| --- | --- |
| **Permission** | macOS mic access. If it's not granted, ORBIS shows a button to request it (or to open System Settings if it was previously denied). |
| **Input device** | Choose which microphone to use. Shown only when the audio mode uses a selectable device; in voice-processing mode ORBIS follows the **macOS system input** instead. |
| **Level meter** | A live meter — speak and confirm the bars move. Your single best "is the mic working?" check. |

::: tip M-series internal mic
The built-in mic on M-series Macs is quiet without hardware AGC. ORBIS applies
input gain so normal speech registers; if you use an external mic and it's
clipping, prefer a quieter input or external device.
:::

## Speech-to-text (STT)

How spoken audio becomes text. Set the **backend**, plus per-backend options.
Config lives under `stt:` in `orbis.yaml`; the `STT_BACKEND` env var overrides
`stt.backend`.

| Backend | What it is | When to use |
| --- | --- | --- |
| **`local`** (Whisper) | In-process Whisper on Apple-Silicon (MPS). Segmented per turn — it transcribes after you stop talking, not while. Default. | The private, offline default. |
| **`parakeet`** | NVIDIA Parakeet-TDT via Apple MLX (opt-in `[parakeet]` extra). Faster and far fewer silence-hallucinations than Whisper. ~600 MB model on first use; restart to apply. | Best local quality/speed if you can install the extra. |
| **`openai`** | An OpenAI-compatible Whisper endpoint. | You want a hosted transcriber. |
| **protoLabs** | faster-whisper on the protoLabs gateway (same key as the LLM). | protoLabs-hosted setups. |

**Keys** (`stt:`):

| Key | Backend | Meaning |
| --- | --- | --- |
| `backend` | all | `local` · `parakeet` · `openai` |
| `whisper_model` | `local` | Whisper model id (takes effect on restart). |
| `model` | `openai` | Remote model id (e.g. `whisper-1`). |
| `url` / `api_key` | `openai` | Endpoint + key (take effect on the next session). |

## Text-to-speech (TTS)

How ORBIS's replies become the orb's voice. Set the **backend** and a **voice**.
Config lives under `voice:` in `orbis.yaml`; `TTS_BACKEND` and `KOKORO_VOICE`
override `voice.tts_backend` and `voice.voice`.

| Backend | What it is | Voice selection |
| --- | --- | --- |
| **`kokoro`** | The default. Runs on CPU, fully local. | A fixed catalogue (e.g. `af_heart`) — pick from the dropdown. |
| **`openai`** | OpenAI-compatible `/v1/audio/speech`. | Type any voice id the endpoint supports. |
| **protoLabs (Fish)** | Fish S2-Pro via the protoLabs gateway (same key as the LLM). | `protolabs/fish`. |
| **`fish`** | Opt-in local sidecar — cloneable voices, needs a GPU. | Custom. |

**Keys** (`voice:`):

| Key | Meaning |
| --- | --- |
| `tts_backend` | `kokoro` · `openai` · `fish` · gateway. |
| `voice` | Voice id (Kokoro voice, OpenAI voice, ElevenLabs `voice_id`, …). |
| `tts_url` / `tts_model` / `tts_api_key` | For OpenAI-compatible endpoints. |

::: tip Voice cache
Kokoro voices are cached after first use; the TTS panel shows which voices are
already cached so you know which will start instantly.
:::

## See also

- [The voice loop](/explanation/the-voice-loop) — how a turn flows end to end.
- [Voice isn't working](/how-to/voice-not-working) — troubleshooting.
- [Config reference](./config) — the full `orbis.yaml`.
