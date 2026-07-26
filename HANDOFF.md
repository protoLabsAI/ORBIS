# HANDOFF — ORBIS

*Refreshed 2026-07-26 (v0.2.165 shipping). This is the durable handoff doc
— the QA checklist, open design questions, and ordered next steps. For the
point-in-time state, read [STATUS.md](./STATUS.md) first; it carries the
live snapshot and is updated every session.*

**Current posture: shipping to testers.** The app is signed, notarized, and
downloadable at orbis.protolabs.studio/download — verify that page
advertises the current version after every release (it silently froze for
two weeks; see STATUS.md 2026-07-26 and #664).

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
- [ ] **LLM-failure UX (#576):** break the API key → speak → the auth line
      within ~3s of your turn; wrong URL → the unreachable line. With
      `LLM_FALLBACK_URL` set: dead primary → the *answer* arrives ~1s late,
      no error line at all.
- [ ] **Persona switch (#608):** mid-conversation, flip Quick tab →
      Persona → "Chef Bruno" → the orb turns Ember immediately and the
      NEXT reply is Bruno — his prompt, `am_michael` voice, no restart.
      Ask a cooking question, then switch back to Default and confirm
      the old voice + orb return.
- [ ] **Persona manager (#609):** Quick tab → Manage… → duplicate Sage,
      tweak the prompt, save, set active; hand-edit the md in
      `~/Library/Application Support/studio.protolabs.orbis/personas/`
      and confirm the dialog reflects it; delete restores the bundled
      original.
- [ ] **Voice persona switch (#610):** say "be Bruno" → confirmation
      arrives in Bruno's voice, ember orb (VERIFIED live 07-12, 4/4
      switches). "Put on the chef" → routes to bruno via the schema
      description hints (#626). "Switch to someone called Batman" →
      spoken list of real personas. (Voice ORB control is parked by
      choice — #627; nothing to QA there until re-enabled.)

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

### Voice naturalness + latency (the "L" series, v0.2.161–165)
- [ ] **Smart Turn (#654):** turn-ends feel semantic, not a fixed silence
      timer — finishing a sentence hands over faster than trailing off
      mid-thought does.
- [ ] **Listener-acks (#656/#660):** on the AEC-confirmed engine they
      auto-enable and do *not* false-trigger on the bot's own tail.
      Backchannel is Fish-only; the UI toggle reflects it.
- [ ] **Latency (#658):** `grep '\[latency\]'` in sidecar.log splits
      TTFA across STT / LLM / TTS. Expect LLM-TTFT + TTS-TTFB to dominate —
      batch Parakeet is ~40× real-time and is *not* the bottleneck.
      `LOG_LATENCY=0` silences it.
- [ ] **Tool-loop guard (#662):** a request that would spin identical tool
      calls gets nudged at 2 repeats and speaks by 3 — no dead air.

### Distribution (do this after every release)
- [ ] orbis.protolabs.studio/download advertises the version you just cut
      (it silently froze at v0.2.159 for two weeks — #664).
- [ ] The advertised `.dmg` URL resolves, and the downloaded app passes
      `spctl -a -vv` + `xcrun stapler validate`.
- [ ] Fresh-machine path: ~3 GB first-run unpack, then the wizard's LLM
      step needs a real endpoint (#649 gates on a live check).

### Regression sanity
- [ ] Reminders still fire (in-process DeliveryController).
- [ ] Session recall: a second session opens with the prior-sessions block in
      the prompt. (`sqlite3 data/orbis.sqlite "SELECT session_id, ended_at FROM sessions;"`)
- [ ] Boot: brand bumper → logo-less boot gate, no stutter.

---

## Known issues / rough edges

- **LLMErrorAnnouncer — DONE + live-soaked 2026-07-11 (#576 closed, PRs
  #599 + #603).** Dead/401 LLM speaks one classified line; with a fallback
  configured, failover retries the failed turn on the backup (seamless,
  live-verified sub-second). The v0.2.154 caveat is cleared — the failover
  line + retry (#603) have shipped since.
- **#601 — FIXED 2026-07-12 (PRs #614 + #616).** The whitelist hole was
  wider than filed: `fallback`, `provider`, `router_model`/`content_model`,
  and the `micro_*` keys were all stripped — on the read path (persona
  loader) AND the write path (config_store, where a drawer save wiped
  hand-authored blocks). Both share `filter_llm_block` semantics now;
  `fallback.api_key` is redacted + echo-back-guarded. The yaml failover
  path works end to end — settings-UI failover is now unblocked.
- **Personas (epic #611) — COMPLETE 2026-07-12 (PRs #617/#619/#621/#623),
  voice + dialog QA pending.** Drop-in `personas/<slug>.md` with live
  switch (prompt/LLM/voice/filler/orb), Quick-tab picker, manager dialog
  ("Manage…"), and the `switch_persona` voice tool. Known nuances:
  cross-backend TTS switch needs a restart (#486); temperature/max_tokens
  are service-constructor params (restart); a persona llm override
  retargets the active service, not the failover switcher's backup member.
- **`set_orb_visual` — fixed (#577/#622, #626) then PARKED BY CHOICE
  (#627).** #562's root cause (unvalidated names persisted + the orb
  store's pending-path wedge) is fixed via `agent/orb_vocab.py`, and
  #626 renders the live vocabulary into the tool schema. Josh parked it
  as not-needed; re-enable = delete the registry-pop in `agent/tools.py`
  + restore OrbControlToggle and the 4 commented eval scenarios. #534
  (voice-edit demo) waits on that re-enable.
- **#625 — summary/context poisoning (OPEN).** A user's "X is broken"
  statement gets summarized into durable context (and `summary.txt`
  recall across boots) and suppresses tool routing for X. The routing
  half is fixed (#626 options-in-schema); the memory-hygiene half needs
  design.
- **#602 — one-off mic wedge (soak observation).** Rust engine stopped
  delivering mic frames mid-session while reporting `mic listening = true`;
  relaunch cleared it. #485/#486 family — treat as extra evidence for the
  robustness bundle.
- **`set_orb_visual` parked (#562, tracked by #577)** — shipped (#560) then
  disabled (buggy). Parked-handler note at `agent/tools.py` + toggle in
  `agent/config_store.py`. Blocks the #534 voice-edit-orb demo.
- **Phase 2 audio not yet validated.** AVAudioEngine voice-processing
  (AEC/AGC/NS) is wired but needs a live Apple Silicon soak before the CPAL
  band-aids come out (MIC_GAIN, STT RMS gates, backchannel/micro-ack off).
  Playbook in STATUS.md § Phase 2b.
- **Distribution / perf / robustness audit #481–491 — mostly banked as of
  2026-07-15.** Closed: **#490** test gate, **#483** lazy torch, **#481**
  TTS/speaker-gate off the event loop (PR #635), **#482** incremental FTS
  (PR #633), **#485** process-tree reaping (PR #634), **#487** a2a from PyPI
  (PR #631). Half-done: **#489** (env GC shipped in PR #637; the 1.7GB
  per-update sidecar re-download is still open) and **#488** (reveal-logs
  PR #581 + copy-diagnostics PR #636; full export-zip + crash reporting
  still open).
  **Still fully open and now the top demo risk: #486** — the audio socket
  binds once with a single-shot accept, so a sidecar restart yields *silent*
  dead audio (also blocks pipeline-rebuild hot-swap and cross-backend persona
  voice switching). **#602** is live evidence of the same family: mic frames
  stopped mid-session while the engine reported `listening = true`, and only
  a relaunch recovered. Treat as one high-churn, device-soaked bundle.
  **#484 dropped** (high churn + the CPAL path it hardens is slated for
  Phase-2 deletion). **#491** (no FE/Tauri test coverage) still open.
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

## Recommended next steps (effort × impact × churn — do in this order)

Banked so far: #490 test gate, #483 lazy torch, **#576 LLM-failure UX**,
**#546 editor-ui extraction**, **#601 llm-key round-trip**, **personas epic
#611 (#607–#610)**, **#577 set_orb_visual re-enabled**, and the whole
2026-07-15/16 fresh-machine sweep — **#485** process-tree reaping, **#482**
incremental FTS, **#481** TTS/speaker-gate off the event loop, **#487**
a2a-from-PyPI, **#489** env GC (half), **#488** copy-diagnostics (half),
**#625** recall-vs-capability, plus the config/boot correctness block
(#641/#645–#653) and the voice "L" series (#654–#662). Sequence what's left:

1. **Spoken QA pass on v0.2.165** — nothing above the boot layer has been
   driven live since the sweep. Checklist above; anything broken gets fixed
   before new work. **This is the gate on wider tester distribution.**
2. **#485 + #486 sidecar/socket robustness bundle** — now the top
   *engineering* risk for demos. #486 (binds-once audio socket → silent dead
   audio on sidecar restart) and **#602** (mic frames stop mid-session while
   `listening = true`; only a relaunch clears it) are the two failure modes
   that kill a live demo with no in-app recovery. **High churn** (RT audio +
   process lifecycle) → deliberate, device-soaked PR, *not* a pre-demo patch.
   #486 also unblocks pipeline-rebuild hot-swap and cross-backend persona
   voice switching.
3. **Tester feedback loop** — #488's copy-diagnostics slice shipped, but
   there's no defined place for a tester to send it. Cheap, and it decides
   whether user testing produces usable signal.
4. **Failover in settings UI** — #601 unblocked the yaml backing store;
   elevate-config-to-UI convention says surface it.
5. **#488 full export-zip + crash reporting** — additive, low churn.
6. **#489 updater payload** — the GC half shipped; the 1.7GB sidecar
   re-download per update is still open and is felt by every tester.
7. **In-app editor parity (#549)** — P1 (#546) done; next **#547** mount the
   full editor in-app, then #548.
8. **Phase 2 audio soak** on Apple Silicon → then the CPAL cleanup commit.

Lower tier (opportunistic): #571 activation affordance, #491 seed FE/Tauri
tests, #536 `@orbis/orb-mcp`, orb gallery epic #543 (#538–542, not started).
**#484 dropped** (high churn + Phase-2-obsoleting).

**Release hygiene (learned the hard way, 2026-07-26):** after cutting a
release, confirm orbis.protolabs.studio/download advertises it. The
changelog sync now errors loudly on failure (#664), but the class of bug —
a best-effort step swallowing failure into a green run — has now bitten
twice (Discord notes at v0.2.113, changelog at v0.2.160–165).

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
