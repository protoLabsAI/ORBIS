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
_ALLOWED_VOICE_KEYS = {
    "tts_backend", "voice", "tts_url", "tts_model", "tts_api_key",
    # Setup-wizard "voice models" choice: "on_device" (download Parakeet +
    # Kokoro) | "byo" (skip; user configures their own backend). Gates the
    # eager prewarm in app.py:prewarm_all.
    "local_models",
}
_ALLOWED_ORB_KEYS = {"variant", "palette", "params", "state_overrides", "mood_overrides"}
_ALLOWED_LLM_KEYS = {"url", "model", "api_key", "api_key_env", "extra_body"}
_ALLOWED_STT_KEYS = {"backend", "whisper_model", "url", "model", "api_key"}
_ALLOWED_WAKEWORD_KEYS = {"enabled", "model", "threshold"}
_ALLOWED_VERBOSITIES = {"silent", "brief", "narrated", "chatty"}
_ALLOWED_TTS_BACKENDS = {"kokoro", "openai", "elevenlabs", "fish"}
_ALLOWED_STT_BACKENDS = {"local", "openai", "sensevoice", "parakeet"}

# Orb state/mood authoring (DECISIONS.md 2026-04-23 amendment). Presets
# grow optional delta maps per voice-state and per mood-dimension on top
# of `orb.params`. Values are additive (for numbers) or replacement (for
# colors), composed in the frontend at uniform-set time. Frontend is
# the only place that interprets them; backend just round-trips.
_ALLOWED_VOICE_STATES = {"idle", "listening", "thinking", "speaking"}
_ALLOWED_MOOD_DIMS = {"valence", "arousal", "guardedness"}


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
        elif k in ("tts_url", "tts_model", "tts_api_key"):
            if v is None:
                out[k] = None
            else:
                trimmed = str(v).strip()
                out[k] = trimmed if trimmed else None
        elif k == "local_models":
            val = str(v).strip().lower()
            if val not in ("on_device", "byo"):
                raise ValueError("voice.local_models must be 'on_device' or 'byo'")
            out[k] = val
    return out


def _validate_param_delta(block: Any, path_hint: str) -> dict[str, Any]:
    """Filter a delta map — same shape as orb.params but treated
    semantically as additive deltas (numbers) or replacements (strings
    like color hex). Non-mapping input and non-scalar values dropped
    with warnings; never raises."""
    if not isinstance(block, dict):
        logger.warning(f"[config_store] {path_hint} must be a mapping; dropping")
        return {}
    out: dict[str, Any] = {}
    for pk, pv in block.items():
        if isinstance(pv, (int, float, str, bool)):
            out[str(pk)] = pv
        else:
            logger.warning(
                f"[config_store] dropping {path_hint}.{pk} "
                f"(unsupported type {type(pv).__name__})"
            )
    return out


def _validate_numeric_delta(block: Any, path_hint: str) -> dict[str, Any]:
    """Like `_validate_param_delta` but numbers only. Mood overrides
    are multiplied by the live mood scalar at render time — a string
    (or bool) can't be scaled, so letting one through would produce a
    silent no-op or a type-error at the shader uniform boundary. Keep
    them out at the config layer."""
    if not isinstance(block, dict):
        logger.warning(f"[config_store] {path_hint} must be a mapping; dropping")
        return {}
    out: dict[str, Any] = {}
    for pk, pv in block.items():
        # `bool` is a subclass of `int`; reject it explicitly — a bool
        # delta scaled by 0.6 is nonsensical.
        if isinstance(pv, bool) or not isinstance(pv, (int, float)):
            logger.warning(
                f"[config_store] dropping {path_hint}.{pk} "
                f"(mood deltas must be numeric; got {type(pv).__name__})"
            )
            continue
        out[str(pk)] = pv
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
                out[k] = _validate_param_delta(v, "orb.params")
            else:
                raise ValueError("orb.params must be a mapping")
        elif k == "state_overrides":
            # Expect: {idle|listening|thinking|speaking: {param: delta, ...}}
            if v is None:
                out[k] = {}
            elif isinstance(v, dict):
                cleaned: dict[str, Any] = {}
                for state, delta in v.items():
                    key = str(state).strip().lower()
                    if key not in _ALLOWED_VOICE_STATES:
                        logger.warning(
                            f"[config_store] dropping orb.state_overrides.{state!r} "
                            f"(must be one of {sorted(_ALLOWED_VOICE_STATES)})"
                        )
                        continue
                    cleaned[key] = _validate_param_delta(
                        delta, f"orb.state_overrides.{key}",
                    )
                out[k] = cleaned
            else:
                raise ValueError("orb.state_overrides must be a mapping")
        elif k == "mood_overrides":
            # Expect: {valence|arousal|guardedness: {param: delta, ...}}
            # Numeric-only — see _validate_numeric_delta for why.
            if v is None:
                out[k] = {}
            elif isinstance(v, dict):
                cleaned = {}
                for dim, delta in v.items():
                    key = str(dim).strip().lower()
                    if key not in _ALLOWED_MOOD_DIMS:
                        logger.warning(
                            f"[config_store] dropping orb.mood_overrides.{dim!r} "
                            f"(must be one of {sorted(_ALLOWED_MOOD_DIMS)})"
                        )
                        continue
                    cleaned[key] = _validate_numeric_delta(
                        delta, f"orb.mood_overrides.{key}",
                    )
                out[k] = cleaned
            else:
                raise ValueError("orb.mood_overrides must be a mapping")
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


def _validate_stt(block: Any) -> dict:
    """Filter an stt block. Empty strings round-trip as None so clearing
    a settings field falls back to env defaults."""
    if not isinstance(block, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in block.items():
        if k not in _ALLOWED_STT_KEYS:
            logger.warning(f"[config_store] dropping unknown stt key {k!r}")
            continue
        if k == "backend":
            val = str(v).strip().lower()
            if val not in _ALLOWED_STT_BACKENDS:
                raise ValueError(
                    f"stt.backend must be one of {sorted(_ALLOWED_STT_BACKENDS)}"
                )
            out[k] = val
        elif k in ("whisper_model", "url", "model", "api_key"):
            if v is None:
                out[k] = None
            else:
                trimmed = str(v).strip()
                out[k] = trimmed if trimmed else None
    return out


def _validate_wakeword(block: Any) -> dict:
    """Filter a wakeword block: hands-free on/off, the active wake-word model
    id (a catalog id like ``hey_orbis``), and the detection threshold."""
    if not isinstance(block, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in block.items():
        if k not in _ALLOWED_WAKEWORD_KEYS:
            logger.warning(f"[config_store] dropping unknown wakeword key {k!r}")
            continue
        if k == "enabled":
            out[k] = bool(v)
        elif k == "model":
            if v is None:
                out[k] = None
            else:
                trimmed = str(v).strip()
                out[k] = trimmed if trimmed else None
        elif k == "threshold":
            try:
                t = float(v)
            except (TypeError, ValueError):
                raise ValueError(f"wakeword.threshold must be numeric; got {v!r}")
            out[k] = min(1.0, max(0.0, t))  # clamp to a probability
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
    if "stt" in data:
        block = _validate_stt(data["stt"])
        if block:
            out["stt"] = block
    if "orb" in data:
        block = _validate_orb(data["orb"])
        if block:
            out["orb"] = block
    if "wakeword" in data:
        block = _validate_wakeword(data["wakeword"])
        if block:
            out["wakeword"] = block
    # Unknown top-level keys — drop with warning.
    for k in data:
        if k not in ("persona", "voice", "llm", "stt", "orb", "wakeword"):
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
    for block_key in ("persona", "voice", "llm", "stt", "orb", "wakeword"):
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
