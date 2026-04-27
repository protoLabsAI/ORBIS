---
name: pipecat-frame-processor
description: Wire a new FrameProcessor into a pipecat pipeline — create the class, refine it, and connect it in app.py
---

# /pipecat-frame-processor

## When to use
Use this when you need to intercept or transform frames in a pipecat pipeline — e.g., intent routing, transcription filtering, or any pre/post-LLM processing step that requires a named position in the pipeline.

## Steps
1. **Create the processor class** — Append a new `FrameProcessor` subclass to the appropriate module (e.g., `agent/intent.py`). Implement `process_frame` to handle the target frame type and pass others through.
2. **Refine the implementation** — Replace any thin/placeholder version with the full implementation: proper `asyncio` passthrough, correct frame type guards, and any side-effect logic (e.g., intent tagging, routing flags).
3. **Update imports in `app.py`** — Add the new class to the import block from its module.
4. **Wire into the pipeline** — Insert the processor at the correct position in the pipeline constructor (e.g., between `audio_tags` and `user_agg` for pre-LLM transcription interception).
5. **Verify ordering** — Confirm the processor sits at the right stage: before the LLM aggregator for input hooks, after for output hooks.

## Examples
- **Intent routing:** Create `IntentRouterProcessor` in `agent/intent.py`, intercept `TranscriptionFrame`, classify intent, attach metadata, then pass the frame downstream — inserted between `audio_tags` and `user_agg` in `app.py`.
- **Transcription filter:** Create a `ProfanityFilterProcessor` that inspects `TranscriptionFrame` text and either drops or modifies it before the LLM aggregator sees it.
