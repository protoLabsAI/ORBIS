# Audio Handling — Echo, Feedback, Turn Detection

Voice agents have two recurring audio problems: **echo / feedback** (the bot's voice bleeds into the mic and gets re-transcribed or interrupts the bot itself) and **premature turn detection** (the agent jumps in when the user pauses to think).

protoVoice has four layers of mitigation, all opt-in via env. They compose — start with the cheapest and add more if you still hear issues.

## Layer 0 — wear headphones

::: tip Single biggest fix.
Headphones eliminate the acoustic path back to the mic entirely. Echo / feedback simply does not happen. If you can wear them, the rest of this page is for hardware you can't control (kiosks, speakerphones, demos).
:::

## Layer 1 — browser AEC (always on)

`getUserMedia({audio: true})` defaults to the browser's WebRTC media constraints: echo cancellation, noise suppression, auto-gain control. Chrome / Firefox / Safari all do reasonable AEC for the WebRTC track. This is automatic — protoVoice doesn't disable it.

It's enough for headphones + most laptop-speaker setups at moderate volume. It struggles with: Bluetooth speakers, loud volume, untreated rooms, kiosk/speakerphone setups.

## Layer 2 — echo-guard window (default ON)

After the bot stops speaking, drop incoming mic audio for `ECHO_GUARD_MS` milliseconds (default 300). Catches the echo tail that bleeds back through speakers + mic that browser AEC missed. Cheap, safe, and on by default.

```bash
ECHO_GUARD_MS=300   # default; raise to 500 if you still hear self-interruption
```

Set to `0` to disable.

How it works: an `EchoGuardObserver` watches `BotStartedSpeakingFrame` / `BotStoppedSpeakingFrame` to update shared state. An `EchoGuardSuppressor` `FrameProcessor` placed immediately after `transport.input()` drops `InputAudioRawFrame`s while the guard window is active — VAD downstream never sees the suppressed audio, so no false `UserStartedSpeakingFrame` from echo bleed.

## Layer 3 — half-duplex mode (off by default)

While the bot is speaking, mute the mic stream entirely.

```bash
HALF_DUPLEX=1
```

Trade-off: **loses real-time barge-in.** The user has to wait for the bot to finish before being heard. In return: zero echo loops, even on the loudest speakerphone setup.

Recommended for: kiosks, demos, conference-call playback, anywhere you can't trust browser AEC.

## Layer 4 — RNNoise filter (opt-in)

A neural noise-suppression filter applied to the mic stream BEFORE STT. Helps with non-speech noise (HVAC, typing, traffic) and modestly with echo.

```bash
NOISE_FILTER=rnnoise
```

Requires the optional pip extra:

```bash
pip install -e .[rnnoise]
# or: pip install pipecat-ai[rnnoise]
```

Wired as `TransportParams.audio_in_filter=RNNoiseFilter()` — if you set the env without the extra installed, you'll see an error in the log and protoVoice falls back to no filter.

## Layer 5 — smart-turn analyzer (opt-in)

Replaces naive VAD endpointing ("silence > 600 ms = user done") with a learned model that decides whether the silence is a real turn-end vs a mid-thought pause.

```bash
SMART_TURN=local
```

Requires the optional pip extra (downloads a ~50 MB Wav2Vec2-based ONNX model on first use):

```bash
pip install -e .[smart-turn]
# or: pip install pipecat-ai[local-smart-turn]
```

What this fixes:

- **Premature interrupt**: user says "I want to... uh..." (pauses to think) → naive VAD fires after the pause → agent jumps in → user has to re-explain. Smart-turn rejects the false end-of-turn, the agent waits.
- **Echo / noise turn-discrimination**: bleed-back from speakers creates audio that doesn't look like a clean turn-end. Smart-turn rejects it more reliably than naive VAD.
- **Faster real end-of-turn**: when the user clearly finishes ("...thanks!"), smart-turn confirms quickly instead of waiting the full silence threshold.

Wired as `LLMUserAggregatorParams(turn_analyzer=LocalSmartTurnAnalyzerV3())` alongside the existing `SileroVADAnalyzer`.

## Stacking strategy

| Setup | Recommended stack |
|:---|:---|
| Headphones | Echo-guard (default). Done. |
| Laptop speakers, quiet room | Echo-guard (default). Optional smart-turn for fewer premature cuts. |
| Speakerphone, untreated room | Echo-guard 500ms + RNNoise + smart-turn. Half-duplex if still echoing. |
| Kiosk / demo / loud playback | Half-duplex. Skip the rest. |
| Bluetooth audio path | Half-duplex (Bluetooth defeats most AEC). |

## Heavier paths (not in protoVoice)

For deployments where layers 1-5 aren't enough:

- **Server-side AEC with reference signal** — true echo cancellation by subtracting the bot's TTS output from the mic input. Requires DSP work (SpeexDSP, WebRTC AEC3 wrapped server-side). ~half-day implementation.
- **Krisp.ai SDK** — commercial, very good. Pipecat ships `[krisp]` extra; license required.
- **Hardware AEC** — dedicated conference-room hardware (Polycom etc.) handles this in DSP before the audio reaches the browser.

Open an issue if you hit a setup that defeats all five layers — there's design space we haven't explored yet.

## Verifying

Quick sanity check via `/healthz`:

```bash
curl http://localhost:7867/healthz | jq .audio
# {
#   "half_duplex": false,
#   "echo_guard_ms": 300,
#   "noise_filter": "off",
#   "smart_turn": "off"
# }
```

When echo-guard fires, you'll see in the log:

```
[echoguard] suppressing audio (half_duplex=False bot_speaking=False guard_ms=300)
[echoguard] resuming audio
```
