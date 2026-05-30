"""SQLite connection + schema for ORBIS memory.

Single-file embedded store. Schema creation is idempotent; subsequent
versions bump SCHEMA_VERSION and add conditional ALTERs in
``_migrate``. FTS5 is required (standard with the Python sqlite3
bundle); init raises if the runtime doesn't have it.

Usage::

    from memory import Memory
    m = Memory()                                  # opens default path
    m.sessions.add(...)
    m.facts.search("jazz")
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)


def _default_db_path() -> str:
    """Resolve the DB path per this precedence:

      1. ``ORBIS_DB_PATH`` env var — explicit override
      2. legacy ``data/orbis.sqlite`` if the repo-local ``data/``
         directory already exists — keeps existing repo-checkout
         installs working unchanged
      3. ``agent.paths.get_db_path()`` — per-OS user data dir used by
         desktop bundles and fresh installs

    Pure resolution: each call re-reads the env and filesystem, so
    anything that invokes this at DB-open time (``open_db``,
    ``Memory()``) picks up fixture-scoped ``ORBIS_DB_PATH`` overrides.
    The module-level ``DEFAULT_DB_PATH`` snapshot below is the one
    place we capture a value eagerly, and it's kept purely for
    backward compatibility with code that imports the constant.
    """
    override = os.environ.get("ORBIS_DB_PATH")
    if override:
        return override
    if Path("data").exists():
        return "data/orbis.sqlite"
    from agent.paths import get_db_path
    return str(get_db_path())


def __getattr__(name: str) -> str:
    """Module-level ``__getattr__`` (PEP 562) — resolves
    ``DEFAULT_DB_PATH`` on each attribute access instead of a single
    import-time snapshot. Matches the precedence rules of
    ``_default_db_path()`` above so ``from memory.db import
    DEFAULT_DB_PATH`` at any point during a session reflects the
    current env, not the env at module-load time."""
    if name == "DEFAULT_DB_PATH":
        return _default_db_path()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
SCHEMA_VERSION = 3


# ---------------------------------------------------------------------------
# Schema. Bump SCHEMA_VERSION + add migration branches in _migrate when
# adding / altering columns. Keep the CREATE TABLE statements idempotent
# (`IF NOT EXISTS`) so a fresh install reproduces the latest schema in
# one pass.
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS _meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Sessions: one row per voice session, written atomically at session end.
CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,
    started_at   TEXT NOT NULL,
    ended_at     TEXT NOT NULL,
    messages     TEXT NOT NULL,           -- JSON: [{role, content}]
    tool_calls   TEXT NOT NULL DEFAULT '[]',
    final_output TEXT,
    trace_id     TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_ended_at ON sessions(ended_at DESC);

-- Full-text on session content for retrieval at session-open.
CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts USING fts5(
    messages,
    final_output,
    content='sessions',
    content_rowid='rowid'
);

-- Facts: structured episodic/semantic memories extracted from conversations.
-- Bi-temporal: valid_at/invalid_at = event time, created_at/expired_at =
-- system time. confidence drives the 90-day half-life curator.
CREATE TABLE IF NOT EXISTS facts (
    id                TEXT PRIMARY KEY,   -- uuid
    subject           TEXT NOT NULL,      -- e.g. "user" or a named entity
    relation          TEXT NOT NULL,      -- e.g. "likes", "is_called"
    object            TEXT NOT NULL,      -- the value
    confidence        REAL NOT NULL DEFAULT 1.0,
    valid_at          TEXT,               -- when the fact became true (world)
    invalid_at        TEXT,               -- when the fact stopped being true
    created_at        TEXT NOT NULL,      -- when this row was written
    expired_at        TEXT,               -- when this row was superseded
    source_session_id TEXT,
    last_accessed     TEXT
);
CREATE INDEX IF NOT EXISTS idx_facts_subject ON facts(subject);
CREATE INDEX IF NOT EXISTS idx_facts_relation ON facts(relation);
CREATE INDEX IF NOT EXISTS idx_facts_confidence ON facts(confidence DESC);

-- FTS on fact bodies for hybrid retrieval.
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    subject, relation, object,
    content='facts', content_rowid='rowid'
);

-- Personality state — one row per axis, drift value in [-1, +1].
CREATE TABLE IF NOT EXISTS personality_axes (
    axis        TEXT PRIMARY KEY,
    value       REAL NOT NULL DEFAULT 0.0,
    updated_at  TEXT NOT NULL
);

-- Append-only log of personality drift events for auditing / viz.
CREATE TABLE IF NOT EXISTS personality_events (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    axis     TEXT NOT NULL,
    delta    REAL NOT NULL,
    reason   TEXT,
    at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_persona_events_at ON personality_events(at DESC);

-- Current mood state (valence / arousal / guardedness).
-- Singleton: one row with id=1.
CREATE TABLE IF NOT EXISTS mood (
    id           INTEGER PRIMARY KEY CHECK (id = 1),
    valence      REAL NOT NULL DEFAULT 0.0,
    arousal      REAL NOT NULL DEFAULT 0.0,
    guardedness  REAL NOT NULL DEFAULT 0.0,
    updated_at   TEXT NOT NULL
);

-- Local cache of Stripe entitlement verification.
CREATE TABLE IF NOT EXISTS entitlement_cache (
    key          TEXT PRIMARY KEY,
    value        TEXT NOT NULL,
    verified_at  TEXT NOT NULL,
    expires_at   TEXT NOT NULL
);

-- Inbox: messages pushed in by external systems (webhooks, cron,
-- other agents) that the voice agent can pull on demand. Read-then-
-- mark-delivered semantics — undelivered messages surface again on
-- the next check_inbox tool call until the agent (or an explicit
-- POST /api/inbox/deliver) marks them delivered.
--
-- ``priority`` mirrors the CC-2.18 inbox pattern: 'now' (auto-surface
-- at session start), 'next' (normal pull, default), 'later' (only
-- shown when the agent explicitly asks for everything). The
-- ingestor declares urgency; the agent (and the runtime) decide what
-- to do with it. CHECK constraint guards against typos in writes.
CREATE TABLE IF NOT EXISTS inbox (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT NOT NULL,        -- UTC ISO8601 of ingestion
    sender       TEXT NOT NULL,        -- 'system', 'webhook:slack', 'agent:ava', ...
    channel      TEXT,                 -- optional grouping ('alerts', 'todos', null)
    subject      TEXT NOT NULL,        -- short one-line summary
    body         TEXT NOT NULL,        -- the full message
    priority     TEXT NOT NULL DEFAULT 'next'
                 CHECK (priority IN ('now', 'next', 'later')),
    delivered_at TEXT                  -- nullable; set on first read by the agent
);
CREATE INDEX IF NOT EXISTS idx_inbox_created_at ON inbox(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_inbox_undelivered ON inbox(delivered_at) WHERE delivered_at IS NULL;
-- The priority index is created in _migrate so existing DBs whose
-- inbox table predates the priority column don't trip on a missing
-- column reference here. Fresh installs hit it through migration too
-- (current=0 → SCHEMA_VERSION).

-- Reminders — time-based proactive prompts the agent fires itself
-- ("remind me in 10 min to X"). The scheduler (agent/scheduler.py)
-- polls `fired_at IS NULL AND fire_at <= now` and delivers via the
-- DeliveryController. See orbis-2a0.
CREATE TABLE IF NOT EXISTS reminders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,        -- UTC ISO8601 of scheduling
    fire_at     TEXT NOT NULL,        -- UTC ISO8601 when it should fire
    text        TEXT NOT NULL,        -- what to say
    source      TEXT,                 -- optional attribution
    repeat_secs INTEGER,              -- nullable; if set, reschedules each fire (recurring)
    fired_at    TEXT                  -- nullable; set when a one-shot fires/drops
);
CREATE INDEX IF NOT EXISTS idx_reminders_pending
    ON reminders(fire_at) WHERE fired_at IS NULL;
"""


# ---------------------------------------------------------------------------
# Connection + migration.
# ---------------------------------------------------------------------------


def _probe_fts5(conn: sqlite3.Connection) -> None:
    """Raise RuntimeError if the sqlite3 build lacks FTS5."""
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts5_probe USING fts5(x)")
        conn.execute("DROP TABLE IF EXISTS _fts5_probe")
    except sqlite3.OperationalError as exc:
        raise RuntimeError(
            "SQLite FTS5 extension is unavailable in this Python build. "
            "Install a build of sqlite3 with FTS5 enabled."
        ) from exc


def open_db(path: str | Path | None = None) -> sqlite3.Connection:
    """Open (or create) the ORBIS memory database at ``path``.

    Applies pragmas friendly to single-writer embedded use:
    WAL journal mode, foreign keys enforced, row_factory=Row.

    When ``path`` is None, the path is re-resolved on each call (so
    tests that monkeypatch ``ORBIS_DB_PATH`` at runtime pick up the
    override) via ``_default_db_path``.
    """
    db_path = Path(path) if path else Path(_default_db_path())
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _probe_fts5(conn)
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create tables + apply migrations if the file is older than SCHEMA_VERSION."""
    conn.executescript(_SCHEMA)
    cur = conn.execute("SELECT value FROM _meta WHERE key = 'schema_version'")
    row = cur.fetchone()
    current = int(row["value"]) if row else 0

    if current == SCHEMA_VERSION:
        return

    if current < SCHEMA_VERSION:
        _migrate(conn, from_version=current, to_version=SCHEMA_VERSION)

    conn.execute(
        "INSERT OR REPLACE INTO _meta (key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()


def _migrate(conn: sqlite3.Connection, *, from_version: int, to_version: int) -> None:
    """Apply schema migrations. Called only when from_version < to_version.

    Each bump to SCHEMA_VERSION adds a new `if from_version < N: ...`
    branch here. SCHEMA_VERSION=1 is the initial shape so this is
    a no-op until we bump.
    """
    logger.info(
        f"[memory] schema migration {from_version} → {to_version}"
    )
    if from_version < 2:
        # v2 adds the inbox table + priority column. The CREATE TABLE
        # IF NOT EXISTS in _SCHEMA handles fresh installs; this branch
        # handles the case where an earlier dev DB created the inbox
        # table BEFORE the priority column existed (the executescript
        # above will no-op on an existing table). Add the column if
        # missing, then create the priority index.
        cur = conn.execute("PRAGMA table_info(inbox)")
        cols = {row["name"] for row in cur.fetchall()}
        if cols and "priority" not in cols:
            conn.execute(
                "ALTER TABLE inbox ADD COLUMN priority TEXT NOT NULL "
                "DEFAULT 'next' "
                "CHECK (priority IN ('now', 'next', 'later'))"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_inbox_priority "
            "ON inbox(priority, delivered_at)"
        )

    if from_version < 3:
        # v3 adds reminders.repeat_secs (recurring reminders, orbis-2t6).
        # The reminders table is created by IF NOT EXISTS in _SCHEMA; this
        # adds the column to a DB that created reminders before recurrence
        # existed (an interim build between C1 and C2).
        cur = conn.execute("PRAGMA table_info(reminders)")
        cols = {row["name"] for row in cur.fetchall()}
        if cols and "repeat_secs" not in cols:
            conn.execute("ALTER TABLE reminders ADD COLUMN repeat_secs INTEGER")


# ---------------------------------------------------------------------------
# Memory façade — groups the per-table DALs. Importers hold one Memory
# instance for the lifetime of the process.
# ---------------------------------------------------------------------------


class Memory:
    """Opens the DB and exposes per-table DALs as attributes."""

    def __init__(self, path: str | Path | None = None):
        self.conn = open_db(path)

        # Deferred imports to keep module-load cheap + avoid cycles.
        from .sessions import SessionsDAL
        from .facts import FactsDAL
        from .personality import PersonalityDAL
        from .entitlement import EntitlementDAL
        from .inbox import InboxDAL
        from .reminders import RemindersDAL

        self.sessions = SessionsDAL(self.conn)
        self.facts = FactsDAL(self.conn)
        self.personality = PersonalityDAL(self.conn)
        self.entitlement = EntitlementDAL(self.conn)
        self.inbox = InboxDAL(self.conn)
        self.reminders = RemindersDAL(self.conn)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Memory":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
