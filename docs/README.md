# ORBIS documentation

These docs are organised by the **[Diátaxis](https://diataxis.fr)** framework
— four kinds of documentation, each serving a different need. Find what you
want by *what you're trying to do*:

| If you want to… | Read a… | Section |
| --- | --- | --- |
| **learn** ORBIS by doing | Tutorial | [Tutorials](#tutorials) |
| **accomplish a specific task** | How-to guide | [How-to guides](#how-to-guides) |
| **look something up** | Reference | [Reference](#reference) |
| **understand why** it works this way | Explanation | [Explanation](#explanation) |

> **Conventions.** New docs go in the matching subfolder
> (`tutorials/`, `how-to/`, `reference/`, `explanation/`). Keep the four
> kinds distinct — a how-to tells you the steps; reference describes the
> knobs; explanation gives the reasoning; a tutorial teaches by doing. Don't
> mix them in one page. Some legacy docs still live at the `docs/` root and
> are categorised below until they're migrated.

---

## Tutorials
*Learning-oriented — start here if you're new.*

- **[tutorials/getting-started.md](./tutorials/getting-started.md)** — build,
  launch, and have your first conversation: a reminder, a hand-off, an
  external ping.

## How-to guides
*Task-oriented — recipes for a specific goal.*

- **[proactive-companion.md](./proactive-companion.md)** — drive ORBIS by
  voice: set reminders, hand work to your agents, ping it from outside.
  *(Spans how-to + reference + explanation; being split into this section,
  `reference/`, and `explanation/`.)*
- **[build-desktop-binary.md](./build-desktop-binary.md)** — build the
  bundled macOS app.
- **[desktop-signing.md](./desktop-signing.md)** — sign + notarize a release.
- **[desktop-dev.md](./desktop-dev.md)** — the local rebuild/dev loop.

## Reference
*Information-oriented — look it up.*

- **[proactive-companion.md#configuration](./proactive-companion.md#configuration)**
  — config-knob reference (models, delivery, conversation feel, auth).
- **[../config/orbis.example.yaml](../config/orbis.example.yaml)** — annotated
  full config file.
- **[native-audio-transport.md](./native-audio-transport.md)** — the native
  PCM Unix-socket audio contract.
- **[agent-inbox.md](./agent-inbox.md)** — the pull-based inbox + push API.

## Explanation
*Understanding-oriented — the reasoning behind the design.*

- **[proactive-agent-direction.md](./proactive-agent-direction.md)** — the
  proactive-agent architecture + roadmap (cross-stack study).
- **[native-audio-direction.md](./native-audio-direction.md)** — the
  Apple-Silicon-first audio direction + migration phases.
- **[../DECISIONS.md](../DECISIONS.md)** — frozen architecture decisions +
  amendments (the canonical "why").
- **[voice-lifecycle.md](./voice-lifecycle.md)**,
  **[voice-lifecycle-research.md](./voice-lifecycle-research.md)**,
  **[voice-lifecycle-risks.md](./voice-lifecycle-risks.md)** — voice-loop
  behaviour, the research behind it, and known risks.
- **[orb-visualizer.md](./orb-visualizer.md)** — the orb plugin/visualizer
  system.

---

*Dev-onboarding docs (`STATUS.md`, `HANDOFF.md`, `CLAUDE.md`) live at the repo
root — they're for picking the codebase up, not for using or understanding the
product, so they sit outside this map.*
