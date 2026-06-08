# Contributing to ORBIS

Thanks for being here. ORBIS is a voice-first AI companion for Apple Silicon —
an orb that talks to you, remembers you, and routes the heavy lifting to your
agents. This guide gets you from clone to merged PR.

If you only want to *use* or *understand* ORBIS, read [docs/](./docs/) instead
(tutorials, how-to, reference, explanation). This file is for changing the code.

## The one rule that saves you a wasted weekend

**Apple Silicon Mac is the only first-class platform; iOS is the planned
secondary target; web / PWA / browser is not a supported runtime.** Don't add
features that re-introduce browser/PWA support, `getUserMedia`, or WebRTC audio
paths — they'll be declined. The reasoning is frozen in
[DECISIONS.md](./DECISIONS.md) (amendment 2026-04-28) and
[docs/internal/native-audio-direction.md](./docs/internal/native-audio-direction.md).

## The extension model — add things without touching core

Most things you'd want to add are **self-registering folders**: you drop a
folder in, it registers itself on import, and nothing in the core wiring needs
to change. There is no central list to edit.

| You want to add… | Drop it in | How it registers | Guide |
| --- | --- | --- | --- |
| **A UI plugin** — always-on chrome (a button, an overlay, a drawer tab) | `web/src/plugins/<name>/` | `registerPlugin()` in `index.tsx` | [plugins/README](./web/src/plugins/README.md) |
| **A widget** — an openable ambient readout (like Weather) | `web/src/widgets/<id>/` + an entry in `config/widgets.yaml` | `registerWidget()` + catalog entry | [widgets/README](./web/src/widgets/README.md) |
| **An orb variant** — a new visual style for the orb | `web/src/plugins/orb/variants/<id>/` | `registerVariant()` in `index.tsx` | [variants/README](./web/src/plugins/orb/variants/README.md) |
| **An agent tool** — a function the model can call | `agent/tools.py` | `@tool(...)` decorator | [agent/README](./agent/README.md) |
| **A delegate** — a sub-agent to hand work to | `config/delegates.yaml` (or the Settings UI) | config only | [How-to: add a delegate](./docs/how-to/add-a-delegate.md) |
| **A delegate _type_** — support a new agent protocol | `agent/delegate_adapters.py` | `register_adapter()` | [agent/README](./agent/README.md) |

If you find yourself editing a hand-maintained list to register something, that
is a bug in the extension point — open an issue, it should auto-discover.

Import what you register (and the runtime services you need —
voice state, `pushStatusTransient`) from **`@/sdk`**, the stable extension
surface, rather than reaching into internal module paths. The backend client
(`@/lib/api`) and design-system primitives (`@/components/ui/*`) are their own
documented stable modules.

## Dev setup

Requirements: **Python 3.11+**, **Bun** (or npm), **Rust** (for the native
shell), and an LLM endpoint. On Apple Silicon the simplest LLM is the built-in
MLX preset the setup wizard offers — no extra install.

```bash
# Backend + frontend, fast iteration loop
cd web && bun install && bun run dev    # frontend on :5173
python app.py                           # backend on :7866 (separate shell)
```

For the full native app (the real runtime — Rust shell + packaged sidecar):

```bash
scripts/preflight-native-audio-host.sh   # one-time host check
scripts/nuke-and-rebuild.sh --launch --tail
```

`nuke-and-rebuild.sh` exists because ORBIS has several stale-cache failure modes
that look exactly like broken code (the React bundle in `web/dist`, the pyapp
sidecar env cache, lingering audio sockets). When voice misbehaves after a
change, **run the nuke script before debugging** — see the comment block at the
top of the script and the "Dev flow" section of [CLAUDE.md](./CLAUDE.md).

## Tests & checks (what CI runs)

Run these before you push — they're the same gates CI enforces:

```bash
.venv/bin/python -m pytest      # backend suite        (pytest.yml)
cd web && bun run build         # type-check + build    (web-build.yml)
ruff check .                    # python lint           (lint.yml)
```

A green local run ≈ green CI. PRs can't merge until all checks pass.

## Conventions

- **Focused PRs.** One concern per PR. Split unrelated changes even if they came
  from the same session — it makes review and revert sane.
- **Conventional commit titles** — `feat(web): …`, `fix(agent): …`,
  `docs: …`, `test: …`, `chore: …`. The release tooling reads these.
- **UI uses the design system.** Style through the tokens in
  `web/src/index.css` (`fg/surface/edge/brand`, not raw `zinc-*`/`amber-*` or
  literal `text-[13px]`), and reuse `web/src/components/ui/*` primitives rather
  than hand-rolling a toggle/button. A test enforces the token rule.
- **No web/PWA reintroduction** (see the one rule above).

## Where things live

```
app.py            sidecar entrypoint
agent/            the voice agent — tools, delegates, persona, memory glue
voice/            STT / transport / barge-in / SSE bus
memory/           SQLite store (sessions, facts, personality, mood)
src-tauri/        the native macOS shell (Rust); audio engine under src/audio/
web/src/          the React frontend (orb, drawer, plugins, widgets)
config/           orbis.yaml, delegates.yaml, starter_orbs.yaml, widgets.yaml, persona.md
docs/             user documentation (Diátaxis); internal/ is dev/architecture notes
tests/            pytest suite
```

On a cold pickup, read [STATUS.md](./STATUS.md) (current snapshot),
[DECISIONS.md](./DECISIONS.md) (frozen architecture), then
[HANDOFF.md](./HANDOFF.md) (open questions + next steps).

## Filing issues

Bugs: include your OS, how you launched (dev `python app.py` vs the native app),
and the relevant log (`~/Library/Logs/studio.protolabs.orbis/sidecar.log` for the
Python side, `/tmp/orbis-tauri.stderr` for the Rust side). Features: say what you
want to *do*, not just what to build — it helps us route it to the right seam.

Welcome aboard. 🛸
