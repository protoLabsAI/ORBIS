# Why Pipecat

protoVoice started on [FastRTC](https://fastrtc.org), a Gradio-based WebRTC wrapper. It got us a sub-200 ms voice loop quickly. Then we tried to add duplex features and hit a wall.

## What FastRTC couldn't do

FastRTC's `ReplyOnPause` is a request/response abstraction — user utterance in, generator out. It assumes one response per user turn. There is no documented API to **push audio out of band**: no way for a background task to say "play this now" without a triggering user utterance.

Workarounds exist (poking the internal emit queue from inside a custom handler), but they mean rebuilding VAD, interruption handling, and sentence chunking from scratch. All the things `ReplyOnPause` gives you for free would have to be re-implemented, just to unlock the one feature we needed.

## What the alternatives offered

We evaluated four options before committing:

| Framework | Server-push audio | Long-running tools | UI | Local STT/TTS/LLM |
|:---|:---|:---|:---|:---|
| FastRTC + hack | Undocumented queue poke | DIY | Keep Gradio | ✓ |
| LiveKit Agents | `session.say()` built-in | Official example ships | Replace (LiveKit client) | ✓ (service adapters) |
| **Pipecat** | `TTSSpeakFrame` + `queue_frame()` | Event-driven idiomatic pattern | Keep custom HTML | **✓ (first-class)** |
| OpenAI Realtime | `response.create` | Supported | Replace stack | ✗ (cloud only) |

## Why Pipecat won

**Frame-shaped pipeline matches our mental model.** Pipeline is literally `Pipeline([input, stt, agg, llm, tts, output])`. Add a processor, remove a processor, swap a backend — all first-class.

**`TTSSpeakFrame` is the primitive we need.** "Speak this now, independent of the current LLM turn." One import, one `queue_frame` call, done. Compare to FastRTC where we'd be reinventing the stream handler.

**Async tool calls are native.** Register a function with `cancel_on_interruption=False` and pipecat injects the result as a developer message when it resolves. Our `DeliveryController` layers policy (NOW / NEXT_SILENCE / WHEN_ASKED) on top.

**Local services are a supported pattern.** Pipecat ships `SegmentedSTTService`, `TTSService`, and `LLMService` as abstract bases. Subclass, yield the right frames, done. Our Whisper and Kokoro wrappers are 40-80 lines each. `OpenAILLMService(base_url=...)` points straight at our local vLLM.

**FastAPI integration is light.** Pipecat's `SmallWebRTCRequestHandler` is two routes on your own FastAPI app. No opinionated web framework.

## What we gave up

- **Gradio UI.** Pipecat doesn't care about Gradio. We replaced the UI with vanilla HTML. This turned out to be a feature — the Gradio chatbox was heavyweight for a voice-only UX.
- **One-call mount.** FastRTC's `Stream(ReplyOnPause(...))` is one import, one call. Pipecat wants two endpoints and a `PipelineTask` inside a background task. Slightly more wiring. Worth it.
- **Browser autoplay ease.** FastRTC's built-in client handles audio unlock automatically. We do it manually now (user clicks Start before anything plays).

## Verdict

Medium migration effort (maybe 6 hours from start-to-validated), massive unlock in capability. Every duplex feature we ship from here is enabled by primitives pipecat ships for free.

## References

- [Pipecat 1.0 release notes](https://github.com/pipecat-ai/pipecat/releases/tag/v1.0.0)
- [`SmallWebRTCTransport` docs](https://docs.pipecat.ai/server/services/transport/small-webrtc)
- [Pipecat p2p-webrtc voice-agent example](https://github.com/pipecat-ai/pipecat-examples/tree/main/p2p-webrtc/voice-agent)
- [`TTSSpeakFrame` / `queue_frame` issue #1787](https://github.com/pipecat-ai/pipecat/issues/1787) — the `EndFrame` race we dodge by sequencing through `BotStoppedSpeakingFrame`
