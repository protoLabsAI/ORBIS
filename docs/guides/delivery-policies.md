# Delivery Policies

Tools can return results in three different ways. Verbosity controls *how chatty* the filler is; delivery policy controls *when* the real result gets spoken.

## Priority (the front-door API)

Callers normally pass a **priority** — the controller maps it to the right policy. Priority model matches Apple's [UNNotificationInterruptionLevel](https://developer.apple.com/documentation/usernotifications/unnotificationinterruptionlevel):

| Priority | Auto-maps to | Use for |
|:---|:---|:---|
| `critical` | `now` | Hard alerts the user must hear immediately |
| `time_sensitive` | `next_silence` | Normal push results — wait for the next pause |
| `active` *(default)* | `when_asked` | Results that matter only if the user comes back to the topic |
| `passive` | `when_asked` (TODO: dedicated digest) | Low-signal background info, hold for a digest |

Explicit `policy=` still wins if a caller needs to override the mapping.

## The three policies

| Policy | When it speaks | Use for |
|:---|:---|:---|
| `now` | Immediately — interrupting the user if they're speaking | Urgent alerts, hard deadlines |
| `next_silence` | Next VAD-detected user silence + 600 ms settle | Normal push results |
| `when_asked` | Only if the user's next utterance contains a query keyword | Background lookups that might not matter anymore |

`now` and `when_asked` are opt-in via `controller.deliver(..., priority=)` or by passing `policy=` directly.

## How it plumbs

```
LLM calls slow_research(query="history of hot dogs")
  ↓
Tool handler returns to LLM immediately:
  "Sure — I'll look that up and let you know. You can keep talking."
  (LLM speaks this as its turn; user can chat in the meantime)
  ↓
Background asyncio task runs the real work
  ↓
After 20s: handler calls controller.deliver(result, policy=NEXT_SILENCE, keywords=...)
  ↓
DeliveryController is a pipeline processor; it sees VAD + transcripts
  ↓
  next_silence:  waits for UserStoppedSpeakingFrame + 600ms,
                 then pushes TTSSpeakFrame(result)
  now:           pushes TTSSpeakFrame immediately (barges in)
  when_asked:    holds until next TranscriptionFrame matches keywords
```

## Keyword matching for `when_asked`

The tool handler passes keywords derived from the original query:

```python
keywords = tuple(w for w in query.split() if len(w) > 3)
await controller.deliver(phrase, priority=Priority.ACTIVE, keywords=keywords)
```

Matching is a naive case-insensitive substring search. If any keyword appears in the user's next utterance, the result drops. No synonyms, no stemming. A smarter matcher (embedding similarity) is an obvious upgrade.

## Filler during async tools

When an async tool dispatches, the opening filler still fires (via `on_function_calls_started`), but:

- Progress loop does NOT run for async tools — the LLM's initial "I'll look into it" reply handles the acknowledgement.
- Delivery is driven entirely by the policy when the tool completes.

This avoids the annoying "still looking, still looking, still looking" chorus when the user has already moved on.

## Testing locally

`slow_research` is wired as an async tool for validation. Ask:

> "Can you investigate the history of hot dogs when you have a moment?"

The agent should:
1. Acknowledge the request ("Sure — I'll look into that...")
2. Let you chat about other things for ~20 seconds
3. Drop in when you pause: "Okay, I found what you asked about the history of hot dogs..."

Tune the sleep with `SLOW_RESEARCH_SECS`.

## Bid-then-drain (≥ 2 items)

When two or more `NEXT_SILENCE` items would drain at the same user pause, the controller asks first instead of flushing all of them:

> "I've got updates from ava and slow_research — want to hear them?"

- User says **yes / sure / okay / tell me** → all held items drain now.
- User says **no / later / skip** → held items are discarded.
- User says neither (changes topic) → items stay queued; may drain on a future pause or get pruned by overflow.

**Exception:** if any held item has priority `critical` or `time_sensitive`, the bid is bypassed — those land immediately, the rest wait until the user asks.

Pattern from [CHI '24 (Zhang et al., "Better to Ask Than Assume")](https://dl.acm.org/doi/full/10.1145/3613904.3642193) — announce-before-barge outperforms direct delivery on trust and acceptance.

## Backpressure (overflow pruning)

If the pending queue grows past **3 items** at drain time, the controller drops low-priority stale ones before draining — keeps a long silence from turning into a monologue when results have piled up.

Sort: priority rank DESC (critical first), then recency DESC (newest first). Keep top-3, plus any `critical` / `time_sensitive` beyond that. The latter two priorities are never dropped regardless of count — they're the ones the user actively needs to hear.

Pattern borrowed from [ProMemAssist](https://arxiv.org/pdf/2507.21378) (UIST '25) which validated utility-gated discard over summarization for voice queues.

## Cross-session replay (reconnect)

If the user disconnects before a delivery lands — or an A2A push / `slow_research` result arrives while no voice session is live — the item is stashed to `{SESSION_STORE_DIR}/{user_id}/{skill_slug}.pending.json` (default `/tmp/protovoice_sessions/`).

On the next session connect by the same user with the same skill:

1. `drain_stashed_deliveries(user_id, skill_slug)` reads + deletes the file.
2. Items are re-enqueued via `delivery.replay_stashed(...)`.
3. If there are ≥ 2, the [bid-then-drain](#bid-then-drain-%E2%89%A5-2-items) path kicks in automatically — the agent asks "I've got updates from ava and slow_research — want to hear them?" before flushing.

Pattern from LangGraph's [interrupt + checkpointer](https://docs.langchain.com/oss/python/langgraph/interrupts) model + the A2A spec's requirement that push configs persist until task completion.

## Edge-case timers

A watchdog ticks once per second while anything is queued:

- **NEXT_SILENCE fallback** — if a `NEXT_SILENCE` item has been pending longer than `DELIVERY_NEXT_SILENCE_FALLBACK_SECS` (default **10 s**), drain it anyway. Handles the muted-mic case where VAD never emits a user-stopped frame.
- **WHEN_ASKED TTL** — items that never match a keyword are silently dropped after `DELIVERY_WHEN_ASKED_TTL_SECS` (default **10 min**), so the queue doesn't accumulate forever.

Both thresholds are env-configurable. Watchdog exits when the queue drains; re-arms on the next `deliver()`.
