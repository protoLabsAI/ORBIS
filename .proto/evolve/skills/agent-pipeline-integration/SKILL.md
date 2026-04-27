---
name: agent-pipeline-integration
description: Integrate a new ML component (classifier, reranker, etc.) into an existing agent pipeline by reading the codebase, identifying hook points, implementing the module, and wiring it in.
---

# /agent-pipeline-integration

## When to use
Use this when adding a new ML-backed component (intent classifier, reranker, retrieval layer, etc.) to an existing agent or pipeline. Applies whenever the new module must slot between existing processing stages.

## Steps
1. **Read existing code** — Load the relevant agent, memory, and LLM call files to understand the current pipeline structure before touching anything.
2. **Identify insertion points** — Trace the data flow (e.g., frame processors, aggregators, search paths) and pinpoint exactly where the new component should be inserted.
3. **Fetch / confirm model architecture** — Check the training script or model card to match the exact architecture (layer sizes, activations, dropout, output labels) so the inference code is correct.
4. **Implement the module** — Write the new file (e.g., `agent/intent.py`, `memory/reranker.py`) as a self-contained processor or helper, handling missing-model gracefully.
5. **Wire it into the pipeline** — Edit the existing integration file (e.g., `memory/facts.py`, `app.py`) to call the new module at the identified insertion point.
6. **Verify the hook** — Confirm the data types flowing in/out match what adjacent pipeline stages expect (e.g., `TranscriptionFrame`, search result lists).

## Examples
- Adding an intent classifier between audio tagging and the LLM aggregator: read `app.py` → find `TranscriptionFrame` path → implement `agent/intent.py` → insert processor between `audio_tags` and `user_agg`.
- Adding a BM25 + cross-encoder reranker to memory search: read `memory/facts.py` `search()` → implement `memory/reranker.py` → call reranker inside `search()` before returning results.
