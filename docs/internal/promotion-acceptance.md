# Promotion acceptance — October 2026

ORBIS is preparing a dependable public beta for Apple Silicon Mac. The release
candidate must pass the native checks below before it is promoted. A green
build or a moving level meter alone does not establish a working conversation.

## Order of work

1. Audio transport and capture failure reporting/recovery (#486, #602).
2. Native acceptance on the exact candidate app, including the failure paths.
3. Structured delegation progress (#711 / #681), checked with spoken tasks.
4. Opt-in wake-word activation, including downloaded-model readiness and mute.
5. Onboarding, download-page, and marketing accuracy for the version advertised.

The app owns voice and presentation; protoAgent remains authoritative for
durable work. Do not add automatic process or microphone restarts without
device evidence that they recover cleanly and preserve those ownership rules.

## Evidence to record

Use one candidate build for each pass. Record source SHA, app version, date,
macOS version, Mac model, microphone and output device, chosen speech/LLM
backends, wake-model id, and the configured delegate. For each row below,
record PASS, FAIL, or NOT RUN with observed behavior and a timestamp. Keep raw
transcripts and credentials out of public PRs; use private logs for diagnosis.

For a built native app:

```bash
scripts/check-macos-release-config.py
scripts/validate-macos-native-audio.sh --app /path/to/ORBIS.app
scripts/validate-macos-native-audio.sh --app /path/to/ORBIS.app --launch --duration 240
```

The live harness starts the app and requires someone to speak and complete a
turn. It stops existing ORBIS processes and truncates their logs; preserve any
diagnostic evidence before running it. Release signing checks use the pristine
downloaded DMG, as described in `scripts/validate-macos-native-audio.sh --help`.

Do not reset a daily-use configuration to imitate a fresh install. Test first
run using a separate macOS user or an isolated test configuration.

## Native acceptance matrix

| Scenario | Required observation |
| --- | --- |
| Fresh install, cold model cache | Runtime and model downloads show progress; setup requires a working LLM; voice stays unavailable until the backend reports running. |
| Microphone permission denied | Actionable permission guidance; granting access and relaunching restores a conversation. |
| Returning-user cold boot | Saved persona, model, orb, and activation style survive; there is no setup flash or premature listening claim. |
| Three ordinary voice turns | Each utterance produces one audible reply; status follows listening, thinking, speaking, and idle. |
| Barge-in during speech | Reply stops promptly; the new utterance gets a reply without hearing or replaying the old tail. |
| Sidecar/socket connection loss | Status no longer claims usable voice; the offered recovery action restores a full turn, and stale microphone/TTS frames are not replayed. |
| Input callback stalls | Capture failure becomes visible; silence or hard mute alone does not trigger failure; the offered recovery restores a full turn. |
| Sleep/wake and input/output changes | Repeat a conversation after sleep and after switching devices; failure is visible if recovery cannot complete. |
| Short delegated task | Acknowledgment/progress and one final spoken result; tool progress clears when that task finishes. |
| Long task and a second task | Current-task progress stays authoritative; older task events do not replace the newer task or resurrect a terminal task. |
| Hub completes before monitoring attaches | Re-query delivers the authoritative result exactly once. |
| Cancel while delegating | Hub reaches canceled state; no late result or progress resurfaces. |
| Delegated question | ORBIS asks, accepts the spoken answer, resumes progress, and delivers one final result. |
| SSE or session reconnect | Authoritative hub state is re-queried; stale local progress clears and completed results are delivered once. |
| Reminder during conversation | Reminder arrives at a pause, once, and remains part of conversational recall. |
| Hub unavailable | One useful warning; ordinary conversation remains available with the chosen LLM. |

Run ordinary conversation, interruption, and wake tests with the built-in mic
and speakers, headphones, and a Bluetooth device if available. Record absent
devices as NOT RUN rather than implying coverage.

## Wake-word acceptance

Wake word is opt-in. Existing users keep their activation style, and tap to
talk remains the fallback. Model integrity and inference-shape checks establish
loadability, not recognition quality. Treat the custom Hey Orbis classifier as
experimental until a recorded spoken pass supports stronger claims.

| Scenario | Required observation |
| --- | --- |
| No model downloaded | Wake mode cannot be activated prematurely; shared and selected model download status/errors are visible. |
| Valid downloaded model | Detector becomes armed only after successful load; the chosen phrase opens listening and a full voice turn completes. |
| Ten phrase attempts | Record successes, misses, distance, and ambient conditions; investigate inconsistent triggering before promotion. |
| Quiet, conversation, and background playback | Observe at least ten minutes per condition; record false activations and whether the bot's voice triggers itself. |
| Hard mute, including rapid mute/unmute | Phrase and queued pre-mute audio cannot open listening while muted; clean new audio can wake after unmuting. |
| Auto-close | After the configured listen window, mode returns to armed and the next wake opens a new turn. |
| Model missing, corrupt, or removed | Wake failure is visible; tap to talk remains usable without a false armed indication. |
| Timer/weather stock classifiers | Model-specific embedding windows load successfully and produce bounded detector state. |

## Baseline observed on 2026-10-06

The base checkout is `b9068ed` and the shipping release is v0.2.171. Its release,
desktop build, backend CI, frontend CI, lint CI, and marketing deployment were
green. The advertised DMG returned HTTP 200. The installed v0.2.171 app passed
Gatekeeper (`Notarized Developer ID`), stapler validation, and the static native
bundle harness on this arm64 Mac.

The base backend test suite (two optional tests skipped), frontend build, and
23 boot/readiness tests passed locally. Python lint passed. Frontend lint
reported 55 errors and 9 warnings, including generated
`dev-dist` files and source findings. No spoken native acceptance or wake-word
recognition pass was performed for that baseline. Those checks remain NOT RUN.

## Promotion gate

Candidate code changes stay in draft until the relevant native rows are
recorded and the visible UX is reviewed. Marketing copy stays accurate for the
currently downloadable version; publish restored wake-word claims only after
the corresponding tested app is downloadable. Follow the repository's required
`QA panel` status check before any merge; do not bypass it.
