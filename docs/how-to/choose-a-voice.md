# Choose a voice (text-to-speech)

Text-to-speech (TTS) is the orb's *voice*. Pick the backend and a specific voice
in **Settings → Voice → Text to speech**. For every key, see the
[Voice reference](/reference/voice#text-to-speech-tts).

## Pick a backend

| If you want… | Use | Voice |
| --- | --- | --- |
| **Local, private, zero setup** | **Kokoro** | The default. CPU, fully on-device. Choose from a fixed catalogue (e.g. `af_heart`). |
| **A hosted voice** | **OpenAI** | An OpenAI-compatible `/v1/audio/speech` endpoint; type any voice id it supports. |
| **protoLabs-hosted** | **protoLabs (Fish)** | `protolabs/fish`, same key as the LLM. |
| **Cloneable voices (advanced)** | **Fish** | An opt-in local sidecar; needs a GPU. |

## Pick the voice

- **Kokoro** shows a **dropdown** of its built-in voices — the catalogue is
  fixed, so just choose one. The panel marks which voices are **cached** (those
  start instantly).
- **OpenAI / hosted** backends let you **type** any voice id the endpoint
  exposes.

## Apply it

Select the backend and voice; changes take effect on the next reply. Switching
back to **Kokoro** (local, no network) is the quickest way to rule out a TTS
problem if the orb goes silent.

## See also

- [Choose a speech-to-text backend](/how-to/choose-speech-to-text)
- [Voice reference](/reference/voice)
