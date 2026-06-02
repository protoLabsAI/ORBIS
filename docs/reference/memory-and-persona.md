# Memory & persona

Two related things: the **persona** is how ORBIS presents and talks; **memory**
is what it remembers about you across sessions. Both are local and single-owner.

For the *why*, see [Memory & persona](/explanation/memory-and-persona).

## Persona

Set under `persona:` in [`orbis.yaml`](./config) — the wizard writes the basics,
and you can edit the rest.

| Key | Meaning |
| --- | --- |
| `name` | What the orb calls itself (default `ORBIS`). |
| `user_name` | What the orb calls **you**. Empty = it won't use a name. |
| `system_prompt_file` | The persona prompt, a Markdown file in the config dir (default `persona.md`). Inline `system_prompt:` overrides it. |
| `temperature` | LLM sampling temperature — higher is more varied. |
| `max_tokens` | Maximum reply length. |
| `filler_verbosity` | How chatty the acknowledgements are: `silent` · `brief` · `narrated` · `chatty`. Also in **Settings → Agent → Behavior**. |

The default persona is **voice-first**: short, spoken-natural answers, no
markdown, no written-channel filler. Edit `persona.md` to retune the voice.

## Memory

ORBIS keeps a small local store (SQLite, in the app's data dir). It holds:

| Store | What it is |
| --- | --- |
| **Sessions** | One record per voice session, saved when the session ends. Recent sessions give the orb continuity — it can refer back to earlier conversations. |
| **Facts** | Things it has learned about you, to recall later. |
| **Reminders** | Your scheduled reminders (see the [Agent reference](./agent#what-the-agent-can-do)). |
| **Inbox** | Background items delivered *between* sessions — e.g. a nightly summary, or an update pushed by another agent — surfaced when relevant (or when you ask "anything for me?"). |

There's also an experimental **personality / mood** layer (the orb drifting in
tone over time). It's **off by default** — ORBIS's focus is being a capable
agent, not an emotional companion.

## Privacy

Memory is **yours and local** — it lives on your Mac and isn't shared. See the
[Access & privacy](./access) reference for the single-owner model.

## See also

- [Memory & persona](/explanation/memory-and-persona)
- [Personalize ORBIS](/how-to/personalize-orbis)
- [Config reference](./config)
