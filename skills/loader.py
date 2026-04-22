"""Discover + load voice skills from YAML files.

Layout:

    config/
      SOUL.md                 ← default persona's system prompt
      skills/
        chef.yaml             ← alternate persona
        voice-01.yaml         ← voice clone, extends: default (implicit)

Each skill YAML maps to `skills.models.Skill`.

## Inheritance

A skill YAML may set ``extends: <slug>`` to inherit all fields from
another skill. Child keys override parent keys. If ``extends:`` is
omitted, the parent defaults to ``default`` (the SOUL.md persona).
To opt out of inheritance entirely, set ``extends: null``. The
``default`` skill itself has no parent.

This lets voice-clone YAMLs be three lines (slug + name + voice);
they inherit the SOUL.md system prompt and fish TTS backend from the
default automatically.

## System prompt resolution

``system_prompt`` can be inline, or ``system_prompt_file: path.md``
points at a file relative to the skill YAML. A child skill that
inherits a parent's resolved ``system_prompt`` doesn't need either
field — inheritance carries the resolved text through.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from .models import DEFAULT_SOUL_SLUG, Skill

logger = logging.getLogger(__name__)


# Keys that flow through inheritance as-is (child overrides parent).
# Everything NOT in this list (e.g. ``extends``, ``_path``) is not
# considered a real skill field and gets dropped at merge time.
_SKILL_KEYS = {
    "slug", "name", "description",
    "system_prompt", "system_prompt_file",
    "tts_backend", "voice", "lang",
    "temperature", "max_tokens",
    "filler_verbosity",
    "tools",
    "behavior",
    "llm",
    "delegates",
    "viz",
}


def load_default_soul(
    config_dir: str | Path = "config",
    *,
    voice: str | None = None,
    tts_backend: str = "fish",
) -> Skill:
    """The 'default' skill — SOUL.md for its system prompt, env-default TTS."""
    path = Path(config_dir) / "SOUL.md"
    prompt = path.read_text().strip() if path.exists() else (
        "You are a helpful voice assistant. Keep responses concise — "
        "1-3 sentences max. Be conversational. No markdown."
    )
    return Skill(
        slug=DEFAULT_SOUL_SLUG,
        name="Default",
        system_prompt=prompt,
        tts_backend=tts_backend,
        voice=voice,
    )


def _skill_to_dict(skill: Skill) -> dict[str, Any]:
    """Serialize a Skill back to a YAML-like dict for inheritance merges."""
    return {
        "slug": skill.slug,
        "name": skill.name,
        "description": skill.description,
        "system_prompt": skill.system_prompt,
        "tts_backend": skill.tts_backend,
        "voice": skill.voice,
        "lang": skill.lang,
        "temperature": skill.temperature,
        "max_tokens": skill.max_tokens,
        "filler_verbosity": skill.filler_verbosity,
        "tools": list(skill.tools),
        "behavior": dict(skill.behavior),
        "llm": dict(skill.llm),
        "delegates": list(skill.delegates),
        "viz": dict(skill.viz),
    }


def _read_yaml(path: Path) -> dict[str, Any] | None:
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except Exception as e:
        logger.warning(f"[skills] failed to read {path}: {e}")
        return None
    if not isinstance(data, dict):
        logger.warning(f"[skills] {path}: top-level must be a mapping")
        return None
    return data


def _resolve_system_prompt(data: dict[str, Any], path: Path) -> str:
    """Resolve ``system_prompt`` / ``system_prompt_file`` into the final text.
    Returns the already-set ``system_prompt`` if present and non-empty;
    otherwise reads from ``system_prompt_file`` (resolved relative to the
    skill YAML's directory). Empty string on miss.
    """
    prompt = (data.get("system_prompt") or "").strip()
    if prompt:
        return prompt
    prompt_file = data.get("system_prompt_file")
    if prompt_file:
        pf = path.parent / str(prompt_file)
        if pf.exists():
            return pf.read_text().strip()
        logger.warning(f"[skills] {data.get('slug')}: system_prompt_file {pf} missing")
    return ""


_DEEP_MERGE_KEYS = {"behavior", "llm", "viz"}


def _merge(parent: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
    """Shallow merge — child keys override parent keys, confined to real skill
    fields. `behavior` + `llm` get a one-level deep merge so a child can
    override one sub-key without clobbering the rest."""
    out: dict[str, Any] = {}
    for k in _SKILL_KEYS:
        if k in _DEEP_MERGE_KEYS:
            pv = parent.get(k) or {}
            cv = child.get(k) or {}
            if isinstance(pv, dict) and isinstance(cv, dict):
                out[k] = {**pv, **cv}
            else:
                out[k] = cv or pv or {}
            continue
        if k in child and child[k] is not None:
            out[k] = child[k]
        elif k in parent and parent[k] is not None:
            out[k] = parent[k]
    return out


def _build_skill(merged: dict[str, Any]) -> Skill | None:
    """Build a Skill from a fully-merged dict. Expects system_prompt already resolved."""
    slug = merged.get("slug")
    if not slug:
        logger.warning("[skills] merged dict missing slug; skipping")
        return None
    prompt = (merged.get("system_prompt") or "").strip()
    if not prompt:
        logger.warning(f"[skills] {slug}: no system_prompt (inherited or direct); skipping")
        return None
    name = merged.get("name") or str(slug).replace("-", " ").replace("_", " ").title()
    return Skill(
        slug=str(slug),
        name=str(name),
        system_prompt=prompt,
        tts_backend=(merged.get("tts_backend") or "fish").lower(),
        voice=merged.get("voice"),
        lang=merged.get("lang"),
        temperature=float(merged.get("temperature", 0.7)),
        max_tokens=int(merged.get("max_tokens", 150)),
        description=(merged.get("description") or "").strip(),
        filler_verbosity=merged.get("filler_verbosity"),
        tools=list(merged.get("tools") or []),
        behavior=dict(merged.get("behavior") or {}),
        llm=dict(merged.get("llm") or {}),
        delegates=list(merged.get("delegates") or []),
        viz=dict(merged.get("viz") or {}),
    )


def load_skills(config_dir: str | Path = "config") -> dict[str, Skill]:
    """Return {slug: Skill} including the default, with ``extends:`` resolved."""
    config_dir = Path(config_dir)
    out: dict[str, Skill] = {}

    default_skill = load_default_soul(config_dir)
    out[DEFAULT_SOUL_SLUG] = default_skill
    default_dict = _skill_to_dict(default_skill)

    skills_dir = config_dir / "skills"
    if not skills_dir.exists():
        logger.info(f"[skills] loaded {len(out)} skills: {list(out.keys())}")
        return out

    # Pass 1 — parse every YAML into a raw dict, keyed by slug. We also
    # resolve system_prompt_file here so inheritance carries concrete text.
    raw_by_slug: dict[str, dict[str, Any]] = {}
    path_by_slug: dict[str, Path] = {}
    for f in sorted(skills_dir.glob("*.yaml")):
        data = _read_yaml(f)
        if data is None:
            continue
        slug = data.get("slug") or f.stem
        data["slug"] = slug
        resolved = _resolve_system_prompt(data, f)
        if resolved:
            data["system_prompt"] = resolved
        # Drop system_prompt_file — it's resolved into system_prompt now.
        data.pop("system_prompt_file", None)
        raw_by_slug[slug] = data
        path_by_slug[slug] = f

    # Pass 2 — resolve inheritance and build final Skills.
    def resolve(slug: str, visited: set[str]) -> dict[str, Any]:
        if slug in visited:
            logger.warning(f"[skills] extends cycle detected at '{slug}': {visited}")
            return {}
        visited = visited | {slug}
        if slug == DEFAULT_SOUL_SLUG:
            return default_dict
        raw = raw_by_slug.get(slug)
        if raw is None:
            logger.warning(f"[skills] extends target '{slug}' not found; treating as default")
            return default_dict
        extends_val: Any = raw.get("extends", DEFAULT_SOUL_SLUG)
        if extends_val is None:
            parent: dict[str, Any] = {}
        else:
            parent = resolve(str(extends_val), visited)
        return _merge(parent, raw)

    for slug in raw_by_slug:
        merged = resolve(slug, set())
        skill = _build_skill(merged)
        if skill:
            out[skill.slug] = skill

    logger.info(f"[skills] loaded {len(out)} skills: {list(out.keys())}")
    return out


def write_voice_clone_skill(
    slug: str,
    name: str,
    reference_id: str,
    *,
    description: str = "",
    config_dir: str | Path = "config",
) -> Path:
    """Write a skill YAML for a newly-cloned Fish voice.

    The generated skill inherits from the default skill (SOUL.md persona,
    fish TTS backend) — so the YAML is just slug + name + voice +
    description. Edit later to customize tone, temperature, tools, etc.
    """
    cfg = Path(config_dir)
    skills_dir = cfg / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    path = skills_dir / f"{slug}.yaml"
    doc = {
        "slug": slug,
        "name": name,
        "description": description,
        "voice": reference_id,
        # tts_backend + system_prompt inherited from the default skill.
    }
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    logger.info(f"[skills] wrote {path}")
    return path
