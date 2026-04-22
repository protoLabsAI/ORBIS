# Configure Verbosity

Verbosity controls how chatty the agent is — both its **pre-tool acknowledgement** ("hmm, let me check") and its **post-tool spoken reply** (fact + optional follow-up). The LLM emits the preamble inline in its response stream, not via a separate parallel call. See [Natural-Sounding Fillers](/explanation/natural-fillers) for the design.

## Levels

Each level rewrites two blocks in the system prompt — **preamble** (while a tool runs) and **post-tool response** (the spoken answer):

| Level | Preamble | Post-tool response |
|:------|:---|:---|
| `silent` | (none) | 10-15 words; top fact only, no follow-up offer |
| `brief` *(default)* | 2-4 words — "one sec" | 12-18 words; fact + "want more?" |
| `narrated` | 4-8 words — "okay, let me look that up" | 18-25 words; fact + one supporting detail + follow-up |
| `chatty` | up to 12 words | 25-40 words; fact + two details + warm follow-up |

Post-tool response sizing is backed by CHI 2025 (Kim et al., "Listening vs Reading: Information Density in Voice UIs") — optimal spoken summary is 18-25 words, and past 40 words users barge-in or skip 3× more often.

The model writes its own preamble + answer each time, conditioned on the user's actual message. There's no phrase pool. Examples in the prompt are explicitly marked "do NOT copy verbatim."

## Latency tiers gate progress narration

Even with verbosity set, FAST and MEDIUM tools just rely on the inline preamble — no separate progress channel. Only SLOW tools (3s+) get a periodic generated "still working" line:

| Tier | What you hear |
|:---|:---|
| `FAST` (calculator, get_datetime) | Inline preamble + answer in one shot |
| `MEDIUM` (web_search, deep_research, a2a_dispatch) | Inline preamble + answer |
| `SLOW` (slow_research) | Inline preamble + periodic generated progress + final delivery |

## Pick at startup

```bash
VERBOSITY=narrated docker compose up -d protovoice
```

## Change between sessions

The dropdown rewrites the prompt for **future** connections:

```bash
curl -X POST http://localhost:7867/api/verbosity \
  -H "Content-Type: application/json" \
  -d '{"level":"chatty"}'
```

Read the current value:

```bash
curl http://localhost:7867/api/verbosity
# → {"verbosity":"brief"}
```

Why not mid-session: the verbosity setting is baked into the system prompt at connect time. Changing it during a live session would require rewriting the live LLM context, which we deliberately don't do (consistent with how skill switching works — see [Personas & Skills](./personas-and-skills)).

## Backend-aware prosody

The TOOL USE prompt block also includes a backend-specific style hint:

- **Fish Audio S2-Pro** — the LLM is told it may use `[softly]`, `[pause]`, `[hmm]`, `[thinking]`, `[whisper]` tags sparingly. Sample output: `[softly] hmm, let me check`.
- **Kokoro 82M** — the LLM is told NO bracketed tags (Kokoro speaks them as literal sounds). Sample output: `okay, let me check`.

The active skill's `tts_backend` decides which style hint gets injected. So a Fish-backed persona naturally speaks with prosody tags; a Kokoro-backed persona speaks plain.

## Tuning progress cadence (SLOW tools)

Two-tier cadence matching Alexa's production pattern + the [arXiv 2507.22352](https://arxiv.org/pdf/2507.22352) finding that >4 s of unfilled silence degrades perceived QoE:

- `progress_first_secs` — wait this long after the tool starts before the first "still working" line. Default **2.0 s**.
- `progress_second_secs` — wait this long again before emitting a second ack (~8 s total wall-clock). Default **6.0 s**.

After the second ack the loop stops — narrating past ~8 s reads as performative. A SLOW tool that finishes inside `progress_first_secs` only gets the LLM's inline preamble, no progress narration.

## Persona override

Skill YAMLs can override session verbosity per persona:

```yaml
slug: chef
filler_verbosity: brief   # Chef Bruno is terse — he's busy
```

The override applies at the next connect; the verbosity-shaped prompt block bakes into that session's system message.
