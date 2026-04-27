---
name: add-pipeline-stage
description: Step-by-step workflow for integrating a new stage into the ORBIS audio/processing pipeline
---

# /add-pipeline-stage

## When to use
Use this when adding a new processing stage (e.g. a detector, filter, or transformer) to the ORBIS pipeline. Covers wiring the stage in, exposing its dependencies, and validating it with tests.

## Steps
1. **Insert the stage into the pipeline** — place it in the correct order relative to existing stages (e.g. before echo-guard, after VAD).
2. **Add the package extra to `pyproject.toml`** — create or update the relevant `[project.optional-dependencies]` extra so the stage's deps are opt-in installable.
3. **Update `.env.example` and `orbis.example.yaml`** — document any new environment variables or config keys the stage requires.
4. **Write the training/usage doc** — add a markdown doc explaining what the stage does, how to train or configure it, and any gotchas.
5. **Write the tests** — cover the happy path, edge cases (e.g. overflow windows, silent frames), and any state the stage carries between calls.
6. **Fix test expectations if needed** — re-read the implementation's actual behaviour (e.g. early-exit on wake, pass-through on overflow) and align assertions to it rather than assumptions.

## Examples
- Adding `wake_word` detector: inserted first in pipeline, added `[wake-word]` extra, documented env vars `WAKE_WORD_MODEL` and `WAKE_WORD_THRESHOLD`, wrote `docs/wake_word.md`, wrote `tests/test_wake_word.py`, then corrected the overflow-window assertion after reviewing early-exit logic.
- Adding a `noise_gate` stage: insert after wake_word, add `[noise-gate]` extra, expose `NOISE_GATE_DB` in `.env.example`, document hysteresis behaviour, write tests for open/closed/transition states.
