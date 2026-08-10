"""Platform-aware path resolution for persistent data + model cache.

ORBIS's two long-lived file locations — the SQLite memory DB and the
Hugging Face / Kokoro model cache — need to land in OS-standard
per-user directories when the app runs as a bundled desktop binary.
The Docker path keeps its existing mount points; ORBIS_DB_PATH /
ORBIS_CACHE_DIR are thin overrides that Docker compose + Tauri both
set explicitly.

Resolution order for each path:
  1. explicit ``ORBIS_DB_PATH`` / ``ORBIS_CACHE_DIR`` env var
  2. legacy env vars (``HF_HOME`` / ``MODEL_DIR`` for cache) so existing
     Docker compose files keep working
  3. OS-standard user directory — XDG on Linux, Application Support
     on macOS, APPDATA on Windows

No I/O at import time; the helpers mkdir-on-demand only when a caller
actually asks for a path.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _platform() -> str:
    """Normalized platform string — 'darwin' | 'windows' | 'linux'."""
    p = sys.platform
    if p.startswith("win"):
        return "windows"
    if p == "darwin":
        return "darwin"
    return "linux"


def _default_data_dir() -> Path:
    """Per-OS persistent-data directory for the ORBIS SQLite DB, config
    that the app itself writes, etc. Created on demand."""
    system = _platform()
    if system == "darwin":
        return Path.home() / "Library" / "Application Support" / "orbis"
    if system == "windows":
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(appdata) / "orbis"
    # Linux / other: XDG_DATA_HOME → ~/.local/share
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "orbis"


def _default_cache_dir() -> Path:
    """Per-OS cache directory for model weights + HF downloads. Created
    on demand."""
    system = _platform()
    if system == "darwin":
        return Path.home() / "Library" / "Caches" / "orbis"
    if system == "windows":
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(local) / "orbis" / "Cache"
    # Linux / other: XDG_CACHE_HOME → ~/.cache
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "orbis"


def get_db_path() -> Path:
    """Absolute path to the SQLite DB. Pure resolution — does not
    touch the filesystem. Callers that open the DB are expected to
    create the parent dir themselves (see ``open_db``).

    Resolution:
      1. ``ORBIS_DB_PATH`` if set (absolute path to the `.sqlite` file)
      2. ``<data_dir>/orbis.sqlite``
    """
    override = os.environ.get("ORBIS_DB_PATH")
    return Path(override) if override else _default_data_dir() / "orbis.sqlite"


def get_sessions_dir() -> Path:
    """Absolute path to the per-session store — the cross-restart "last
    summary" recall and stashed push deliveries. Durable, alongside the
    SQLite memory DB.

    This used to default to ``/tmp/orbis_sessions``, which macOS purges
    (periodic tmp clean / reboot), so the companion's "remembers what we
    talked about" recall silently evaporated while the SQLite memory
    (facts/personality/mood) survived — a split-brain durability bug.

    Resolution:
      1. ``SESSION_STORE_DIR`` if set (Docker/explicit override)
      2. ``<data_dir>/sessions`` — same per-OS data dir as the SQLite DB
    """
    override = os.environ.get("SESSION_STORE_DIR")
    return Path(override) if override else _default_data_dir() / "sessions"


def get_voiceprint_path() -> Path:
    """Absolute path to the cached owner voiceprint.

    Speaker-verification gate (#35) reads from here on session-open;
    the wizard enrollment step writes here once. Platform-aware so
    desktop and Docker each get an OS-natural location instead of a
    cwd-relative ``data/voiceprint.npy`` file that breaks under
    ``cd``.

    Resolution:
      1. ``SPEAKER_GATE_VOICEPRINT_PATH`` if set
      2. ``<data_dir>/voiceprint.npy``
    """
    override = os.environ.get("SPEAKER_GATE_VOICEPRINT_PATH")
    return Path(override) if override else _default_data_dir() / "voiceprint.npy"


def get_oauth_dir() -> Path:
    """Absolute path to the OAuth credential-store directory — ORBIS's
    own instance-scoped copies of subscription tokens (Claude / Codex,
    see voice/llm/oauth.py) plus the explicit-disconnect marker. Pure
    resolution — writers create the dir on demand with 0o600 files.

    Resolution:
      1. ``ORBIS_OAUTH_DIR`` if set (tests / explicit override)
      2. ``<data_dir>/oauth``
    """
    override = os.environ.get("ORBIS_OAUTH_DIR")
    return Path(override) if override else _default_data_dir() / "oauth"


def get_cache_dir() -> Path:
    """Absolute path to the model-cache directory. Pure resolution —
    does not touch the filesystem. ``configure_hf_home`` creates the
    dir when it's actually needed.

    Resolution:
      1. ``ORBIS_CACHE_DIR`` if set
      2. ``HF_HOME`` if set (Docker compatibility — compose file still
         mounts ``/models`` and points HF_HOME at it)
      3. ``MODEL_DIR`` if set (legacy alias some docs used)
      4. per-OS default
    """
    override = (
        os.environ.get("ORBIS_CACHE_DIR")
        or os.environ.get("HF_HOME")
        or os.environ.get("MODEL_DIR")
    )
    return Path(override) if override else _default_cache_dir()


def configure_hf_home() -> Path:
    """Set ``HF_HOME`` / ``TRANSFORMERS_CACHE`` in os.environ so every
    downstream transformers / huggingface_hub import finds the ORBIS
    cache dir. Must run before the first transformers import.

    Creates the cache dir — this is the one path function that does
    I/O, because downstream consumers assume the dir exists.

    Returns the resolved cache dir for logging.
    """
    cache = get_cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(cache))
    os.environ.setdefault("TRANSFORMERS_CACHE", str(cache))
    return cache
