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


# Sentinel substituted for stored secret values in the GET /api/config
# response so the actual provider keys never leave the box (the UI only
# needs to know a key *is* set — it shows a "key saved" indicator and
# only sends a value when the user types a new one). merge_patch treats
# this exact value as "keep the existing secret" so a client that echoes
# the redacted block back can't wipe the saved key.
REDACTED_SECRET = "__redacted__"  # noqa: S105 — not a credential

# Top-level block + field pairs holding provider secrets, used for both
# redaction (app.py GET) and the keep-existing guard in merge_patch.
_SECRET_FIELDS = {
    "llm": ("api_key", "micro_api_key"),
    "stt": ("api_key",),
    "voice": ("tts_api_key",),
}
# llm.fallback.api_key is nested one level deeper than the flat
# block/field model above — redact_secrets + merge_patch handle it
# explicitly.

# Keys we allow at each level of the YAML. Anything else is dropped
# from the write (with a log message) so the UI can't inject garbage
# that later boots would have to tolerate.
_ALLOWED_PERSONA_KEYS = {
    "slug", "name", "user_name", "system_prompt", "system_prompt_file",
    "temperature", "max_tokens", "filler_verbosity",
    # Selection pointer to a persona FILE (agent/personas.py, epic
    # #611). Empty / "default" → the yaml persona as-is.
    "active_persona",
    # Advanced per-session behavior overrides (speaker_gate / backchannel /
    # micro_ack / bargein / audio_tags). Interpreted in app.py
    # (_resolve_behavior_block); config_store only round-trips the nested block
    # so a drawer save can't silently strip a hand-edited one.
    "behavior",
}
_ALLOWED_VOICE_KEYS = {
    "tts_backend", "voice", "tts_url", "tts_model", "tts_api_key",
    # Setup-wizard "voice models" choice: "on_device" (download Parakeet +
    # Kokoro) | "byo" (skip; user configures their own backend). Gates the
    # eager prewarm in app.py:prewarm_all.
    "local_models",
    # Listener-ack ("mm-hmm") on/off, surfaced in the Voice settings panel.
    # Only viable on the Fish backend — Kokoro's short-clip synthesis makes the
    # acks sound wrong — so the UI shows the toggle only when Fish is active and
    # the pipeline caps the feature to Fish regardless. See voice/pipeline.py.
    "backchannel",
}
_ALLOWED_ORB_KEYS = {"variant", "palette", "params", "state_overrides", "mood_overrides"}
# Must round-trip everything the persona loader (agent/persona.py)
# accepts off the llm block — a key missing here is stripped from
# orbis.yaml on the next UI save (#601's write-path twin).
_ALLOWED_LLM_KEYS = {
    "url", "model", "api_key", "api_key_env", "extra_body",
    "provider",
    "router_model", "content_model",
    "micro_url", "micro_model", "micro_api_key",
    "fallback",
}
_ALLOWED_LLM_FALLBACK_KEYS = {
    "url", "model", "api_key", "api_key_env", "provider", "extra_body",
}
_ALLOWED_STT_KEYS = {"backend", "whisper_model", "url", "model", "api_key"}
_ALLOWED_WAKEWORD_KEYS = {"enabled", "model", "threshold"}
# User-toggleable agent capabilities. `allow_orb_control` gates the
# `set_orb_visual` tool — when false the voice agent can't change the orb's
# variant/palette/params (the tool refuses). Default-on; the settings UI flips it.
_ALLOWED_AGENT_KEYS = {"allow_orb_control"}
# First-run wizard completion — the durable source of truth for "has setup been
# done". The frontend's localStorage flag is a fast-path cache that gets wiped by
# a rebuild / "clear browsing data"; this survives, so the wizard doesn't re-ask
# (and re-prewarm models) on an instance that's already configured.
_ALLOWED_SETUP_KEYS = {"complete"}
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
        if k in (
            "slug", "name", "user_name", "system_prompt",
            "system_prompt_file", "active_persona",
        ):
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
        elif k == "behavior":
            # Round-trip only — the nested behavior block (speaker_gate /
            # backchannel / micro_ack / bargein / audio_tags) is interpreted by
            # app.py, not here. Preserve it as-is so a UI save doesn't drop a
            # hand-edited block; a non-dict value is malformed, so skip it.
            if isinstance(v, dict):
                out[k] = v
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
        elif k == "backchannel":
            # Store a clean bool. Accept the on/1/true (and off/0/false) string
            # forms too so a hand-edited YAML value works the same as the UI's
            # JSON boolean.
            if isinstance(v, bool):
                out[k] = v
            else:
                out[k] = str(v).strip().lower() in ("1", "true", "on", "yes")
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


def _validate_llm_fallback(block: Any) -> dict:
    """Filter the nested llm.fallback block — same shape as llm itself
    minus the routing/micro tiers (a backup endpoint is one URL)."""
    if not isinstance(block, dict):
        raise ValueError("llm.fallback must be a mapping")
    out: dict[str, Any] = {}
    for k, v in block.items():
        if k not in _ALLOWED_LLM_FALLBACK_KEYS:
            logger.warning(f"[config_store] dropping unknown llm.fallback key {k!r}")
            continue
        if k == "extra_body":
            if v is None or isinstance(v, dict):
                out[k] = v if v else None
            else:
                raise ValueError("llm.fallback.extra_body must be a mapping or null")
        elif v is not None:
            out[k] = str(v)
    return out


def _validate_llm(block: Any) -> dict:
    """Filter an llm block — URL + model + either direct api_key or
    api_key_env reference + optional extra_body, plus the provider /
    two-model-routing / micro-tier strings and the nested failover
    ``fallback`` block."""
    if not isinstance(block, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in block.items():
        if k not in _ALLOWED_LLM_KEYS:
            logger.warning(f"[config_store] dropping unknown llm key {k!r}")
            continue
        if k == "extra_body":
            if v is None or isinstance(v, dict):
                out[k] = v if v else None
            else:
                raise ValueError("llm.extra_body must be a mapping or null")
        elif k == "fallback":
            if v is None:
                continue
            fb = _validate_llm_fallback(v)
            if fb:
                out[k] = fb
        elif v is not None:
            out[k] = str(v)
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


def _validate_setup(block: Any) -> dict:
    """Filter a setup block: just the durable first-run `complete` flag."""
    if not isinstance(block, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in block.items():
        if k not in _ALLOWED_SETUP_KEYS:
            logger.warning(f"[config_store] dropping unknown setup key {k!r}")
            continue
        if k == "complete":
            out[k] = bool(v)
    return out


def _validate_agent(block: Any) -> dict:
    """Filter an agent block: user-toggleable agent capabilities."""
    if not isinstance(block, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in block.items():
        if k not in _ALLOWED_AGENT_KEYS:
            logger.warning(f"[config_store] dropping unknown agent key {k!r}")
            continue
        if k == "allow_orb_control":
            out[k] = bool(v)
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
    if "setup" in data:
        block = _validate_setup(data["setup"])
        if block:
            out["setup"] = block
    if "agent" in data:
        block = _validate_agent(data["agent"])
        if block:
            out["agent"] = block
    # Unknown top-level keys — drop with warning.
    for k in data:
        if k not in ("persona", "voice", "llm", "stt", "orb", "wakeword", "setup", "agent"):
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
    for block_key in ("persona", "voice", "llm", "stt", "orb", "wakeword", "setup", "agent"):
        if block_key not in patch:
            continue
        block_patch = patch[block_key]
        if not isinstance(block_patch, dict):
            continue
        existing = merged.get(block_key) or {}
        if not isinstance(existing, dict):
            existing = {}
        block_patch = dict(block_patch)
        # Keep-existing on redacted secrets: a client may echo back the
        # GET-redacted block (api_key == REDACTED_SECRET); never let that
        # overwrite the real stored key.
        for field in _SECRET_FIELDS.get(block_key, ()):
            if block_patch.get(field) == REDACTED_SECRET:
                block_patch.pop(field, None)
        # Same guard one level deeper for llm.fallback.api_key — the
        # shallow block merge replaces the fallback dict wholesale, so
        # re-inject the stored key when the patch echoes the redaction.
        if block_key == "llm" and isinstance(block_patch.get("fallback"), dict):
            fb_patch = dict(block_patch["fallback"])
            if fb_patch.get("api_key") == REDACTED_SECRET:
                fb_patch.pop("api_key", None)
                fb_existing = existing.get("fallback")
                if isinstance(fb_existing, dict) and fb_existing.get("api_key"):
                    fb_patch["api_key"] = fb_existing["api_key"]
            block_patch["fallback"] = fb_patch
        merged[block_key] = {**existing, **block_patch}
    return write_config(merged, path)


def redact_secrets(cfg: dict) -> dict:
    """Return a shallow copy of ``cfg`` with stored provider secrets
    replaced by ``REDACTED_SECRET`` (only when a non-empty value is set).
    Used by GET /api/config so keys never leave the box."""
    out = dict(cfg)
    for block_key, fields in _SECRET_FIELDS.items():
        block = out.get(block_key)
        if not isinstance(block, dict):
            continue
        block = dict(block)
        for field in fields:
            if block.get(field):
                block[field] = REDACTED_SECRET
        out[block_key] = block
    # Nested: llm.fallback.api_key (see _SECRET_FIELDS note).
    llm = out.get("llm")
    if isinstance(llm, dict) and isinstance(llm.get("fallback"), dict):
        fb = dict(llm["fallback"])
        if fb.get("api_key"):
            fb["api_key"] = REDACTED_SECRET
        llm = dict(llm)
        llm["fallback"] = fb
        out["llm"] = llm
    return out
