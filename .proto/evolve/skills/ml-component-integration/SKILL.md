---
name: ml-component-integration
description: Integrate a new ML component (classifier, reranker, embedder, etc.) into an existing pipeline by locating insertion points, implementing the module, and wiring it in.
---

# /ml-component-integration

## When to use
Use this when adding a new ML model or inference component (e.g., intent classifier, reranker, embedder) to an existing processing pipeline. Applies whenever the component needs a defined insertion point, a standalone module, and a hook into existing logic.

## Steps
1. **Identify insertion points** — Read the pipeline code to find exactly where the new component should receive input and emit output (e.g., between two existing processors or inside a search/retrieval function).
2. **Confirm model contract** — Check `train.py` or equivalent to pin down the model architecture, input shape, output format, and artifact schema (weights file format, label names, etc.).
3. **Implement the component module** — Write the standalone module (e.g., `agent/intent.py`, `memory/reranker.py`) with a clean interface matching the pipeline's data types.
4. **Hook into existing system** — Patch the integration point in the host file (e.g., `memory/facts.py` `search()`) to call the new module, handling fallback/disabled cases gracefully.
5. **Verify end-to-end** — Confirm the frame/data flows correctly through the new component with a smoke test or log trace.

## Examples
- Adding an intent classifier between `audio_tags` and `user_agg` in a voice pipeline: read `TranscriptionFrame` after tags, emit intent label before the LLM aggregator.
- Plugging a BM25 + cross-encoder reranker into `memory/facts.py` `search()` as a post-processing step over raw retrieval results.
