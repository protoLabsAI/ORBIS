"""Persona catalog — drop-in frontmatter-markdown identity bundles.

Epic #611, P1 (#607). Plan of record: docs/internal/personas.md.

A persona is one ``personas/<slug>.md`` file: YAML frontmatter for the
identity metadata, markdown body as the system prompt::

    ---
    name: Chef Bruno
    description: Italian-American chef, practical kitchen wisdom.
    extends: default          # optional; this is the implicit parent
    voice:
      tts_backend: kokoro     # omit either field to inherit
      voice: am_michael
    llm:                      # optional; one-level merge over the parent's
      model: protolabs/fast
    orb: ember                # starter slug, imported .orbis id,
                              # or inline {variant, palette, params}
    temperature: 0.9
    max_tokens: 200
    filler_verbosity: brief
    tools: [get_datetime, web_search]
    ---
    You are Chef Bruno... (body = system prompt)

Slug = filename stem. The **default persona** is the one assembled from
``config/orbis.yaml`` by ``agent.persona.load_persona`` — every persona
file implicitly ``extends`` it (protoVoice's SOUL.md role; the loader
here is a port of the seed's ``skills/loader.py``). ``extends`` may
also name another persona file; ``extends: null`` opts out entirely.

Two directories, same split as imported orbs (``orb_definitions``):

- bundled starters: ``ORBIS_BUNDLED_PERSONAS`` (Tauri Resource, read-
  only in the packaged app) or the dev fallback ``config/personas``
- user-authored: ``ORBIS_PERSONAS_DIR`` (``<app_data_dir>/personas``,
  writable) or the same dev fallback

A user file **shadows** a bundled one with the same slug — that's the
edit path for shipped starters ("Duplicate" in the manager dialog).

Persona files are shareable text, so ``llm.api_key`` (and
``llm.fallback.api_key``) are refused — use ``api_key_env``. Machine
config (stt, tts endpoints/credentials, user_name) never comes from a
persona file; it always flows from orbis.yaml / env.

Read paths never raise — a bad file logs a warning and drops out,
mirroring ``load_persona``'s never-crash-the-boot contract.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from agent.persona import Persona, filter_llm_block

logger = logging.getLogger(__name__)

# Slugs that always refer to the orbis.yaml persona, never a file.
RESERVED_SLUGS = {"default", "orbis"}

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# Frontmatter keys a persona file may set. Anything else is dropped
# with a warning (typo'd keys should be loud, not silent).
_ALLOWED_META_KEYS = {
    "name", "description", "extends",
    "voice", "llm", "orb",
    "temperature", "max_tokens", "filler_verbosity",
    "tools",
}
_ALLOWED_VOICE_KEYS = {"tts_backend", "voice"}
_ALLOWED_VERBOSITIES = {"silent", "brief", "narrated", "chatty"}


def bundled_personas_dir(path: str | Path | None = None) -> Path:
    """Read-only starters. Packaged: the Tauri shell points
    ``ORBIS_BUNDLED_PERSONAS`` at the app Resource dir; dev fallback is
    repo-relative ``config/personas``."""
    return Path(
        path or os.environ.get("ORBIS_BUNDLED_PERSONAS") or "config/personas"
    )


def user_personas_dir(path: str | Path | None = None) -> Path:
    """User-authored personas. Packaged: ``ORBIS_PERSONAS_DIR`` is
    ``<app_data_dir>/personas`` (writable, survives updates); dev
    fallback is the same ``config/personas`` as the bundled dir."""
    return Path(
        path or os.environ.get("ORBIS_PERSONAS_DIR") or "config/personas"
    )


@dataclass(frozen=True)
class PersonaFile:
    """One parsed persona markdown file (pre-composition)."""

    slug: str
    source: str  # "bundled" | "user"
    path: Path
    meta: dict
    body: str

    @property
    def name(self) -> str:
        raw = self.meta.get("name")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        return self.slug.replace("-", " ").replace("_", " ").title()

    @property
    def description(self) -> str:
        raw = self.meta.get("description")
        return raw.strip() if isinstance(raw, str) else ""


def parse_frontmatter(text: str) -> tuple[dict, str] | None:
    """Split ``---``-fenced YAML frontmatter from the markdown body.

    No opening fence → the whole file is the body (a bare prompt file
    is a valid persona; the slug supplies the name). Malformed YAML or
    a non-mapping frontmatter → None (caller drops the file).
    """
    if not text.lstrip().startswith("---"):
        return {}, text.strip()
    # Split on the first two fence lines. lstrip so a leading BOM/blank
    # line doesn't break the fence match.
    lines = text.lstrip().splitlines()
    try:
        close = next(
            i for i, ln in enumerate(lines[1:], start=1) if ln.strip() == "---"
        )
    except StopIteration:
        logger.warning("[personas] unterminated frontmatter fence")
        return None
    raw_meta = "\n".join(lines[1:close])
    body = "\n".join(lines[close + 1:]).strip()
    try:
        meta = yaml.safe_load(raw_meta) or {}
    except Exception as e:
        logger.warning(f"[personas] bad frontmatter yaml: {e}")
        return None
    if not isinstance(meta, dict):
        logger.warning("[personas] frontmatter must be a mapping")
        return None
    return meta, body


def _filter_meta(meta: dict, *, origin: str) -> dict:
    """Drop unknown keys + the secrets a shareable file must not hold."""
    out: dict[str, Any] = {}
    for k, v in meta.items():
        if k not in _ALLOWED_META_KEYS:
            logger.warning(f"[personas] {origin}: dropping unknown key {k!r}")
            continue
        out[k] = v
    llm = out.get("llm")
    if isinstance(llm, dict):
        if "api_key" in llm:
            logger.warning(
                f"[personas] {origin}: llm.api_key is not allowed in a "
                "persona file (shareable text) — use api_key_env"
            )
            llm = {k: v for k, v in llm.items() if k != "api_key"}
        fb = llm.get("fallback")
        if isinstance(fb, dict) and "api_key" in fb:
            logger.warning(
                f"[personas] {origin}: llm.fallback.api_key is not allowed "
                "in a persona file — use api_key_env"
            )
            llm = dict(llm)
            llm["fallback"] = {k: v for k, v in fb.items() if k != "api_key"}
        out["llm"] = llm
    return out


def _load_dir(d: Path, source: str) -> dict[str, PersonaFile]:
    out: dict[str, PersonaFile] = {}
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.md")):
        slug = f.stem.strip().lower()
        if slug in RESERVED_SLUGS:
            logger.warning(f"[personas] {f}: slug {slug!r} is reserved; skipping")
            continue
        if not _SLUG_RE.match(slug):
            logger.warning(f"[personas] {f}: invalid slug {slug!r}; skipping")
            continue
        try:
            parsed = parse_frontmatter(f.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"[personas] failed to read {f}: {e}")
            continue
        if parsed is None:
            logger.warning(f"[personas] {f}: unparseable; skipping")
            continue
        meta, body = parsed
        out[slug] = PersonaFile(
            slug=slug,
            source=source,
            path=f,
            meta=_filter_meta(meta, origin=f.name),
            body=body,
        )
    return out


def load_persona_files(
    *,
    bundled: str | Path | None = None,
    user: str | Path | None = None,
) -> dict[str, PersonaFile]:
    """{slug: PersonaFile} across both dirs; user shadows bundled."""
    b_dir = bundled_personas_dir(bundled)
    u_dir = user_personas_dir(user)
    files = _load_dir(b_dir, "bundled")
    if u_dir.resolve() != b_dir.resolve():
        files.update(_load_dir(u_dir, "user"))
    return files


# --- composition ------------------------------------------------------------


def _resolve_orb_ref(ref: Any, *, origin: str) -> dict:
    """Resolve ``orb:`` into {variant, palette, params}.

    A string is a starter slug first (config/starter_orbs.yaml), else an
    orb variant / imported ``.orbis`` definition id passed through as
    the variant (the frontend registry lookup fails gracefully on
    unknowns — same contract as protoVoice's viz). A mapping passes
    through key-filtered.
    """
    if isinstance(ref, str):
        ref = ref.strip()
        if not ref:
            return {}
        from agent.starter_orbs import load_starters
        for s in load_starters():
            if s.slug == ref:
                return {
                    "variant": s.variant,
                    "palette": s.palette,
                    "params": dict(s.params),
                }
        logger.info(
            f"[personas] {origin}: orb {ref!r} is not a starter slug; "
            "passing through as a variant/definition id"
        )
        return {"variant": ref, "palette": None, "params": {}}
    if isinstance(ref, dict):
        params = ref.get("params")
        return {
            "variant": ref.get("variant"),
            "palette": ref.get("palette"),
            "params": dict(params) if isinstance(params, dict) else {},
        }
    logger.warning(f"[personas] {origin}: orb must be a string or mapping")
    return {}


def _overrides_from(pf: PersonaFile) -> dict:
    """Persona-dataclass field overrides a single file contributes.
    Invalid values warn and drop — inheritance fills the gap."""
    meta = pf.meta
    o: dict[str, Any] = {}

    voice = meta.get("voice")
    if isinstance(voice, dict):
        for k in _ALLOWED_VOICE_KEYS:
            v = voice.get(k)
            if isinstance(v, str) and v.strip():
                o["tts_backend" if k == "tts_backend" else "voice"] = (
                    v.strip().lower() if k == "tts_backend" else v.strip()
                )
    elif voice is not None:
        logger.warning(f"[personas] {pf.slug}: voice must be a mapping")

    if meta.get("temperature") is not None:
        try:
            o["temperature"] = float(meta["temperature"])
        except (TypeError, ValueError):
            logger.warning(f"[personas] {pf.slug}: temperature must be numeric")
    if meta.get("max_tokens") is not None:
        try:
            o["max_tokens"] = int(meta["max_tokens"])
        except (TypeError, ValueError):
            logger.warning(f"[personas] {pf.slug}: max_tokens must be an integer")
    fv = meta.get("filler_verbosity")
    if fv is not None:
        fv = str(fv).strip().lower()
        if fv in _ALLOWED_VERBOSITIES:
            o["filler_verbosity"] = fv
        else:
            logger.warning(f"[personas] {pf.slug}: unknown filler_verbosity {fv!r}")

    tools = meta.get("tools")
    if isinstance(tools, list) and tools:
        o["tools"] = [str(t) for t in tools]

    if meta.get("orb") is not None:
        orb = _resolve_orb_ref(meta["orb"], origin=pf.slug)
        if orb:
            # An orb identity is atomic — replace all three together.
            o["orb_variant"] = orb["variant"]
            o["orb_palette"] = orb["palette"]
            o["orb_params"] = orb["params"]

    if pf.body:
        o["system_prompt"] = pf.body
    return o


class _CycleError(Exception):
    """extends chain loops back on itself — an authoring error the
    composer surfaces by dropping the persona (vs. a missing target,
    which degrades to extending the default)."""


def _chain(
    slug: str, files: dict[str, PersonaFile], seen: set[str],
) -> tuple[list[PersonaFile], bool] | None:
    """Ordered [root..leaf] chain for ``slug`` + whether the root
    extends the default persona. None on a missing link; raises
    ``_CycleError`` on a cycle."""
    if slug in seen:
        raise _CycleError(slug)
    pf = files.get(slug)
    if pf is None:
        logger.warning(f"[personas] extends target {slug!r} not found")
        return None
    ext = pf.meta.get("extends", "default")
    if ext is None or ext is False:
        return [pf], False
    ext = str(ext).strip().lower()
    if ext in RESERVED_SLUGS:
        return [pf], True
    parent = _chain(ext, files, seen | {slug})
    if parent is None:
        # Broken parent link — degrade to extending the default rather
        # than dropping the persona entirely.
        return [pf], True
    chain, from_default = parent
    return chain + [pf], from_default


def compose_persona(
    slug: str,
    default: Persona,
    files: dict[str, PersonaFile] | None = None,
) -> Persona | None:
    """Compose the persona file chain over the default persona.

    Returns a full ``Persona`` (same object the pipeline consumes) or
    None when the slug is unknown / unusable. Machine-level fields —
    user_name, stt, tts endpoint/credentials, orb authoring deltas —
    always come from the default regardless of the chain.
    """
    if slug in RESERVED_SLUGS:
        return default
    if files is None:
        files = load_persona_files()
    if slug not in files:
        logger.warning(f"[personas] unknown persona {slug!r}")
        return None
    try:
        resolved = _chain(slug, files, set())
    except _CycleError as e:
        logger.warning(f"[personas] extends cycle detected at {e}; dropping {slug!r}")
        return None
    if resolved is None:
        return None
    chain, from_default = resolved

    base = default if from_default else Persona()
    overrides: dict[str, Any] = {}
    llm_acc: dict = dict(base.llm or {})
    for pf in chain:  # root → leaf; leaf wins
        overrides.update(_overrides_from(pf))
        node_llm = filter_llm_block(pf.meta.get("llm"))
        if node_llm:
            llm_acc.update(node_llm)

    if not (overrides.get("system_prompt") or base.system_prompt):
        logger.warning(f"[personas] {slug}: no system prompt anywhere in the chain")
        return None

    leaf = chain[-1]
    return replace(
        base,
        slug=slug,
        name=leaf.name,
        llm=llm_acc or None,
        active_persona="",
        **overrides,
    )


# --- authoring (manager dialog / PUT endpoint) -------------------------------


def serialize_persona_md(meta: dict, body: str) -> str:
    """Frontmatter + body, ready to write. Meta is key-filtered the
    same way the reader filters, so what we write always round-trips."""
    clean = _filter_meta(dict(meta), origin="serialize")
    fm = yaml.safe_dump(clean, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{fm}\n---\n\n{body.strip()}\n"


def write_persona_file(
    slug: str, meta: dict, body: str, *, user_dir: str | Path | None = None,
) -> Path:
    """Atomic write to the user personas dir. Raises ValueError on a
    bad or reserved slug (the API layer surfaces it as a 400)."""
    slug = slug.strip().lower()
    if slug in RESERVED_SLUGS:
        raise ValueError(f"slug {slug!r} is reserved for the orbis.yaml persona")
    if not _SLUG_RE.match(slug):
        raise ValueError(
            "slug must be lowercase alphanumeric with - or _ (max 64 chars)"
        )
    d = user_personas_dir(user_dir)
    d.mkdir(parents=True, exist_ok=True)
    dest = d / f"{slug}.md"
    text = serialize_persona_md(meta, body)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".md.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.rename(tmp, dest)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise
    logger.info(f"[personas] wrote {dest}")
    return dest


def delete_persona_file(
    slug: str, *, user_dir: str | Path | None = None,
) -> bool:
    """Delete a USER persona file. Returns False when there's nothing
    to delete in the user dir (bundled files are read-only — a user
    file with the same slug is a shadow, and deleting it un-shadows)."""
    slug = slug.strip().lower()
    dest = user_personas_dir(user_dir) / f"{slug}.md"
    if not dest.is_file():
        return False
    dest.unlink()
    logger.info(f"[personas] deleted {dest}")
    return True
