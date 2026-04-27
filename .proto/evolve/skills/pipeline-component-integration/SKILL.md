---
name: pipeline-component-integration
description: Integrate a new component into the ORBIS pipeline — import, builder function, config wiring, pipeline insertion, pyproject extra, and env/yaml examples.
---

# /pipeline-component-integration

## When to use
Use this when adding a new processing stage (e.g. wake-word detector, speaker gate, echo guard) to the ORBIS `app.py` pipeline. Covers every touch-point from code to config to docs.

## Steps
1. **Import** — Add the component's import to `app.py`.
2. **Builder function** — Add a `_build_<component>()` helper next to the other `_build_*` functions in `app.py`.
3. **Config resolution** — Locate the behavior-block resolution section and add the component's config key (e.g. `ww_cfg`).
4. **Instantiate in pipeline setup** — Add `<component> = _build_<component>(<cfg>)` beside the other component constructions.
5. **Insert into pipeline** — Add the component at the correct position in the pipeline chain (e.g. first, before echo-guard).
6. **pyproject.toml extra** — Add a `[<component>]` optional-dependencies extra so the dependency is opt-in.
7. **Update `.env.example` and `orbis.example.yaml`** — Document any new env vars and config keys so users know what to set.

## Examples

**Adding a wake-word detector:**
1. `from orbis.wake_word import WakeWordDetector` in `app.py`
2. `def _build_wake_word_detector(cfg): ...` next to `_build_speaker_gate`
3. Add `ww_cfg = cfg.get("wake-word", {})` in the behavior-block resolution section
4. `wake_word = _build_wake_word_detector(ww_cfg)` beside `speaker_gate = _build_speaker_gate(sg_cfg)`
5. Insert `wake_word` as the first stage in the pipeline, before echo-guard
6. Add `wake-word = ["pvporcupine>=3"]` under `[project.optional-dependencies]` in `pyproject.toml`
7. Add `WAKE_WORD_MODEL=` to `.env.example` and `wake-word: { enabled: false }` to `orbis.example.yaml`

**Adding a noise suppressor:**
Follow the same 7 steps, inserting the suppressor after echo-guard but before the speaker gate.
