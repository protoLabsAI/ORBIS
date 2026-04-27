---
name: pipecat-frame-processor-integration
description: Wire a new FrameProcessor into ORBIS's pipecat pipeline at the correct hook point
---

# /pipecat-frame-processor-integration

## When to use
Use this when adding a new processing stage to the ORBIS voice pipeline — e.g. intent classification, content filtering, transcription transformation — that needs to intercept frames between existing pipeline stages.

## Steps
1. **Identify the hook point** — Determine where in the pipeline the processor belongs. The canonical intercept point for transcription-level work is between `audio_tags` and `user_agg` (i.e. after transcription frames exist but before the LLM user aggregator consumes them). For post-LLM work, hook after the LLM service.
2. **Implement the FrameProcessor subclass** — Add the class to the appropriate module (e.g. `agent/intent.py`). Override `process_frame`: inspect the frame type (e.g. `TranscriptionFrame`), perform the logic, then either pass the frame downstream via `await self.push_frame(frame)` or swallow/replace it.
3. **Wire into `app.py`** — Insert the processor instance into the pipeline tuple/list in the correct position. Order matters: place it between the two stages identified in step 1. Confirm the processor receives the expected frame types by checking adjacent stage outputs.

## Examples
- **IntentRouterProcessor**: Sits between `audio_tags` and `user_agg`. Reads `TranscriptionFrame`, classifies intent, optionally injects a `SystemFrame` or routes to a different handler before passing the frame along.
- **ContentFilterProcessor**: Sits between `user_agg` and `llm`. Reads `LLMMessagesFrame`, redacts sensitive content, then pushes the sanitized frame downstream.
