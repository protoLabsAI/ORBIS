---
name: wire-pipeline-component
description: Step-by-step process for integrating a new component into the ORBIS audio pipeline (config, wiring, deps, env, docs, tests)
---

# /wire-pipeline-component

## When to use
Use this when adding a new processing stage (e.g. wake word, noise gate, echo guard) to the ORBIS pipeline. Covers every touch-point from config construction through to test coverage.

## Steps
1. **Build the component** — Implement the component class/function (e.g. `_build_wake_word(cfg)`).
2. **Wire into pipeline** — Find the pipeline assembly site and insert the component in the correct position (e.g. before echo-guard, after VAD).
3. **Add dependency** — Add the required package as an optional extra in `pyproject.toml` (e.g. `[wake-word]`).
4. **Update env/config examples** — Add relevant keys to `.env.example` and `orbis.example.yaml` so new users know the knobs exist.
5. **Write training/usage doc** — Add a Markdown doc explaining the component, its config options, and how to train or tune it.
6. **Write tests** — Cover construction, pipeline integration, and edge cases (component disabled, bad config, etc.).

## Examples
- **Wake-word detector**: build `_build_speaker_gate` → insert before echo-guard in pipeline → add `[wake-word]` extra → document `WAKE_WORD_MODEL` in `.env.example` → write `docs/wake_word.md` → add `tests/test_wake_word.py`.
- **Noise suppressor**: build `_build_noise_suppressor` → insert after VAD stage → add `[noise-suppression]` extra → document threshold knobs → write unit + integration tests.
