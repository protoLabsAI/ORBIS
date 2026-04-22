# Clone a Voice

Fish Audio S2-Pro can imitate a voice from a 10-30 s reference clip. protoVoice has two cloning paths: a **one-click UI** (auto-transcribes via Whisper + creates a skill automatically) and a **raw HTTP API** for scripting.

::: tip
Cloning requires `TTS_BACKEND=fish`. Kokoro ships fixed preset voices only.
:::

## One-click in the UI

On the main page, expand **"+ Clone a new voice"**:

1. **Audio sample** — pick a WAV/MP3 file (10-30 seconds of one speaker, clean audio).
2. **Slug** — lowercase-hyphen identifier (`alex`, `kai-pm`, `narrator-female`). Becomes the skill id AND the Fish reference id.
3. **Display name** — optional, defaults to `Alex` → "Alex".
4. **Transcript** — **leave empty to auto-transcribe via Whisper**. Provide explicitly if you want more control (e.g. including a proper-noun spelling Whisper tends to mis-hear).
5. **Description** — optional text that shows as the dropdown's hover title.

Click **Clone voice**. Behind the scenes:

- The clip gets POSTed to `POST /api/voice/clone` (multipart).
- If no transcript: the audio is piped through the same Whisper pipeline the STT side uses. Auto-transcribed text is returned so you can see what it heard.
- The reference is saved on the Fish server.
- A new skill YAML is written to `config/skills/<slug>.yaml` pointing at the fresh reference, reusing `SOUL.md` as the persona.
- The skill list refreshes in-place — the dropdown now shows the new voice.

No restart needed. Switch to the new skill and Start.

## Raw API

For scripting or bulk cloning, bypass the UI and go straight to Fish.

### Save a reference

```bash
curl -X POST http://localhost:8092/v1/references/add \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d @- <<EOF
{
  "id": "alex",
  "audio": "$(base64 < /path/to/alex_sample.wav)",
  "text": "This is the exact transcript of the audio clip. Accuracy matters for quality."
}
EOF
```

- **`id`** — your chosen name. Match the regex `^[a-zA-Z0-9\-_ ]+$`.
- **`audio`** — base64-encoded WAV (or MP3). 10-30 seconds, mono, clean.
- **`text`** — exact transcript of the audio. This is not optional; Fish aligns the clip to the transcript.

## Use it for this session

```bash
FISH_REFERENCE_ID=alex docker compose up -d protovoice
```

Or hit our Python API directly (from inside the container):

```python
from voice.tts.fish import FishAudioTTS
tts = FishAudioTTS(reference_id="alex")
```

## List saved references

```bash
curl -H "Accept: application/json" http://localhost:8092/v1/references/list
# → {"success": true, "reference_ids": ["alex","voice_01", ...]}
```

## Delete a reference

```bash
curl -X DELETE http://localhost:8092/v1/references/delete \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d '{"reference_id": "alex"}'
```

::: warning JSON vs MsgPack
Fish's API content-negotiates on `Accept`. Without an explicit `Accept: application/json` header it returns MsgPack — which will break naive clients. protoVoice's Python client sets the header for you; external callers must do so manually.
:::

## Inline one-shot cloning

If you don't want to persist a voice, pass `references=[{audio, text}]` inline per TTS call. The protoVoice client doesn't wrap this yet — open an issue if you need it.

## Using control tags

Fish S2-Pro supports inline prosody tags in the text you send. Drop them into your LLM's system prompt or the text you synthesize:

- `[pause]` / `[inhale]` / `[sighs]` — timing
- `[whisper]` / `[excited]` / `[angry]` / `[sad]` — emotion
- `[professional broadcast tone]` / `[singing]` — style
- `[pitch up]` / `[pitch down]` — pitch

About 15,000 free-form tags are supported per the Fish S2 paper. Experiment.
