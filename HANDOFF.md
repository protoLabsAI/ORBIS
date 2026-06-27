# HANDOFF — ORBIS

*Refreshed 2026-06-26 (v0.2.146). This is the durable handoff doc — the
QA checklist, open design questions, and ordered next steps. For the
point-in-time state, read [STATUS.md](./STATUS.md) first; it carries the
live snapshot and is updated every session.*

Read order for someone picking ORBIS up cold:

1. [STATUS.md](./STATUS.md) — current snapshot + active threads.
2. [DECISIONS.md](./DECISIONS.md) — frozen architecture decisions; don't
   re-litigate without reading the entry.
3. [CLAUDE.md](./CLAUDE.md) — agent operating notes + the nuke-and-rebuild
   dev loop + the "voice doesn't work" diagnosis checklist.
4. [`docs/internal/native-audio-direction.md`](./docs/internal/native-audio-direction.md)
   — the Apple-Silicon-only direction + 4-phase migration plan.
5. This file — what to test, what's open, what to do next.

---

## Product shape (durable)

ORBIS is a **voice-first AI companion + agent** — an orb that talks back in
real time, remembers you across sessions (SQLite memory + personality), and
**delegates** heavy reasoning/execution to your configured agents (A2A + ACP).
Router-first: it hands off rather than trying to be a framework itself. Single
owner, tailnet-hostable.

- **Platform:** Apple Silicon Mac is the only first-class target; iOS is the
  planned secondary. Web / PWA / browser is dropped. (DECISIONS 2026-04-28.)
- **Distribution:** **fully free + open source** (Apache-2.0). The paywall/
  entitlement subsystem was removed in #551 (PR #573) — no gating on orb
  customization or anything else.
- **Provenance:** forked from `protoLabsAI/protoVoice` v0.12.1, then demolished
  and rebuilt. Old PWA history archived at `archive/pwa-main-v0.2.22`.

---

## QA checklist — drive these on the running build

Build + launch with `./scripts/nuke-and-rebuild.sh --launch --tail` (see
CLAUDE.md for why a partial rebuild silently misleads you).

### Voice loop + agent
- [ ] Tap-to-talk → speak → STT → LLM → TTS round-trip; multi-turn stays clean.
- [ ] *"Who can you delegate to?"* lists the configured delegates.
- [ ] *"Ask <delegate> to …"* → A2A/ACP routing; the orb narrates the work and
      speaks the result (ACP actually edits the file).
- [ ] A multi-step goal → `orchestrate` narrates its plan, emits spoken
      "still working" beats on slow steps (presence loop), and synthesizes a
      final answer. HITL: if it `ask_user`s, it waits for your spoken answer
      and resumes (watch the live voice timing — historically under-tested).
- [ ] **Barge-in:** interrupt mid-delegation → the stale delegate/orchestrate
      answer is dropped, not spoken late (#565).
- [ ] Add/remove a delegate in Settings while running → seen on the next turn
      (hot-swap, no restart).

### Orb editor (free, in-app)
- [ ] Orb tab → edit shader/controls → changes persist to the sidecar config
      (#568) and survive a relaunch.
- [ ] Orb tab routes to the full editor (#554); imported `.orbis` orb survives
      an app update and stays selected.
- [ ] `set_orb_visual` is **disabled** (#562) — confirm the voice agent does
      *not* try to restyle the orb until that's fixed.

### Activation + updater
- [ ] "Tap to talk" affordance works; wake word is hidden (#572). Listen-window
      slider visible under the activation styles.
- [ ] In-app updater → "Update & Restart" → changelog renders as a markdown
      modal (#561).

### Regression sanity
- [ ] Reminders still fire (in-process DeliveryController).
- [ ] Session recall: a second session opens with the prior-sessions block in
      the prompt. (`sqlite3 data/orbis.sqlite "SELECT session_id, ended_at FROM sessions;"`)
- [ ] Boot: brand bumper → logo-less boot gate, no stutter.

---

## Known issues / rough edges

- **LLMErrorAnnouncer (not built) — worst first-user failure.** A dead/401'd
  LLM leaves the orb silently "thinking" forever: StallWatchdog disarms on
  `LLMFullResponseStartFrame`, the ErrorFrame flows upstream where nothing
  re-arms. Designed, not built. (STATUS.md 06-11.)
- **`set_orb_visual` parked (#562)** — shipped (#560) then disabled (buggy).
  See the parked-handler note at `agent/tools.py` + the toggle in
  `agent/config_store.py`. Blocks the #534 voice-edit-orb demo.
- **Phase 2 audio not yet validated.** AVAudioEngine voice-processing
  (AEC/AGC/NS) is wired but needs a live Apple Silicon soak before the CPAL
  band-aids come out (MIC_GAIN, STT RMS gates, backchannel/micro-ack off).
  Playbook in STATUS.md § Phase 2b.
- **Distribution / perf / robustness audit #481–491 — all still open**
  (re-verified 06-26). Highest-leverage: #486 binds-once audio socket → silent
  dead audio on sidecar restart (also blocks pipeline-rebuild hot-swap), #485
  bare-SIGKILL orphans sidecar grandchildren, #489 updater re-downloads the
  1.7GB sidecar.
- **Two flaky/pre-existing test failures** (not regressions):
  `test_skill_llm_resolution::test_micro_model_defaults_to_model` (fails on
  clean main — env default picks `protolabs/nano`) and
  `test_a2a_migration::test_closed_loop_send_returns_answer` (passes in
  isolation; flaky under the full suite).

---

## Open design questions

1. **Activation affordance (#571).** "Tap to talk" hides the wake word, but
   what's the real talk affordance — an on-orb button, a global hold-to-talk
   hotkey, or both? #572 was a partial step.
2. **In-app ↔ live editor parity (#549).** Plan is extract the editor panes
   into a shared package (#546) → mount the full editor in-app (#547) → unify
   authoring polish (#548). Confirm the shared-package boundary before P1.
3. **Orb gallery scope (#543).** Community share/vote/curate (Worker+D1+R2+
   GitHub OAuth, phases #538–542, not started). Decide moderation + "Orb of
   the Day" curation model before building.
4. **Phase 2 audio cleanup gating.** What evidence clears the CPAL fallback
   deletion and re-enables backchannel/micro-ack? (Live soak across first-run,
   denied-permission, and noisy-room.)
5. **Per-variant mood visual mapping.** `moodStore` polls but no orb variant
   subscribes — mood flows into prompts but never shows in the orb. Still the
   biggest visible gap vs DECISIONS.md. (Note: emotional layer is paused by
   default, `ORBIS_EMOTIONAL_LAYER=0`.)

---

## Recommended next steps (priority order)

1. **LLMErrorAnnouncer** — catch the upstream ErrorFrame, classify (auth vs
   unreachable), speak one throttled canned line. Kills the silent-thinking
   first-user failure.
2. **Fix + re-enable `set_orb_visual`** (#562) → unblocks the #534 demo.
3. **In-app editor parity** (#546 → #547 → #548).
4. **Triage the distribution audit** — start with #486 (socket re-accept) and
   #485 (graceful shutdown); they cause silent failures on restart.
5. **Phase 2 audio soak** on Apple Silicon → then the cleanup commit.

---

## Useful commands

STATUS.md § "Quick-start" + "Useful commands" carry the full set (memory
inspection, LLM-endpoint test, config round-trip, full reset). The one you'll
use most:

```bash
./scripts/nuke-and-rebuild.sh --launch --tail   # the supported dev loop
python -m pytest                                 # full suite (or: uv run --extra test pytest -q)
```

## One-line rollback

The seed (pre-carve protoVoice v0.12.1) is the first squashed commit:

```bash
git checkout 25bcc9d
```
