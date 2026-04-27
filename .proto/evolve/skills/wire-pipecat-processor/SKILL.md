---
name: wire-pipecat-processor
description: Step-by-step workflow for designing and wiring a new FrameProcessor into the ORBIS pipecat pipeline in app.py
---

# /wire-pipecat-processor

## When to use
Use this when adding a new `FrameProcessor` (or subclass) to the ORBIS pipeline — e.g., intent routers, filters, interceptors. Covers design, placement, import wiring, and closure-ordering pitfalls.

## Steps
1. **Identify the hook point** — Determine where in the pipeline the processor belongs (e.g., between `audio_tags` and `user_agg`, before LLM, after STT). Verify no existing pipecat hook (like `before_generate`) already covers the need.
2. **Design the FrameProcessor subclass** — Implement as a proper `FrameProcessor` (not a thin wrapper). Override `process_frame` and handle both the target frame type and passthrough for all others.
3. **Update imports in `app.py`** — Add the import for the new processor class at the top of `app.py`.
4. **Construct the processor inside `run_bot`** — Instantiate the processor in the `run_bot` function, after all dependencies it needs (tools schema, session delegates, skill config, etc.) are available.
5. **Insert into the pipeline** — Add the processor to the pipeline list at the correct position between existing components.
6. **Handle closure-ordering issues** — If the processor's callback references `task` (or other objects created *after* the pipeline), confirm the callback is an async closure that captures by reference — it will resolve correctly at call time even if `task` is assigned later.
7. **Smoke test** — Send a test frame through the pipeline and verify the processor fires correctly and passes unhandled frames downstream without dropping them.

## Examples
- **Intent router:** Place `IntentRouterProcessor` between `audio_tags` and `user_agg` to intercept `TranscriptionFrame` before it reaches the LLM aggregator. Its `_command_bypass_handler` can safely reference `task` via closure even though `task` is created after pipeline construction.
- **Audio filter:** Place a noise-gate `FrameProcessor` between the transport input and `audio_tags` to drop frames below an energy threshold before any downstream processing.
