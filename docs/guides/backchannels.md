# Backchannels

Backchannels are the brief listener-acks ("mm-hmm", "yeah", "right") humans drop into someone else's turn to signal "I'm tracking, keep going." They're different from filler:

| | Filler | Backchannel |
|:---|:---|:---|
| Fires when | Tool dispatch (agent stalling for compute) | User is mid-utterance (agent listening) |
| Speaker | Agent thinking | Agent acknowledging |
| Length | A few words | One or two tokens |
| Volume | Normal | Quiet (Fish: `[whisper] [softly]`) |

## How it works

`BackchannelController` (`agent/backchannel.py`) sits in the pipeline next to `DeliveryController` and watches `UserStartedSpeakingFrame` / `UserStoppedSpeakingFrame`:

1. User starts speaking → start a timer.
2. After `BACKCHANNEL_FIRST_SECS` (default 5s), generate one backchannel via the same `FillerGenerator` and queue it.
3. Continue every `BACKCHANNEL_INTERVAL_SECS` (default 6s) until the user stops.
4. User stops → cancel the timer.

Most user turns are <5s and never trigger a backchannel. Long monologues get a polite "mm-hmm" partway through.

## Backend rendering

Same backend-aware approach as filler:

| Backend | Sample backchannel |
|:---|:---|
| Fish | `[softly] uh-huh` / `[softly] mhm` / `[softly] yeah` |
| Kokoro | `right` / `hmm` / `uh` |

The Fish wrapper (`[softly]` or `[whisper] [softly]` if missing) keeps the backchannel quiet enough that it doesn't compete with the user's voice.

## Tunables (env)

| Variable | Default | Purpose |
|:---|:---|:---|
| `BACKCHANNEL_FIRST_SECS` | `5.0` | Seconds into a user turn before the first backchannel |
| `BACKCHANNEL_INTERVAL_SECS` | `6.0` | Interval between subsequent backchannels |
| `BACKCHANNEL_COMMIT_GRACE_MS` | `180` | Delay between "generator returned" and "emit frame" so state can update |

## Suppression

- `VERBOSITY=silent` — no backchannels (or any filler) fire.
- A user turn shorter than `BACKCHANNEL_FIRST_SECS` — no backchannel; timer cancels on stop.
- **Bot is thinking or speaking** — `LLMFullResponseStartFrame` flips `_bot_thinking` before audio even begins (leading indicator), `BotStartedSpeakingFrame` flips `_bot_speaking` when audio starts. Either cancels the loop and blocks new starts.
- **Generator finished too late** — after the LLM returns the phrase, the emitter waits `BACKCHANNEL_COMMIT_GRACE_MS` (default 180ms) and re-checks state. If user stopped or bot started in that window, the phrase is dropped.
- **Frame already in flight** — every emitted `TTSSpeakFrame` is tagged. When it re-enters the pipeline via `task.queue_frame`, the backchannel processor re-evaluates state and drops instead of pushing downstream if the world has moved on.
- LLM generator timeout/error — the backchannel is dropped (logged as warning); the next interval tries again.

## Why generative

The same reasons as [filler](/explanation/natural-fillers): a fixed pool of "mm-hmms" pattern-matches to "scripted" within a session. Generative + recency-aware = unique each time, never the same shape twice in close succession.

## Caveats

- **Volume mismatch** — if you find Fish backchannels too loud, edit the wrap in `agent/filler.FillerGenerator.backchannel()` from `[softly]` to `[whisper] [softly]`. Whisper is much quieter but loses some intelligibility — try in your room first.
- **Latency to first backchannel matters** — the LLM generator takes ~50-200ms. If you set `BACKCHANNEL_FIRST_SECS=2`, generation may finish AFTER the user has already paused. The 5s default leaves plenty of headroom, and the emit step re-checks `_bot_speaking` / `_user_speaking` so a late-arriving phrase won't tack onto the agent's reply.

## Disabling

Set `BACKCHANNEL_FIRST_SECS` to a value larger than your longest user utterance (e.g. `999`) — the timer never fires. Or set `VERBOSITY=silent` to mute filler + backchannel together.
