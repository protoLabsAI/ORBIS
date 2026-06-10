"""Store + validator for user-imported ``.orbis`` orb definitions.

A ``.orbis`` file is a single JSON document: GLSL fragment body + typed
uniform declarations + settings fields + palettes + declarative
signal→uniform bindings. No executable JS anywhere in the format — that
is the security model for runtime-imported orbs. The frontend renders
definitions through ``@orbis/orb-runtime``'s raymarch-v1 engine.

This module is the sidecar's HALF of the contract:
  - mirrors ``packages/orb-runtime/src/definition/validate.ts`` rule for
    rule (caps, slug/uniform regexes, binding target/signal checks) so a
    definition the API accepts is one the engine will load;
  - persists definitions as ``<id>.orbis`` JSON files in the app-data
    orbs dir (``ORBIS_ORBS_DIR``, wired by the Tauri shell the same way
    as ``DELEGATES_YAML``) with atomic tempfile→rename writes.

Plan of record: docs/internal/orb-format-and-editor.md.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = [
    "OrbDefinitionError",
    "validate_definition",
    "list_definitions",
    "save_definition",
    "delete_definition",
    "orbs_dir",
]

# --- caps: keep identical to packages/orb-runtime/src/definition/validate.ts ---
MAX_FRAGMENT_CHARS = 256_000
MAX_DEFINITION_CHARS = 512_000
MAX_UNIFORMS = 64
MAX_BINDINGS = 128
MAX_FIELDS = 64
MAX_PALETTES = 32

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
_UNIFORM_NAME_RE = re.compile(r"^u[A-Za-z0-9_]{1,63}$")
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

_UNIFORM_TYPES = {"float", "vec2", "vec3", "vec4", "color"}
_VEC_ARITY = {"vec2": 2, "vec3": 3, "vec4": 4}
_SECTIONS = {"color", "energy", "motion", "fractal", "perf"}
_OPS = {"set", "add", "mul"}
_CURVES = {"linear", "exp", "smoothstep"}
_COMPONENTS = ["x", "y", "z", "w"]
_MOOD_DIMS = {"valence", "arousal", "guardedness"}

_STANDARD_UNIFORMS = {
    "uTime", "uLocalCamPos", "uPrimaryColor", "uSecondaryColor",
    "uClickDir", "uClickStrength",
}
_RESERVED_TARGETS = {"uTime", "uLocalCamPos", "uClickDir"}

_SCALAR_SIGNALS = {
    "time", "bot.level", "user.level", "breath", "pointer.clickStrength",
    "mood.valence", "mood.arousal", "mood.guardedness",
    "snap.density", "snap.glow", "snap.speed", "snap.ca", "snap.asymmetry",
    "snap.rotation", "snap.scale",
}
_COLOR_SIGNALS = {"snap.primary", "snap.secondary"}


class OrbDefinitionError(ValueError):
    """Raised by the store on invalid input; ``.errors`` carries the list."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def orbs_dir(path: str | Path | None = None) -> Path:
    """The directory holding ``<id>.orbis`` files. ``ORBIS_ORBS_DIR`` is
    set by the Tauri shell to ``<app_data_dir>/orbs``; the dev fallback
    is repo-relative ``config/orbs``."""
    return Path(path or os.environ.get("ORBIS_ORBS_DIR") or "config/orbs")


# ---------------------------------------------------------------------------
# Validation — mirror of the TS validator
# ---------------------------------------------------------------------------


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _valid_default(utype: str, d) -> bool:
    if utype == "float":
        return _is_num(d)
    if utype == "color":
        return isinstance(d, str) and bool(_HEX_COLOR_RE.match(d))
    arity = _VEC_ARITY[utype]
    return isinstance(d, list) and len(d) == arity and all(_is_num(x) for x in d)


def _validate_field(f, i: int) -> str | None:
    if not isinstance(f, dict):
        return f"fields[{i}] must be an object"
    key = f.get("key")
    label = f.get("label")
    if not isinstance(key, str) or not 0 < len(key) <= 64:
        return f"fields[{i}].key invalid"
    if not isinstance(label, str) or not 0 < len(label) <= 64:
        return f"fields[{i}].label invalid"
    if f.get("section") not in _SECTIONS:
        return f"fields[{i}].section must be color|energy|motion|fractal|perf"
    kind = f.get("kind")
    if kind == "color":
        return None
    if kind == "slider":
        mn, mx, step = f.get("min"), f.get("max"), f.get("step")
        if not (_is_num(mn) and _is_num(mx) and _is_num(step)) or step <= 0 or mx <= mn:
            return f"fields[{i}] slider needs numeric min < max and step > 0"
        return None
    return f"fields[{i}].kind must be color|slider"


def _validate_binding(b, i: int, uniform_names: set, uniform_types: dict) -> list[str]:
    errs: list[str] = []
    if not isinstance(b, dict):
        return [f"bindings[{i}] must be an object"]

    target = b.get("target")
    if not isinstance(target, str):
        return [f"bindings[{i}].target must be a string"]
    parts = target.split(".")
    u_name = parts[0]
    comp = parts[1] if len(parts) > 1 else None
    if len(parts) > 2 or (comp is not None and comp not in _COMPONENTS):
        return [f"bindings[{i}].target {target!r} — component must be one of x|y|z|w"]
    if u_name in _RESERVED_TARGETS:
        errs.append(f"bindings[{i}].target {u_name!r} is engine-managed and cannot be bound")
    if u_name not in uniform_names:
        errs.append(f"bindings[{i}].target {u_name!r} is not a declared or standard uniform")

    utype = uniform_types.get(u_name)
    target_is_color = utype == "color" or u_name in ("uPrimaryColor", "uSecondaryColor")
    if comp is not None:
        if utype not in _VEC_ARITY:
            errs.append(f"bindings[{i}].target {target!r} — component suffix needs a vec uniform")
        elif _COMPONENTS.index(comp) >= _VEC_ARITY[utype]:
            errs.append(f"bindings[{i}].target {target!r} — out of range for {utype}")

    signal = b.get("signal")
    if not isinstance(signal, str):
        errs.append(f"bindings[{i}].signal must be a string")
        return errs
    is_param = signal.startswith("param.")
    is_scalar_sig = signal in _SCALAR_SIGNALS or is_param
    is_color_sig = signal in _COLOR_SIGNALS or is_param
    if not is_scalar_sig and not is_color_sig:
        errs.append(f"bindings[{i}].signal {signal!r} is unknown")

    if target_is_color and comp is None:
        if not is_color_sig:
            errs.append(f"bindings[{i}] — color target {u_name!r} needs a color signal")
        if b.get("op") not in (None, "set"):
            errs.append(f"bindings[{i}] — color targets only support op \"set\"")

    if b.get("op") is not None and b["op"] not in _OPS:
        errs.append(f"bindings[{i}].op must be set|add|mul")
    if b.get("curve") is not None and b["curve"] not in _CURVES:
        errs.append(f"bindings[{i}].curve must be linear|exp|smoothstep")
    for k in ("scale", "offset"):
        if b.get(k) is not None and not _is_num(b[k]):
            errs.append(f"bindings[{i}].{k} must be a number")
    smooth = b.get("smooth")
    if smooth is not None and (not _is_num(smooth) or not 0 < smooth <= 1):
        errs.append(f"bindings[{i}].smooth must be in (0, 1]")
    return errs


def validate_definition(data) -> list[str]:
    """Return every problem found (empty list = valid). Mirrors
    ``validateOrbDefinition`` in the TS package — keep the rules in
    lock-step when either side changes."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["definition must be a JSON object"]

    try:
        if len(json.dumps(data)) > MAX_DEFINITION_CHARS:
            return [f"definition exceeds {MAX_DEFINITION_CHARS} chars"]
    except (TypeError, ValueError):
        return ["definition is not serializable JSON"]

    if data.get("format") != "orbis-orb":
        errors.append('format must be "orbis-orb"')
    if data.get("version") != 1:
        errors.append("version must be 1")
    orb_id = data.get("id")
    if not isinstance(orb_id, str) or not _ID_RE.match(orb_id):
        errors.append("id must be a slug ([a-z0-9-], 2-64 chars)")
    name = data.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        errors.append("name must be a non-empty string (≤120 chars)")
    if data.get("engine") != "raymarch-v1":
        errors.append('engine must be "raymarch-v1" (the only v1 engine)')

    shaders = data.get("shaders")
    fragment = shaders.get("fragment") if isinstance(shaders, dict) else None
    if not isinstance(fragment, str) or not fragment.strip():
        errors.append("shaders.fragment must be a non-empty GLSL string")
    elif len(fragment) > MAX_FRAGMENT_CHARS:
        errors.append(f"shaders.fragment exceeds {MAX_FRAGMENT_CHARS} chars")

    uniform_names = set(_STANDARD_UNIFORMS)
    uniform_types: dict[str, str] = {}
    uniforms = data.get("uniforms")
    if not isinstance(uniforms, dict):
        errors.append("uniforms must be an object (may be empty)")
    else:
        if len(uniforms) > MAX_UNIFORMS:
            errors.append(f"more than {MAX_UNIFORMS} uniforms")
        for uname, decl in uniforms.items():
            if not isinstance(uname, str) or not _UNIFORM_NAME_RE.match(uname):
                errors.append(f"uniform {uname!r} — names must match u[A-Za-z0-9_]+")
                continue
            if uname in _STANDARD_UNIFORMS:
                errors.append(f"uniform {uname!r} shadows a standard uniform")
                continue
            utype = decl.get("type") if isinstance(decl, dict) else None
            if utype not in _UNIFORM_TYPES:
                errors.append(f"uniform {uname!r} — type must be one of float|vec2|vec3|vec4|color")
                continue
            default = decl.get("default")
            if default is not None and not _valid_default(utype, default):
                errors.append(f"uniform {uname!r} — default doesn't match type {utype}")
                continue
            uniform_names.add(uname)
            uniform_types[uname] = utype

    fields = data.get("fields")
    if not isinstance(fields, list):
        errors.append("fields must be an array (may be empty)")
    else:
        if len(fields) > MAX_FIELDS:
            errors.append(f"more than {MAX_FIELDS} fields")
        for i, f in enumerate(fields):
            e = _validate_field(f, i)
            if e:
                errors.append(e)

    palettes = data.get("palettes")
    if not isinstance(palettes, dict) or not palettes:
        errors.append("palettes must be a non-empty object")
    else:
        if len(palettes) > MAX_PALETTES:
            errors.append(f"more than {MAX_PALETTES} palettes")
        for pname, palette in palettes.items():
            if not isinstance(palette, dict):
                errors.append(f"palette {pname!r} must be an object")
                continue
            for k, pv in palette.items():
                if not _is_num(pv) and not isinstance(pv, str):
                    errors.append(f"palette {pname!r}.{k} must be number or string")
        if data.get("defaultPalette") not in palettes:
            errors.append("defaultPalette must name an existing palette")

    bindings = data.get("bindings")
    if not isinstance(bindings, list):
        errors.append("bindings must be an array (may be empty)")
    else:
        if len(bindings) > MAX_BINDINGS:
            errors.append(f"more than {MAX_BINDINGS} bindings")
        for i, b in enumerate(bindings):
            errors.extend(_validate_binding(b, i, uniform_names, uniform_types))

    mood = data.get("moodDefaults")
    if mood is not None:
        if not isinstance(mood, dict):
            errors.append("moodDefaults must be an object or null")
        else:
            for dim, deltas in mood.items():
                if dim not in _MOOD_DIMS:
                    errors.append(f"moodDefaults.{dim} — unknown mood dimension")
                elif not isinstance(deltas, dict):
                    errors.append(f"moodDefaults.{dim} must be an object of param deltas")

    for key in ("geometry", "material", "motion"):
        if data.get(key) is not None and not isinstance(data[key], dict):
            errors.append(f"{key} must be an object")
    if data.get("post") is not None and not isinstance(data["post"], dict):
        errors.append("post must be an object or null")

    return errors


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def list_definitions(path: str | Path | None = None) -> list[dict]:
    """All valid definitions in the orbs dir, sorted by id. Invalid files
    are skipped with a warning — one corrupt import must never blank the
    whole catalog."""
    d = orbs_dir(path)
    if not d.is_dir():
        return []
    out: list[dict] = []
    for f in sorted(d.glob("*.orbis")):
        try:
            data = json.loads(f.read_text())
        except (OSError, ValueError) as e:
            logger.warning(f"[orbs] skipping unreadable {f.name}: {e}")
            continue
        errors = validate_definition(data)
        if errors:
            logger.warning(f"[orbs] skipping invalid {f.name}: {errors[:3]}")
            continue
        out.append(data)
    return out


def save_definition(data: dict, path: str | Path | None = None) -> tuple[Path, bool]:
    """Validate + persist a definition as ``<id>.orbis``. Returns
    ``(path, replaced)``. Atomic write — same tempfile→rename posture as
    the config/delegates stores."""
    errors = validate_definition(data)
    if errors:
        raise OrbDefinitionError(errors)
    d = orbs_dir(path)
    d.mkdir(parents=True, exist_ok=True)
    dest = d / f"{data['id']}.orbis"
    replaced = dest.exists()
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".orbis-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.rename(tmp, dest)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    logger.info(f"[orbs] {'replaced' if replaced else 'saved'} {dest.name}")
    return dest, replaced


def delete_definition(orb_id: str, path: str | Path | None = None) -> bool:
    """Remove ``<id>.orbis``. Returns False when it didn't exist. The id
    is validated against the slug regex so a hostile id can't traverse
    out of the orbs dir."""
    if not _ID_RE.match(orb_id or ""):
        return False
    dest = orbs_dir(path) / f"{orb_id}.orbis"
    if not dest.is_file():
        return False
    dest.unlink()
    logger.info(f"[orbs] deleted {dest.name}")
    return True
