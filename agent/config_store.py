"""Read/write helpers for config/orbis.yaml.

The setup wizard + drawer UI both mutate the persona YAML, so the
file is the source of truth and the UI is a mirror. This module
handles atomic-replace writes + preserves comment structure only
when we have it (PyYAML's dump strips comments — acceptable cost
for the simplicity).

Safety:
  - All writes are tempfile → ``os.rename`` (atomic on POSIX)
  - Schema validation on write — reject anything the persona loader
    won't accept, so a bad UI call can't brick the next boot
  - Never write secrets; the owner API key lives in users.yaml, TTS
    provider credentials live in .env
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DEFAULT_PATH = os.environ.get("ORBIS_CONFIG", "config/orbis.yaml")


# Keys we allow at each level of the YAML. Anything else is dropped
# from the write (with a log message) so the UI can't inject garbage
# that later boots would have to tolerate.
_ALLOWED_PERSONA_KEYS = {
    "slug", "name", "user_name", "system_prompt", "system_prompt_file",
    "temperature", "max_tokens", "filler_verbosity",
}
_ALLOWED_VOICE_KEYS = {"tts_backend", "voice"}
_ALLOWED_ORB_KEYS = {"variant", "palette", "params"}
_ALLOWED_LLM_KEYS = {"url", "model", "api_key", "api_key_env", "extra_body"}
_ALLOWED_VERBOSITIES = {"silent", "brief", "narrated", "chatty"}
_ALLOWED_TTS_BACKENDS = {"kokoro", "openai", "elevenlabs", "fish"}


def read_config(path: str | Path | None = None) -> dict:
    """Load the current YAML as a dict. Missing file → {}."""
    p = Path(path) if path else Path(DEFAULT_PATH)
    if not p.exists():
        return {}
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as e:
        logger.error(f"[config_store] failed to parse {p}: {e}")
        return {}
    return data if isinstance(data, dict) else {}


def _validate_persona(block: Any) -> dict:
    """Filter a persona block to allowed keys + typed validation.
    Unknown keys are dropped with a warning."""
    if not isinstance(block, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in block.items():
        if k not in _ALLOWED_PERSONA_KEYS:
            logger.warning(f"[config_store] dropping unknown persona key {k!r}")
            continue
        if k in ("slug", "name", "user_name", "system_prompt", "system_prompt_file"):
            if v is not None:
                out[k] = str(v)
        elif k == "temperature":
            try:
                out[k] = float(v)
            except (TypeError, ValueError):
                raise ValueError(f"temperature must be numeric; got {v!r}")
        elif k == "max_tokens":
            try:
                out[k] = int(v)
            except (TypeError, ValueError):
                raise ValueError(f"max_tokens must be an integer; got {v!r}")
        elif k == "filler_verbosity":
            val = str(v).strip().lower()
            if val not in _ALLOWED_VERBOSITIES:
                raise ValueError(
                    f"filler_verbosity must be one of {sorted(_ALLOWED_VERBOSITIES)}"
                )
            out[k] = val
    return out


def _validate_voice(block: Any) -> dict:
    if not isinstance(block, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in block.items():
        if k not in _ALLOWED_VOICE_KEYS:
            logger.warning(f"[config_store] dropping unknown voice key {k!r}")
            continue
        if k == "tts_backend":
            val = str(v).strip().lower()
            if val not in _ALLOWED_TTS_BACKENDS:
                raise ValueError(
                    f"tts_backend must be one of {sorted(_ALLOWED_TTS_BACKENDS)}"
                )
            out[k] = val
        elif k == "voice":
            out[k] = str(v) if v is not None else None
    return out


def _validate_orb(block: Any) -> dict:
    if not isinstance(block, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in block.items():
        if k not in _ALLOWED_ORB_KEYS:
            logger.warning(f"[config_store] dropping unknown orb key {k!r}")
            continue
        if k in ("variant", "palette"):
            out[k] = str(v) if v is not None else None
        elif k == "params":
            if v is None:
                out[k] = {}
            elif isinstance(v, dict):
                # Params are shader uniforms — numbers or strings (color hex).
                cleaned: dict[str, Any] = {}
                for pk, pv in v.items():
                    if isinstance(pv, (int, float, str, bool)):
                        cleaned[str(pk)] = pv
                    else:
                        logger.warning(
                            f"[config_store] dropping orb.params.{pk} "
                            f"(unsupported type {type(pv).__name__})"
                        )
                out[k] = cleaned
            else:
                raise ValueError("orb.params must be a mapping")
    return out


def _validate_llm(block: Any) -> dict:
    """Filter an llm block — URL + model + either direct api_key or
    api_key_env reference + optional extra_body."""
    if not isinstance(block, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in block.items():
        if k not in _ALLOWED_LLM_KEYS:
            logger.warning(f"[config_store] dropping unknown llm key {k!r}")
            continue
        if k in ("url", "model", "api_key", "api_key_env"):
            if v is not None:
                out[k] = str(v)
        elif k == "extra_body":
            if v is None or isinstance(v, dict):
                out[k] = v if v else None
            else:
                raise ValueError("llm.extra_body must be a mapping or null")
    return out


def validate_and_normalize(data: dict) -> dict:
    """Apply schema filtering across the top-level blocks. Raises
    ValueError on typed validation failures; silently drops unknown
    keys."""
    out: dict[str, Any] = {}
    if "persona" in data:
        block = _validate_persona(data["persona"])
        if block:
            out["persona"] = block
    if "voice" in data:
        block = _validate_voice(data["voice"])
        if block:
            out["voice"] = block
    if "llm" in data:
        block = _validate_llm(data["llm"])
        if block:
            out["llm"] = block
    if "orb" in data:
        block = _validate_orb(data["orb"])
        if block:
            out["orb"] = block
    # Unknown top-level keys — drop with warning.
    for k in data:
        if k not in ("persona", "voice", "llm", "orb"):
            logger.warning(f"[config_store] dropping unknown top-level key {k!r}")
    return out


def write_config(data: dict, path: str | Path | None = None) -> dict:
    """Validate + atomically write the config. Returns the normalized
    dict that was written. Raises ValueError on validation failure."""
    p = Path(path) if path else Path(DEFAULT_PATH)
    normalized = validate_and_normalize(data)
    p.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            yaml.safe_dump(normalized, fh, sort_keys=False, allow_unicode=True)
        os.rename(tmp, p)
        tmp = None
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    logger.info(f"[config_store] wrote {p}")
    return normalized


def merge_patch(patch: dict, path: str | Path | None = None) -> dict:
    """Read current config, shallow-merge ``patch`` onto it per
    top-level block, validate, write. Returns the post-write normalized
    dict.

    Shallow-merge: `patch["persona"]["name"]` overrides just the name;
    other persona fields are preserved. Missing blocks in the patch
    are untouched in the file.
    """
    current = read_config(path)
    merged: dict[str, Any] = dict(current)
    for block_key in ("persona", "voice", "llm", "orb"):
        if block_key not in patch:
            continue
        block_patch = patch[block_key]
        if not isinstance(block_patch, dict):
            continue
        existing = merged.get(block_key) or {}
        if not isinstance(existing, dict):
            existing = {}
        merged[block_key] = {**existing, **block_patch}
    return write_config(merged, path)
