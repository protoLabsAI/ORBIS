# Personalize ORBIS

Make the orb yours — its name, what it calls you, how it talks, and how chatty
it is. For the full list of knobs, see the
[Memory & persona reference](/reference/memory-and-persona).

## Set your name and the orb's name

The quickest path is the setup wizard's **Introductions** step. To change them
later, edit `persona:` in [`orbis.yaml`](/reference/config):

- `name` — what the orb calls itself (default `ORBIS`).
- `user_name` — what the orb calls **you**. Leave empty and it won't use a name.

## Change how it talks

The orb's voice comes from its **persona prompt** — a Markdown file
(`persona.md` in the config dir). Edit it to retune tone and behaviour; keep it
**voice-first** (short, spoken-natural, no markdown), since everything is read
aloud. Point `persona.system_prompt_file` at a different file, or set an inline
`system_prompt:` instead, omitting the file setting.

The default style is conversational: one short sentence, a second when useful,
contractions, and no routine follow-up offers or plan narration. Ask for more
detail when you want it. Custom personas keep their character, with a shared
spoken-delivery rule that keeps answers in plain prose. Markdown formatting
is also removed at the speech synthesis boundary.

## Tune how chatty it is

The **verbosity** setting shapes acknowledgements and the detail in normal
spoken results. Find it in **Settings → Agent → Behavior** (or
`persona.filler_verbosity` in config):

- **Silent** — no acknowledgements; the result stays very short.
- **Brief** — one short sentence for the result (the default).
- **Narrated** — one or two sentences with useful supporting detail.
- **Chatty** — a few short sentences.

None of these modes adds a routine "want more?" question. You can still ask
for an explanation or a longer answer.

## Reset memory

Memory lives in a local store on your Mac. To start the orb fresh — clear its
sessions and learned facts — remove the app's data store (it's recreated on next
launch). This is also how you re-run the setup wizard.

## See also

- [Memory & persona reference](/reference/memory-and-persona)
- [Memory & persona](/explanation/memory-and-persona)
