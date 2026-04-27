---
name: wire-ai-component
description: Step-by-step workflow for building and wiring a new AI component (classifier, reranker, etc.) into the ORBIS pipeline
---

# /wire-ai-component

## When to use
Use this when introducing a new AI component (e.g. intent classifier, reranker, retrieval module) that needs to be integrated into the existing ORBIS request pipeline and metrics system.

## Steps
1. **Create the component module** — Write the new component in its own file under the relevant package (e.g. `agent/intent.py`, `memory/reranker.py`). Define a clean class or function interface.
2. **Hook into the nearest data layer** — Integrate the component into the lowest-level module that owns the relevant data (e.g. hook reranker into `memory/facts.py` `search()`).
3. **Wire into the app entrypoint** — Import the component in `app.py`, instantiate it, and insert it into the pre-LLM processing path at the correct point.
4. **Add metrics** — Find the `_METRICS` dict in `app.py` and register any new counters, histograms, or gauges for the component.

## Examples
- **Intent classifier:** Create `agent/intent.py` → hook into `app.py` pre-LLM path → add `intent_classified_total` counter to `_METRICS`.
- **BM25 + cross-encoder reranker:** Create `memory/reranker.py` → hook into `memory/facts.py` `search()` → wire result into `app.py` → add `rerank_latency_seconds` histogram to `_METRICS`.
