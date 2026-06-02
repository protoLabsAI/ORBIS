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
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Resolve the SAME way the live DelegateRegistry does (app.py reads
# DELEGATES_YAML) so the /api/delegates read/write endpoints touch the exact
# file the registry loads — otherwise the UI shows zero delegates while the
# registry has them. ORBIS_DELEGATES_CONFIG still wins if explicitly set.
DEFAULT_PATH = (
    os.environ.get("ORBIS_DELEGATES_CONFIG")
    or os.environ.get("DELEGATES_YAML")
    or "config/delegates.yaml"
)

# Allowed top-level keys per delegate type. Anything outside the union
# is dropped on write with a log message — keeps the UI from injecting
# fields the registry parser would silently ignore.
_COMMON_KEYS = {"name", "type", "description", "url"}
_A2A_KEYS = {"auth", "headers"}
_ACP_KEYS = {"command", "args", "workdir"}
_OPENAI_KEYS = {
    "model", "api_key_env", "system_prompt", "max_tokens", "temperature",
}


class DelegateValidationError(ValueError):
    """Raised when a delegate entry fails schema validation. Caller
    should map to HTTP 400."""


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
    """Apply the same schema checks the runtime registry uses, return
    a normalized dict. Raises DelegateValidationError with a
    user-readable message on any failure.

    The runtime registry (``agent.delegates._parse_entry``) silently
    skips invalid entries; for the API path we want loud failures so
    the UI can surface them.
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
    if dtype not in ("a2a", "openai", "acp"):
        raise DelegateValidationError(
            f"`type` must be 'a2a', 'openai', or 'acp', got {dtype!r}"
        )

    description = entry.get("description")
    if not isinstance(description, str) or not description.strip():
        raise DelegateValidationError(
            "`description` is required and must be a non-empty string — the LLM "
            "uses this to choose between delegates"
        )

    # type=acp — a local coding agent ORBIS launches + drives. Needs command +
    # workdir, NOT a url (handled before the url requirement below).
    if dtype == "acp":
        command = entry.get("command")
        if not isinstance(command, str) or not command.strip():
            raise DelegateValidationError(
                "`command` is required for acp delegates (the agent binary, e.g. 'proto')"
            )
        workdir = entry.get("workdir")
        if not isinstance(workdir, str) or not workdir.strip():
            raise DelegateValidationError(
                "`workdir` is required for acp delegates (the directory the agent works in)"
            )
        raw_args = entry.get("args") or []
        if not isinstance(raw_args, list):
            raise DelegateValidationError("`args` must be a list of strings")
        out: dict[str, Any] = {
            "name": name,
            "type": dtype,
            "description": description.strip(),
            "command": command.strip(),
            "args": [str(a) for a in raw_args],
            "workdir": workdir.strip(),
        }
        for k in entry:
            if k not in _COMMON_KEYS | _ACP_KEYS:
                logger.warning(
                    f"[delegate_config_store] {name}: dropping unknown acp key {k!r}"
                )
        return out

    url = entry.get("url")
    if not isinstance(url, str) or not url.strip():
        raise DelegateValidationError("`url` is required")

    out: dict[str, Any] = {
        "name": name,
        "type": dtype,
        "description": description.strip(),
        "url": url.strip(),
    }

    if dtype == "a2a":
        auth = entry.get("auth")
        if auth is not None:
            if not isinstance(auth, dict):
                raise DelegateValidationError("`auth` must be an object when present")
            scheme = auth.get("scheme")
            if scheme is not None and scheme not in ("apiKey", "bearer"):
                raise DelegateValidationError(
                    f"`auth.scheme` must be 'apiKey' or 'bearer', got {scheme!r}"
                )
            cred_env = auth.get("credentialsEnv")
            if cred_env is not None and not isinstance(cred_env, str):
                raise DelegateValidationError("`auth.credentialsEnv` must be a string env-var name")
            out["auth"] = {
                k: v for k, v in {"scheme": scheme, "credentialsEnv": cred_env}.items()
                if v is not None
            }
        headers = entry.get("headers")
        if headers is not None:
            if not isinstance(headers, dict):
                raise DelegateValidationError("`headers` must be an object")
            out["headers"] = {str(k): str(v) for k, v in headers.items()}
        for k in entry:
            if k not in _COMMON_KEYS | _A2A_KEYS:
                logger.warning(
                    f"[delegate_config_store] {name}: dropping unknown a2a key {k!r}"
                )
        return out

    # type=openai
    model = entry.get("model")
    if not isinstance(model, str) or not model.strip():
        raise DelegateValidationError(
            "`model` is required for openai delegates"
        )
    out["model"] = model.strip()

    api_key_env = entry.get("api_key_env")
    if api_key_env is not None:
        if not isinstance(api_key_env, str):
            raise DelegateValidationError("`api_key_env` must be a string env-var name")
        out["api_key_env"] = api_key_env

    system_prompt = entry.get("system_prompt")
    if system_prompt is not None:
        if not isinstance(system_prompt, str):
            raise DelegateValidationError("`system_prompt` must be a string")
        out["system_prompt"] = system_prompt

    max_tokens = entry.get("max_tokens")
    if max_tokens is not None:
        try:
            out["max_tokens"] = int(max_tokens)
        except (TypeError, ValueError) as e:
            raise DelegateValidationError(f"`max_tokens` must be an int: {e}") from e
        if out["max_tokens"] <= 0:
            raise DelegateValidationError("`max_tokens` must be > 0")

    temperature = entry.get("temperature")
    if temperature is not None:
        try:
            out["temperature"] = float(temperature)
        except (TypeError, ValueError) as e:
            raise DelegateValidationError(f"`temperature` must be a number: {e}") from e

    for k in entry:
        if k not in _COMMON_KEYS | _OPENAI_KEYS:
            logger.warning(
                f"[delegate_config_store] {name}: dropping unknown openai key {k!r}"
            )
    return out


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
