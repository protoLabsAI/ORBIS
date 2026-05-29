# Agent inbox

External systems push messages into ORBIS's inbox; the voice agent
pulls them on demand or has urgent items auto-surfaced at session
start. The mechanism is intentionally pull-based and never preempts
an active turn — modelled after [cc-2.18's queued-command pattern][cc].

## When to use

- **Cron jobs** — "post a daily summary to the agent's inbox so the
  user hears it the next time they ask 'anything new?'"
- **Webhooks** — Slack-style integrations push notifications
  ("PR landed", "calendar event reminder"). Owner controls cadence.
- **Sister agents** — another agent in the user's fleet leaves
  context for ORBIS to pick up between sessions.

## Priority model

Each ingested message carries a priority. The ingestor declares
urgency; the runtime + agent decide what to do with it.

| Priority | When surfaced |
|---|---|
| `now` | Auto-surfaced at the next session start (system-prompt block). The agent sees them without having to call `check_inbox`. |
| `next` (default) | Surfaced when the agent calls `check_inbox` — typically on user prompts like "anything new?". |
| `later` | Background chatter. Only surfaced when the agent calls `check_inbox` with `priority_floor='later'` (e.g., user asks "show me everything"). |

Ingestors should be conservative with `now`. Auto-surfacing every
message defeats the agent's ability to triage; reserve it for items
that genuinely shouldn't wait.

## API

### POST /api/inbox — ingest a message

Auth: owner X-API-Key **or** `INBOX_INGEST_TOKEN` env value (in the
`X-API-Key` header). Webhook ingestors should use the scoped token
so they don't need full owner credentials.

```bash
curl -X POST http://localhost:7866/api/inbox \
  -H "X-API-Key: $INBOX_INGEST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "sender":   "webhook:slack",
    "subject":  "Deploy succeeded",
    "body":     "v1.2.3 went live at 14:02 UTC",
    "channel":  "ops",
    "priority": "next"
  }'
```

| Field | Required | Notes |
|---|---|---|
| `sender` | yes | Free-form id (`webhook:slack`, `cron:daily`, `agent:ava`) |
| `subject` | yes | One-line summary (read aloud) |
| `body` | yes | Full message |
| `channel` | no | Optional grouping label |
| `priority` | no | `now` / `next` / `later` (default `next`) |
| `created_at` | no | ISO-8601; auto-stamped to UTC now if omitted |

Returns `{ "ok": true, "id": <int> }`.

### GET /api/inbox — list messages

Auth: owner X-API-Key only.

Query params:
- `unread_only=1` — filter to undelivered
- `priority_floor=now|next|later` — when `unread_only`, the floor
  controlling which priorities are returned (default `later` =
  everything; `next` = urgent + normal; `now` = urgent only)
- `limit=N` — page size (default 50, max 200)

### POST /api/inbox/deliver — mark messages delivered

Auth: owner X-API-Key only. Body `{"ids": [1, 2, 3]}`. Idempotent;
already-delivered ids are no-ops.

## Agent tool: `check_inbox`

The voice agent has a built-in `check_inbox` tool the LLM calls
when the user prompts ("any messages?", "what's in my inbox?") or
when conversation flow suggests a check. The tool's description
shapes its cadence — see `agent/tools.py` for the prompt.

Parameters:
- `priority_floor`: `now` / `next` / `later` (default `next`)
- `include_delivered`: bool (default false)

Surfaced messages are marked delivered automatically so they don't
re-appear on the next call.

## Session-start surfacing

`now`-priority unread messages are appended to the system prompt
under a **PENDING NOTIFICATIONS** block at the start of each voice
session (see `_render_inbox_pending_block` in `app.py`). The block
is read-once: surfacing through the system prompt also marks the
messages delivered, so the same urgent message doesn't appear at
the start of every subsequent session.

The model is instructed to surface the items at a natural break in
conversation rather than leading with them, unless they're truly
urgent. This mirrors cc-2.18's "the runtime almost never decides —
the agent decides" posture: ORBIS never aborts an active turn to
deliver an inbox message.

## Why pull-based + priority instead of mid-turn injection

The voice loop is fundamentally turn-based. Inserting messages
mid-turn would either:

1. **Force the LLM to context-switch** — interrupts the user
   experience and pollutes the active conversation flow.
2. **Get queued silently** — same effective latency as our
   pull-based approach, with extra plumbing.

Session-start injection for `now` + on-demand `check_inbox` covers
the practical cases: urgent items reach the user on their next
interaction (typically seconds away if they're already in session),
non-urgent items wait for natural retrieval. Per-voice-turn
injection is a future enhancement if mid-session urgency surfaces
as a real need.

## Storage

Single SQLite table (`inbox`) in the existing memory DB. Schema
version 2. Migration from v1 ALTERs in the priority column when
needed (handles dev DBs that ran intermediate revisions).

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | autoincrement |
| `created_at` | TEXT | UTC ISO-8601 |
| `sender` | TEXT | required |
| `channel` | TEXT | nullable |
| `subject` | TEXT | required |
| `body` | TEXT | required |
| `priority` | TEXT | `now`/`next`/`later`, default `next` |
| `delivered_at` | TEXT | nullable |

Indexes on `created_at`, `delivered_at`-where-null, and
`(priority, delivered_at)`.

## Configuration

| Env | Purpose |
|---|---|
| `INBOX_INGEST_TOKEN` | Optional scoped token. When set, accepts ingest writes via `X-API-Key: <token>` without full owner credentials. Read endpoints still require the owner key. |

[cc]: https://github.com/protoLabsAI/cc-2.18 — the queued-command +
attachment-injection pattern this design borrows from.
