---
name: add-pipeline-component
description: Step-by-step workflow for adding a new processing component to the ORBIS agent pipeline
---

# /add-pipeline-component

## When to use
Use this when introducing a new stage into the ORBIS agent pipeline — e.g., a detector, gate, filter, or transformer. Covers frame definition, module creation, builder wiring, and pipeline insertion.

## Steps
1. **Define a Frame** — Add the component's input/output frame class(es) to `agent/frames.py`
2. **Write the module** — Create `agent/<component>.py` with the component's processing logic
3. **Add a builder** — Write a `_build_<component>()` helper in `app.py` alongside the other `_build_*` functions
4. **Wire config** — Find the behavior block resolution section in `app.py` and extract the component's config dict (e.g., `ww_cfg = cfg["wake_word"]`)
5. **Instantiate** — Call the builder next to the other component instantiations (e.g., near `_build_speaker_gate`)
6. **Insert into pipeline** — Add the component to the pipeline list in the correct order (e.g., before echo-guard, after VAD)

## Examples
- Adding a **wake word detector**: `WakeWordFrame` → `agent/wake_word.py` → `_build_wake_word_detector()` → `ww_cfg` extraction → instantiation → pipeline insertion before echo-guard
- Adding a **noise gate**: `NoiseGateFrame` → `agent/noise_gate.py` → `_build_noise_gate()` → config wiring → insert after VAD
