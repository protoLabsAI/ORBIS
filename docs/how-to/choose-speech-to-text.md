# Choose a speech-to-text backend

Speech-to-text (STT) is how ORBIS turns your voice into text. Pick the backend
that fits your priorities in **Settings → Voice → Speech to text**. For the full
list of keys, see the [Voice reference](/reference/voice#speech-to-text-stt).

## Which one?

| If you want… | Use | Notes |
| --- | --- | --- |
| **Private, offline, zero setup** | **Local (Whisper)** | The default. Runs on-device (Apple-Silicon MPS). Transcribes after you stop talking. |
| **Faster + fewer false transcripts** | **Parakeet** | Best local quality/speed, far fewer silence-hallucinations. Needs the `[parakeet]` extra and a ~600 MB model on first use. |
| **A hosted transcriber** | **OpenAI** | An OpenAI-compatible Whisper endpoint; needs a key. |
| **protoLabs-hosted** | **protoLabs** | faster-whisper on the gateway, same key as the LLM. |

When in doubt, the **Local** default is the right call — it's private and needs
nothing extra. Move to **Parakeet** if you find Whisper slow or it transcribes
silence into stray words.

## Switch backends

1. Open **Settings → Voice → Speech to text**.
2. Pick the **backend**.
3. For a hosted backend, set the **URL / model / key**.
4. **Parakeet** needs a one-time install of the `[parakeet]` extra and a
   **restart** to load its model. Hosted backends take effect on the next
   conversation.

## Confirm it

Speak and watch the transcript / the mic level meter. If audio reaches ORBIS
(the meter moves) but no text appears, see
[Voice isn't working](/how-to/voice-not-working#orbis-doesn-t-hear-me).

## See also

- [Choose a voice (TTS)](/how-to/choose-a-voice)
- [Voice reference](/reference/voice)
