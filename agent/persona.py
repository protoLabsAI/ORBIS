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
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Persona:
    """The single ORBIS persona loaded from config/orbis.yaml."""

    slug: str = "orbis"
    name: str = "ORBIS"
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

    # Retained for compatibility with the skills-era call signature
    # used by _effective_prompt / delegate routing. Always empty for
    # ORBIS (delegation targets are global, not persona-scoped).
    delegates: list | None = None
    behavior: dict = field(default_factory=dict)
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

    slug = (persona_block.get("slug") or "orbis").strip()
    name = (persona_block.get("name") or "ORBIS").strip()
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

    persona = Persona(
        slug=slug,
        name=name,
        system_prompt=system_prompt,
        temperature=temperature,
        max_tokens=max_tokens,
        filler_verbosity=filler_verbosity,
        tts_backend=tts_backend,
        voice=voice,
        orb_variant=orb_variant,
        orb_palette=orb_palette,
        orb_params=orb_params,
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
