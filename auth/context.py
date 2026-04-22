"""Context vars for current user + session.

Every request / voice session / A2A turn runs in a context with a
``current_user_id`` and ``current_session_id`` set. Deep code (tracing
spans, session_store lookups, filler generators) reads these rather
than threading user_id through every call site.

Set:

  - ``require_user`` FastAPI dependency sets user on each HTTP request
  - ``run_bot`` sets user + session at the top of each voice connection
  - ``text_agent`` sets user + session per inbound A2A turn

Read:

  - ``agent/tracing.py`` — spans attach user_id + session_id automatically
  - ``agent/session_store.py`` — paths keyed on user_id
"""

from __future__ import annotations

from contextvars import ContextVar

current_user_id: ContextVar[str] = ContextVar("current_user_id", default="default")
current_session_id: ContextVar[str | None] = ContextVar("current_session_id", default=None)
