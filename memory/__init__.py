"""SQLite-backed memory + state for ORBIS.

Single-file embedded store holding:

  - **sessions** — one row per voice session, persisted at session end.
  - **facts** — structured memories extracted from conversations, with
    bi-temporal (valid_at / invalid_at) + confidence fields and a
    90-day half-life decay curator. Graphiti-shaped but SQL-native.
  - **personality_axes** — slow-drift personality state (playful,
    warm, sarcastic, etc.). One row per axis, updated after sessions.
  - **personality_events** — append-only log of drift events for
    auditing / visualization.
  - **mood** — short-term emotional weather (valence / arousal /
    guardedness). Updated continuously.

FTS5 virtual tables give hybrid BM25 retrieval on session text and
fact objects without a vector-DB dependency. ``sqlite-vec`` can be
added later for semantic search if BM25 proves insufficient.

Layout:

    memory/
      __init__.py  — package re-exports
      db.py        — connection + schema creation + migrations
      sessions.py  — session add / list / search / prior-N
      facts.py     — facts add / search / decay / prune
      personality.py — axes + events + mood DALs

Import the DALs from the package root:

    from memory import Memory
    m = Memory()                          # opens data/orbis.sqlite by default
    m.sessions.add(session_id, messages, final_output, trace_id)
    recent = m.sessions.prior_n(10)
    m.facts.add("user", "likes", "jazz", confidence=0.9)
"""

from .db import Memory, open_db, DEFAULT_DB_PATH

__all__ = ["Memory", "open_db", "DEFAULT_DB_PATH"]
