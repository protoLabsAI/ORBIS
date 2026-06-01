# The voice loop

A conversation with ORBIS is a loop: it **hears** you, **understands** what you
said, **thinks** about it, and **speaks** back — then waits for your next turn.
This page explains the shape of that loop and the ideas that make it feel like
talking to someone rather than typing at a prompt.

For the knobs, see the [Voice reference](/reference/voice); for fixes, see
[Voice isn't working](/how-to/voice-not-working).

## Hearing

1. **Capture.** The native audio engine reads your microphone with low latency.
   ORBIS is Apple-Silicon-only partly *because* it uses native audio instead of
   the browser — it's what makes the round-trip fast and reliable.

2. **Echo guard.** The orb is also playing audio (its own voice). Echo
   cancellation removes that from the input so ORBIS doesn't hear — and reply
   to — itself.

3. **Turn-taking (VAD).** Voice-activity detection watches for you to *start*
   and *stop* speaking. ORBIS doesn't transcribe a fixed window; it waits for a
   natural pause, then treats what you said as one turn. That's why a brief
   pause mid-sentence is fine, but a long silence ends your turn.

4. **Transcription (STT).** The captured turn becomes text. With the local
   Whisper/Parakeet backends this is **segmented** — it happens *after* you stop
   talking. (Streaming backends, which transcribe as you speak, are a future
   option.) See [STT backends](/reference/voice#speech-to-text-stt).

## Understanding & thinking

The text goes to the language model, which acts as a **router** first. For each
turn it decides among:

- **Just answer** — a normal spoken reply.
- **Use a tool** — e.g. set a reminder, list reminders.
- **Delegate** — hand a heavier task to one of your configured agents and
  narrate the result when it returns.

While it's working it may emit short **acknowledgements** ("on it…") so the orb
isn't silent during a slow tool call or delegation. These are deliberately
brief and don't become part of the conversation.

## Speaking

The reply text is synthesized by the TTS backend into the orb's voice and
played back through the native engine. The orb's visual state tracks the loop —
**idle → listening → thinking → speaking** — so you can *see* where it is.

## Barge-in

You don't have to wait for ORBIS to finish. If you start talking while the orb
is speaking, the **barge-in** gate stops playback and starts listening — the
same way a person stops mid-sentence when you cut in. (Some barge-in features
are conservative by default to avoid the orb interrupting itself on its own
audio tail.)

## Why it's local

Everything above — capture, echo cancellation, VAD, and the default STT/TTS —
runs **on your Mac**. Audio doesn't leave the machine unless you point STT or
TTS at a hosted endpoint. That's the single-owner, on-your-hardware posture
ORBIS is built around.

## See also

- [Voice reference](/reference/voice)
- [How ORBIS works](/explanation/how-orbis-works)
- [Voice isn't working](/how-to/voice-not-working)
