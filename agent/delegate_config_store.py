"""Read/write helpers for ``config/delegates.yaml``.

The Delegates Settings panel (PR A2) mutates this file via the API,
so YAML stays the source of truth and the UI is a thin mirror —
identical posture to ``agent/config_store.py`` for orbis.yaml.

Safety:
  - All writes are tempfile → ``os.rename`` (atomic on POSIX)
  - Schema validation on write — same rules as
    ``agent/delegates.py:_parse_entry`` apply so a malformed UI POST
    can't brick the next boot's registry load
  - **No credentials are ever stored or returned.** The schema only
    references env-var *names* (``credentialsEnv`` / ``api_key_env``);
    the actual values are pulled from the process environment at
    registry-parse time. This makes the entire YAML safe to round-trip
    through the API without redaction.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

import yaml

# DelegateValidationError + the per-type field/key schema live with the adapters
# now (one place per delegate type). Re-exported here so existing imports
# (`from agent.delegate_config_store import DelegateValidationError`) keep working.
from agent.delegate_adapters import (
    DelegateValidationError,
    all_adapter_types,
    get_adapter,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DelegateValidationError",
    "read_delegates",
    "validate_entry",
    "write_delegates",
    "upsert_delegate",
    "delete_delegate",
    "DEFAULT_PATH",
]

# Resolve the SAME way the live DelegateRegistry does (app.py reads
# DELEGATES_YAML) so the /api/delegates read/write endpoints touch the exact
# file the registry loads — otherwise the UI shows zero delegates while the
# registry has them. ORBIS_DELEGATES_CONFIG still wins if explicitly set.
DEFAULT_PATH = (
    os.environ.get("ORBIS_DELEGATES_CONFIG")
    or os.environ.get("DELEGATES_YAML")
    or "config/delegates.yaml"
)

def read_delegates(path: str | Path | None = None) -> list[dict]:
    """Return the list of raw delegate entries from disk. Empty list
    when the file is missing — the registry treats that the same as
    "no delegates configured." Caller is responsible for calling
    validate_entry() if it cares about correctness."""
    p = Path(path) if path else Path(DEFAULT_PATH)
    if not p.exists():
        return []
    try:
        data = yaml.safe_load(p.read_text()) or {}
    except yaml.YAMLError as e:
        # Don't silently truncate the user's config — surface the parse
        # error so the API caller can decide what to do.
        raise DelegateValidationError(f"YAML parse error in {p}: {e}") from e
    raw = data.get("delegates")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise DelegateValidationError(
            f"{p}: top-level `delegates:` must be a list, got {type(raw).__name__}"
        )
    out: list[dict] = []
    for entry in raw:
        if isinstance(entry, dict):
            out.append(entry)
        else:
            logger.warning(
                f"[delegate_config_store] skipping non-dict entry: {entry!r}"
            )
    return out


def validate_entry(entry: dict) -> dict:
    """Apply the same schema checks the runtime registry uses, return a
    normalized dict. Raises DelegateValidationError with a user-readable
    message on any failure.

    Generic checks (object / name / known-type / description) live here; the
    per-type normalization is the registered adapter's ``validate()`` (one
    place per delegate type — see ``agent/delegate_adapters.py``).

    The runtime registry (``agent.delegates._parse_entry``) silently skips
    invalid entries; for the API path we want loud failures so the UI can
    surface them.
    """
    if not isinstance(entry, dict):
        raise DelegateValidationError(
            f"entry must be an object, got {type(entry).__name__}"
        )

    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        raise DelegateValidationError("`name` is required and must be a non-empty string")
    name = name.strip()

    dtype = entry.get("type")
    adapter = None
    if isinstance(dtype, str):
        try:
            adapter = get_adapter(dtype)
        except KeyError:
            adapter = None
    if adapter is None:
        raise DelegateValidationError(
            f"`type` must be one of {sorted(all_adapter_types())}, got {dtype!r}"
        )

    description = entry.get("description")
    if not isinstance(description, str) or not description.strip():
        raise DelegateValidationError(
            "`description` is required and must be a non-empty string — the LLM "
            "uses this to choose between delegates"
        )

    return adapter.validate(entry, name, description.strip())


def write_delegates(
    entries: list[dict], path: str | Path | None = None,
) -> list[dict]:
    """Atomically write the given list of (validated) entries to disk.

    Each entry is run through validate_entry() — partial failures abort
    the whole write so the file never contains a half-applied state.
    Returns the normalized entries that were persisted.
    """
    p = Path(path) if path else Path(DEFAULT_PATH)
    normalized = [validate_entry(e) for e in entries]

    # Catch duplicate names early — the registry's dict-keyed by name
    # would silently let the last duplicate win, but the UI should be
    # told about the collision so it can prompt the user.
    seen: set[str] = set()
    for e in normalized:
        if e["name"] in seen:
            raise DelegateValidationError(
                f"duplicate delegate name {e['name']!r} in payload"
            )
        seen.add(e["name"])

    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            yaml.safe_dump(
                {"delegates": normalized}, fh,
                sort_keys=False, allow_unicode=True,
            )
        os.rename(tmp, p)
        tmp = None
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    logger.info(f"[delegate_config_store] wrote {len(normalized)} delegate(s) to {p}")
    return normalized


def upsert_delegate(
    entry: dict, path: str | Path | None = None,
) -> list[dict]:
    """Add ``entry`` to the file, or replace an existing entry with the
    same name. Returns the post-write delegate list.

    Used by both POST (create) and PUT (update) since semantics are
    the same on disk — the API surface chooses the HTTP verb based on
    whether the name already exists.
    """
    normalized = validate_entry(entry)
    current = read_delegates(path)
    out: list[dict] = [e for e in current if e.get("name") != normalized["name"]]
    out.append(normalized)
    return write_delegates(out, path)


def delete_delegate(
    name: str, path: str | Path | None = None,
) -> list[dict]:
    """Remove the named delegate from the file. Returns the post-write
    delegate list. Raises KeyError if the name doesn't exist — caller
    maps to HTTP 404."""
    current = read_delegates(path)
    filtered = [e for e in current if e.get("name") != name]
    if len(filtered) == len(current):
        raise KeyError(name)
    return write_delegates(filtered, path)
