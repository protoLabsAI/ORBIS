---
name: wire-component-into-pipeline
description: Step-by-step workflow for integrating a new component (classifier, reranker, etc.) into an existing app pipeline with metrics and hooks
---

# /wire-component-into-pipeline

## When to use
Use this when adding a new processing component (e.g. intent classifier, reranker, embedder) to an existing pipeline — requiring code edits across multiple files, metrics registration, and hook-in at the right execution stage.

## Steps
1. **Implement the component** — write or import the core module (e.g. `memory/reranker.py`, `classifiers/intent.py`)
2. **Hook into the relevant data layer** — modify the search/retrieval function (e.g. `memory/facts.py search()`) to call the component
3. **Wire into the app entry point** — import the component in `app.py`, instantiate it, and insert it into the pre-LLM / pipeline path
4. **Register metrics** — find the `_METRICS` dict (or equivalent) and add counters/histograms for the new component
5. **Locate the correct pipeline stage** — read the handler function (e.g. `on_user_turn_handler`) to find exactly where to invoke the component relative to the LLM call
6. **Test end-to-end** — verify the component runs in a real request and metrics increment correctly

## Examples
- Adding an intent classifier: implement class → hook before LLM call in `app.py` → add `intent_classified_total` to `_METRICS`
- Adding a reranker: implement rerank function → call inside `facts.py search()` after vector retrieval → add `rerank_latency_seconds` histogram to metrics
