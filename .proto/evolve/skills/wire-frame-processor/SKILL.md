---
name: wire-frame-processor
description: Wire a new pipecat FrameProcessor into the ORBIS pipeline — class authoring through pipeline insertion
---

# /wire-frame-processor

## When to use
Use this when adding a new processing stage to the ORBIS pipecat pipeline: writing the `FrameProcessor` subclass, wiring it into `app.py`, and positioning it correctly between existing pipeline components.

## Steps
1. **Author the processor class** — Append the new `FrameProcessor` subclass to the appropriate module under `agent/` (e.g. `agent/intent.py`). Include `__init__`, `process_frame`, and any supporting methods.
2. **Refine to proper FrameProcessor** — Ensure the class inherits from `FrameProcessor`, uses `await self.push_frame(frame)` for pass-through, and handles only the `Frame` types it cares about.
3. **Update `app.py` imports** — Add the new class to the import block at the top of `app.py`.
4. **Construct the processor in `run_bot`** — Instantiate the processor and any associated handlers/callbacks inside the `run_bot` function, near the other component constructions.
5. **Insert into the pipeline** — Place the processor in the `Pipeline([...])` list at the correct position relative to existing components (e.g. between `audio_tags` and `user_agg` for pre-LLM interception).

## Examples
- Adding `IntentRouterProcessor` between `audio_tags` and `user_agg` to intercept `TranscriptionFrame` before the LLM aggregator sees it.
- Adding a moderation `FrameProcessor` between `user_agg` and `llm` to filter or transform `LLMMessagesFrame` before generation.
