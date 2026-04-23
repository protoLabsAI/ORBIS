"""Single-persona loader.

ORBIS ships with one persona per install — ``config/orbis.yaml`` is the
source of truth. The persona composes the voice agent's identity:
system prompt, TTS voice, LLM tuning knobs, starter orb visual defaults.

Replaces the skills catalog the seed inherited from protoVoice. Re-read
on demand via ``reload_persona`` when the file changes.

Shape of ``config/orbis.yaml``::

    persona:
      slug: orbis
      name: ORBIS
      user_name: Alice          # how the orb addresses the user
      system_prompt: |
        You are ORBIS — an AI companion that speaks to the user...
      # Optional: point at an external file instead of inlining the prompt.
      # system_prompt_file: config/persona.md   # relative to config dir

      temperature: 0.7
      max_tokens: 150
      filler_verbosity: brief      # silent | brief | narrated | chatty

    voice:
      # TTS provider overrides (optional). When unset, the env defaults
      # from TTS_BACKEND / KOKORO_VOICE / etc. are used.
      tts_backend: kokoro
      voice: af_heart

    llm:
      # The small/fast LLM that drives the voice agent's routing +
      # personality layer. Unset → falls back to the env defaults
      # (LLM_URL / LLM_SERVED_NAME / LLM_API_KEY). Use api_key_env for
      # env-var indirection; api_key for direct storage.
      url: https://api.openai.com/v1
      model: gpt-4o-mini
      api_key_env: OPENAI_API_KEY
      # api_key: sk-...   (direct; less secure, but wizard writes here
      #                    when the user pastes a key)

    orb:
      # Starter orb viz — applied on first boot. Once entitled, the user
      # can change these via voice / drawer.
      variant: fractal
      palette: Aurora
      params:
        density: 2.0
        speed: 0.55

Env overrides win when set:
  - ``SYSTEM_PROMPT`` overrides ``persona.system_prompt``
  - ``TTS_BACKEND`` / ``KOKORO_VOICE`` override ``voice.*``
  - ``LLM_URL`` / ``LLM_SERVED_NAME`` / ``LLM_API_KEY`` are the fallback
    when the ``llm`` block is absent; explicit ``llm`` block values
    always win (lets a packaged install ship with the config user-
    editable and not rely on env-var plumbing).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


def _normalize_override_map(
    raw: Any,
    *,
    allowed: set[str],
    numeric_only: bool,
) -> dict:
    """Lowercase + filter the outer key (voice-state or mood-dim) and
    the inner param-delta dict. Keys outside `allowed` are dropped;
    the value dict drops non-scalar entries. If `numeric_only` is set,
    strings and bools in the inner dict are also dropped — mood deltas
    get multiplied by a scalar at render time and can't be non-numeric.
    Mirrors `agent.config_store._validate_orb` semantics for the
    read path so hand-authored YAML can't bypass it."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict] = {}
    for k, v in raw.items():
        key = str(k).strip().lower()
        if key not in allowed:
            logger.warning(
                f"[persona] dropping orb override key {k!r} "
                f"(not in {sorted(allowed)})"
            )
            continue
        if not isinstance(v, dict):
            logger.warning(
                f"[persona] orb override {key!r} must be a mapping; dropping"
            )
            continue
        inner: dict[str, Any] = {}
        for pk, pv in v.items():
            if numeric_only:
                if isinstance(pv, bool) or not isinstance(pv, (int, float)):
                    continue
            elif not isinstance(pv, (int, float, str, bool)):
                continue
            inner[str(pk)] = pv
        out[key] = inner
    return out


@dataclass(frozen=True)
class Persona:
    """The single ORBIS persona loaded from config/orbis.yaml."""

    slug: str = "orbis"
    name: str = "ORBIS"
    # How the orb addresses the user. Empty = no name injected.
    user_name: str = ""
    # Persona identity — composed into the voice agent's system prompt.
    system_prompt: str = ""
    # Decoder-side LLM knobs.
    temperature: float = 0.7
    max_tokens: int = 150
    # Baseline verbosity for filler generation.
    filler_verbosity: str = "brief"
    # Voice + TTS provider overrides.
    tts_backend: str | None = None
    voice: str | None = None
    # Starter orb visual state — (variant, palette, params).
    orb_variant: str | None = None
    orb_palette: str | None = None
    orb_params: dict = field(default_factory=dict)
    # Authoring deltas per voice state + mood dimension (DECISIONS.md
    # 2026-04-23). Composed on top of orb_params in the frontend at
    # uniform-set time; backend just carries them through.
    orb_state_overrides: dict = field(default_factory=dict)
    orb_mood_overrides: dict = field(default_factory=dict)

    # Retained for compatibility with the skills-era call signature
    # used by _effective_prompt / delegate routing. Always empty for
    # ORBIS (delegation targets are global, not persona-scoped).
    delegates: list | None = None
    behavior: dict = field(default_factory=dict)
    # llm: dict with shape {url, model, api_key, api_key_env}. Unset →
    # run_bot falls back to env defaults. Set from the setup wizard's
    # LLM-provider step, writable via /api/config.
    llm: dict | None = None
    tools: list = field(default_factory=list)

    @property
    def viz(self) -> dict:
        """Compatibility shim matching the old Skill.viz shape."""
        return {
            "variant": self.orb_variant,
            "palette": self.orb_palette,
            "params": dict(self.orb_params),
        }


_DEFAULT_SYSTEM_PROMPT = (
    "You are ORBIS — an AI companion. You're primarily a router to the "
    "user's configured agents via the delegate_to tool; you chat, remember, "
    "and have personality, but heavy reasoning you hand off. Keep replies "
    "brief, warm, and spoken aloud."
)


def _resolve_prompt(
    persona_block: dict, config_dir: Path,
) -> str:
    """Resolve persona.system_prompt, preferring env override → file → inline."""
    env_override = os.environ.get("SYSTEM_PROMPT")
    if env_override:
        return env_override.strip()

    file_ref = persona_block.get("system_prompt_file")
    if file_ref:
        path = Path(file_ref)
        if not path.is_absolute():
            path = config_dir / path
        if path.exists():
            try:
                return path.read_text(encoding="utf-8").strip()
            except Exception as e:
                logger.warning(f"[persona] failed to read {path}: {e}")

    inline = persona_block.get("system_prompt")
    if inline:
        return str(inline).strip()

    logger.info("[persona] no system_prompt configured; using baked-in default")
    return _DEFAULT_SYSTEM_PROMPT


def load_persona(config_path: str | Path = "config/orbis.yaml") -> Persona:
    """Load the single persona from ``config_path``. Returns a Persona
    with baked-in defaults if the file is missing or unreadable.

    Never raises — first-boot and misconfiguration should produce a
    working (if bland) persona rather than crashing the boot path.
    """
    path = Path(config_path)
    config_dir = path.parent if path.parent.parts else Path("config")

    if not path.exists():
        logger.info(f"[persona] {path} not found; using defaults")
        return Persona(system_prompt=_DEFAULT_SYSTEM_PROMPT)

    try:
        raw = path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw) or {}
    except Exception as e:
        logger.error(f"[persona] failed to read/parse {path}: {e}")
        return Persona(system_prompt=_DEFAULT_SYSTEM_PROMPT)

    if not isinstance(data, dict):
        logger.warning(f"[persona] {path} is not a mapping; using defaults")
        return Persona(system_prompt=_DEFAULT_SYSTEM_PROMPT)

    persona_block = data.get("persona") or {}
    voice_block = data.get("voice") or {}
    orb_block = data.get("orb") or {}
    llm_block = data.get("llm") or {}

    slug = (persona_block.get("slug") or "orbis").strip()
    name = (persona_block.get("name") or "ORBIS").strip()
    user_name = (persona_block.get("user_name") or "").strip()
    system_prompt = _resolve_prompt(persona_block, config_dir)

    # Env-win over config for TTS selection so the same YAML file works
    # across deployments with different TTS setups.
    tts_backend = os.environ.get("TTS_BACKEND") or voice_block.get("tts_backend")
    voice = os.environ.get("KOKORO_VOICE") or voice_block.get("voice")

    try:
        temperature = float(
            persona_block.get("temperature", 0.7)
        )
    except (TypeError, ValueError):
        temperature = 0.7

    try:
        max_tokens = int(persona_block.get("max_tokens", 150))
    except (TypeError, ValueError):
        max_tokens = 150

    filler_verbosity = str(
        persona_block.get("filler_verbosity", "brief")
    ).strip().lower()
    if filler_verbosity not in ("silent", "brief", "narrated", "chatty"):
        logger.warning(
            f"[persona] unknown filler_verbosity {filler_verbosity!r}; using 'brief'"
        )
        filler_verbosity = "brief"

    orb_variant = orb_block.get("variant")
    orb_palette = orb_block.get("palette")
    orb_params_raw = orb_block.get("params") or {}
    orb_params = dict(orb_params_raw) if isinstance(orb_params_raw, dict) else {}

    # State + mood authoring deltas (optional). Writes via config_store
    # normalize keys (case, enum membership) but hand-authored YAML
    # bypasses that path — mirror the filter here so a config.yaml with
    # `Speaking:` or `Valence:` doesn't silently fall out of the
    # composer's lookup.
    orb_state_overrides = _normalize_override_map(
        orb_block.get("state_overrides"),
        allowed={"idle", "listening", "thinking", "speaking"},
        numeric_only=False,
    )
    orb_mood_overrides = _normalize_override_map(
        orb_block.get("mood_overrides"),
        allowed={"valence", "arousal", "guardedness"},
        numeric_only=True,
    )

    # LLM routing — when the block is present, it wins over env.
    # run_bot reads persona.llm.{url, model, api_key, api_key_env}
    # and falls back to env defaults for each field individually.
    llm: dict | None = None
    if isinstance(llm_block, dict) and llm_block:
        llm = {}
        for k in ("url", "model", "api_key", "api_key_env", "extra_body"):
            v = llm_block.get(k)
            if v is not None:
                llm[k] = v

    persona = Persona(
        slug=slug,
        name=name,
        user_name=user_name,
        system_prompt=system_prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        filler_verbosity=filler_verbosity,
        tts_backend=tts_backend,
        voice=voice,
        orb_variant=orb_variant,
        orb_palette=orb_palette,
        orb_params=orb_params,
        orb_state_overrides=orb_state_overrides,
        orb_mood_overrides=orb_mood_overrides,
        llm=llm,
    )
    logger.info(f"[persona] loaded {persona.slug!r} from {path}")
    return persona


# Module-level cache. Updated by ``reload_persona``.
_active_persona: Persona | None = None


def get_active_persona() -> Persona:
    """Return the module-cached active persona, loading lazily on first use."""
    global _active_persona
    if _active_persona is None:
        _active_persona = load_persona(
            os.environ.get("ORBIS_CONFIG", "config/orbis.yaml")
        )
    return _active_persona


def reload_persona(config_path: str | Path | None = None) -> Persona:
    """Re-read the YAML file and replace the cached persona."""
    global _active_persona
    path = config_path or os.environ.get("ORBIS_CONFIG", "config/orbis.yaml")
    _active_persona = load_persona(path)
    return _active_persona
