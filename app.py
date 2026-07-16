#!/usr/bin/env python3
"""ORBIS — Pipecat pipeline (Mac-first native audio, single transport).

Pipeline (all in-process Python; mic + speaker frames cross a Unix
socket to the Rust native audio engine in src-tauri/):

  Native mic (AVAudioEngine voice-processing on Mac) → Unix socket
                  → LocalAudioTransport.input()
                  → LocalWhisperSTT
                  → user aggregator (VAD attached here)
                  → OpenAILLMService — tools registered for delegate_to etc.
                  → TTS (Kokoro default; ElevenLabs / OpenAI-compat / Fish opt-in)
                  → LocalAudioTransport.output() → Unix socket → CPAL speaker (Rust)
                  → assistant aggregator

Native is the supported desktop transport. Apple Silicon Mac is the current
production hardening target; Linux and Windows desktop builds come later. See
DECISIONS.md amendments 2026-04-28 and 2026-05-29.

Duplex behavior:
  - on `on_function_calls_started`: queue a TTSSpeakFrame opening filler
  - `_progress_loop()`: emit periodic progress phrases while the tool runs
  - tool handlers are wrapped so they cancel the progress loop on return
"""

import argparse
import asyncio
import logging
import os
import signal
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

# Load .env BEFORE any other module reads os.environ. python-dotenv leaves
# already-set env vars alone (shell env wins over .env — standard).
# For deployed boxes, Infisical (or whichever secrets manager) injects
# env vars at container start; this block then no-ops because the file
# isn't there. Local dev + CI keep a .env; production doesn't.
try:
    from dotenv import load_dotenv
    load_dotenv()
    # Runtime tuning override: an optional .env in the user-config dir
    # (next to orbis.yaml) lets us flip env knobs — VAD windows, echo
    # guard, SMART_TURN, micro-ack — with a 5-second app restart instead
    # of an 80-second sidecar rebuild. override=True so it wins over the
    # values the Tauri shell injects when it spawns the sidecar.
    #
    # ORBIS_SKIP_RUNTIME_ENV disables this. The test suite sets it (see
    # conftest.py): override=True beats anything a test set up, so this
    # file would otherwise import a developer's personal tuning config
    # into every test process and make the local suite disagree with CI.
    # It did — a stray A2A_AUTH_TOKEN silently 401'd an a2a test that
    # passes in CI, and tests/test_skill_llm_resolution.py carries a
    # fixture that hand-clears LLM_MICRO_MODEL for the same reason.
    from pathlib import Path as _RTPath
    _rt_env = _RTPath.home() / "Library/Application Support/studio.protolabs.orbis/.env"
    if _rt_env.is_file() and not os.environ.get("ORBIS_SKIP_RUNTIME_ENV"):
        load_dotenv(_rt_env, override=True)
except ImportError:
    # Missing dotenv shouldn't crash boot — secrets just have to come
    # from the shell env in that case.
    pass

# Route HF downloads to the ORBIS cache directory before transformers
# imports anything. Honors ORBIS_CACHE_DIR / HF_HOME / MODEL_DIR in that
# order; falls back to a per-OS user cache dir when run as a bundled
# desktop binary. See agent/paths.py for the full resolution.
from agent.paths import configure_hf_home  # noqa: E402
_cache_dir = configure_hf_home()


def _emit_boot(stage: str, detail: str) -> None:
    """Print a boot-progress marker for the Rust shell to parse and forward to
    the UI loading gate. Stdout because the shell reads the sidecar pipe even
    while Python is GIL-stalled by a heavy import or model load — so progress
    keeps flowing during the slow steps. Defined up here (before the heavy
    imports) so the import phase can report progress too: a cold start spends
    ~80s importing torch / MLX / pipecat before anything else runs, and without
    these markers the loading screen sits on one line the whole time."""
    import json as _json

    try:
        print(f"ORBIS_BOOT {_json.dumps({'stage': stage, 'detail': detail})}", flush=True)
    except Exception:
        pass


# Import-phase progress. The UI shows an indeterminate bar for the "import"
# stage (it's deliberately not in the gate's STAGE_PROGRESS table) while the
# detail line tracks which subsystem is loading. pipecat (next) is the long pole.
_emit_boot("import", "Loading the voice pipeline…")

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pipecat.processors.frameworks.rtvi import (
    RTVIObserver,
    RTVIObserverParams,
)

_emit_boot("import", "Loading the agent…")

from voice.local_transport import LocalAudioTransport, audio_runtime_info  # noqa: F401 — audio_runtime_info re-exported for server.routers.system (app.audio_runtime_info, monkeypatched in tests)
from voice.sse_bus import sse_bus

from a2a_server import register_a2a_routes
from agent.delegates import DelegateRegistry
from agent.echo_guard import (
    EchoGuardState,
)
from agent.paths import get_voiceprint_path
from agent.speaker_gate import (
    SpeakerGate,
    StrangerAction,
    VoiceprintCorrupted,
    load_voiceprint,
)
from agent.filler import (
    FillerGenerator,
    audio_context_block,
    grounding_block,
    plan_block,
    recall_block,
    repair_block,
    tool_response_block,
    tool_use_block,
)
from agent.session_store import (
    load_last_summary,
)
from agent.tools import (
    build_text_tool_schemas,
    capabilities_block,
    run_text_tool,
)
from auth import load_users, user_registry  # noqa: F401 — user_registry re-exported for routers + tests (app.user_registry)
from auth.context import current_user_id
from agent.user_state import all_user_states, user_state_for

_emit_boot("import", "Loading speech + voice engines…")

from voice.stt import STT_BACKEND, prewarm as prewarm_stt, stt_emits_audio_tags
from voice.tts import TTS_BACKEND, prewarm as prewarm_tts

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("orbis")

# Quiet the per-inference noise that floods the sidecar log and buries the
# signal we tune against (STT/LLM/TTS timings, turn events). torch + kokoro
# emit a UserWarning on every istft call; phonemizer warns per line.
import warnings as _warnings  # noqa: E402
_warnings.filterwarnings("ignore", category=UserWarning)
_warnings.filterwarnings("ignore", category=FutureWarning)  # torch weight_norm etc.
logging.getLogger("phonemizer").setLevel(logging.ERROR)

PORT = int(os.environ.get("PORT", "7866"))
LLM_URL = os.environ.get("LLM_URL", f"http://localhost:{os.environ.get('VLLM_PORT', '8100')}/v1")
LLM_SERVED_NAME = os.environ.get("LLM_SERVED_NAME", "local")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "not-needed")
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "150"))
LLM_TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", "0.7"))

CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "config"))

# User roster — populated from Infisical first (when INFISICAL_CLIENT_ID
# is set), else config/users.yaml, else empty (single-user fallback).
# Gates every /api/* route via require_user.
load_users(CONFIG_DIR / "users.yaml")

# Delegate registry — A2A agents + OpenAI-compat endpoints the agent can
# hand off to via `delegate_to`. Loaded once at boot. Shared across users.
_DELEGATES_YAML = Path(os.environ.get("DELEGATES_YAML", "config/delegates.yaml"))
_DELEGATES = DelegateRegistry(_DELEGATES_YAML)

# Single ORBIS persona loaded from config/orbis.yaml (see agent/persona.py).
# Module-level cache refreshes via reload_persona().
from agent.persona import get_active_persona  # noqa: E402

# Memory backend — SQLite-embedded sessions + facts + personality + mood.
from memory import Memory  # noqa: E402

_memory: Memory | None = None
_native_transport: LocalAudioTransport | None = None
_native_pipeline_task: asyncio.Task | None = None


def get_memory() -> Memory:
    """Lazy memory handle — opens data/orbis.sqlite on first access."""
    global _memory
    if _memory is None:
        _memory = Memory()
        _memory.personality.seed_defaults()
    return _memory


def _active_skill(user_id: str = "default"):
    """Return the single ORBIS persona. Name kept (Skill-shaped signature)
    until all call sites are renamed to `get_active_persona()` directly."""
    return get_active_persona()


def _resolve_api_key(block: dict, *, what: str) -> str:
    """Resolve an api_key from an llm config block: a direct ``api_key`` →
    ``api_key_env`` indirection → the ``LLM_API_KEY`` placeholder default.

    Shared by the primary (``_resolve_skill_llm``) and fallback
    (``_resolve_fallback_llm``) paths so credential resolution has one
    source of truth.

    ``api_key_env`` is a footgun on the desktop app: a Finder/Dock launch
    gives the sidecar launchd's minimal env, so a shell export like
    ``OPENAI_API_KEY`` is NOT visible (this is why the Tauri shell
    hand-augments PATH). The var then silently resolves to the
    ``"not-needed"`` placeholder and the request 401s with no hint why.
    When ``api_key_env`` names a var that's unset, WARN loudly instead of
    swallowing it. Prefer a direct ``api_key`` in orbis.yaml — what the
    setup wizard writes.
    """
    if block.get("api_key"):
        return str(block["api_key"])
    env_var = block.get("api_key_env")
    if env_var:
        resolved = os.environ.get(str(env_var))
        if resolved:
            return resolved
        logger.warning(
            "[%s] api_key_env=%r but that env var is unset in the sidecar's "
            "environment (a Finder/Dock launch does not inherit shell "
            "exports). Falling back to a placeholder key — expect a 401. Set "
            "api_key directly in orbis.yaml instead.",
            what, str(env_var),
        )
    return LLM_API_KEY


def _wants_thinking_suppression(url: str, provider: str | None) -> bool:
    """Should this endpoint be sent
    ``chat_template_kwargs={"enable_thinking": False}``?

    That field is a **property of the endpoint** (of its chat template),
    so it is keyed off the resolved URL — never off *where* the URL came
    from. It used to be `bool(persona.llm.get("url"))`, i.e. "did the
    config name a URL", which meant the very same gateway URL behaved
    differently depending on whether it sat in `orbis.yaml` or in the
    `LLM_URL` env. From yaml, `enable_thinking=False` was never sent, so
    the Qwen-family models behind `protolabs/*` streamed raw
    chain-of-thought into `content` — and ORBIS speaks `content`, so the
    orb narrated its own tool-call planning out loud. Since the shipped
    `config/orbis.yaml` names the gateway URL, that was the out-of-box
    behavior.

    True for the vLLM/Qwen dialect (the protoLabs gateway; a self-hosted
    vLLM via `provider: vllm`). False everywhere else — OpenAI,
    Anthropic, Groq, Mistral et al. reject unknown body fields with a
    400. An explicit `persona.llm.extra_body` always wins over this.
    """
    if provider:
        return provider.lower() in ("vllm", "protolabs")
    return (urlparse(url).hostname or "").lower() == "api.proto-labs.ai"


def _resolve_skill_llm(skill) -> dict:
    """Resolve LLM routing for a skill. Single source of truth shared by
    the voice path (run_bot) and the inbound A2A path (text_agent).

    Precedence per-field: persona.llm.{url,model,api_key,api_key_env}
    overrides; env var fallback; finally module-level defaults.

    `extra_body`: an explicit user override always wins; otherwise it's
    derived from the resolved endpoint via `_wants_thinking_suppression`.

    Returns a dict with keys: url, model, api_key, extra_body, provider.
    Callers compose request kwargs from this. ``provider`` rides through
    to ``make_llm()`` so the adapter factory can route Ollama-native vs
    OpenAI-compat correctly.
    """
    skill_llm = (skill.llm if skill else None) or {}
    url = str(skill_llm.get("url") or LLM_URL)
    model = str(skill_llm.get("model") or LLM_SERVED_NAME)
    api_key = _resolve_api_key(skill_llm, what="llm")
    provider = skill_llm.get("provider")
    if "extra_body" in skill_llm:
        extra_body = skill_llm["extra_body"] or None
    elif _wants_thinking_suppression(url, provider):
        extra_body = {"chat_template_kwargs": {"enable_thinking": False}}
    else:
        extra_body = None
    # Two-model routing (orbis-3it): optional smart/fast split. When set,
    # make_llm builds a TwoModelOpenAILLMService that runs the
    # tool-decision turn on router_model and the post-tool narration turn
    # on content_model. Both None → single-model behavior unchanged.
    router_model = skill_llm.get("router_model") or os.environ.get("LLM_ROUTER_MODEL") or None
    content_model = skill_llm.get("content_model") or os.environ.get("LLM_CONTENT_MODEL") or None
    # Dedicated micro-task tier (fillers, opening acks, progress narration,
    # backchannels, proactive announcements). These are short, throwaway, and
    # fire constantly, so they want a FAST cheap endpoint — ideally a LOCAL
    # model (Ollama/MLX, ~250ms first token) so every line can be generated
    # live and natural instead of falling back to canned phrases. The micro
    # endpoint is decoupled from the main LLM: point `micro_url` at local
    # Ollama (http://127.0.0.1:11434/v1) with a small `micro_model` and the
    # main conversation still runs on the gateway. All three fields fall back
    # to the main endpoint, so unset = previous behavior (gateway).
    micro_url = skill_llm.get("micro_url") or os.environ.get("LLM_MICRO_URL") or url
    micro_model = (
        skill_llm.get("micro_model") or os.environ.get("LLM_MICRO_MODEL") or model
    )
    micro_api_key = (
        skill_llm.get("micro_api_key")
        or os.environ.get("LLM_MICRO_API_KEY")
        or (api_key if micro_url == url else "not-needed")
    )
    # A local/custom micro endpoint (Ollama, MLX server) rejects the gateway's
    # `chat_template_kwargs` extra_body — only send it when the micro tier
    # shares the main gateway URL.
    micro_extra_body = extra_body if micro_url == url else None
    return {
        "url": url,
        "model": model,
        "api_key": api_key,
        "extra_body": extra_body,
        "provider": provider,
        "router_model": router_model,
        "content_model": content_model,
        "micro_model": micro_model,
        "micro_url": micro_url,
        "micro_api_key": micro_api_key,
        "micro_extra_body": micro_extra_body,
    }


def _resolve_fallback_llm(skill) -> dict | None:
    """Resolve an OPTIONAL backup LLM for failover (orbis-1dd).

    Returns ``None`` when no fallback is configured — in which case the
    voice pipeline runs the single-LLM path unchanged (no LLMSwitcher,
    zero behavior or resource cost). Configure a backup to get automatic
    failover when the primary LLM errors (e.g. the cloud gateway is
    unreachable → fall through to a local MLX model so the orb still
    talks).

    Resolution precedence (first present wins):
      1. ``persona.llm.fallback`` — a dict with the same shape as
         ``persona.llm`` (``url`` / ``model`` / ``api_key`` /
         ``api_key_env`` / ``provider`` / ``extra_body``).
      2. ``LLM_FALLBACK_URL`` env (+ ``LLM_FALLBACK_MODEL``,
         ``LLM_FALLBACK_API_KEY`` or ``LLM_FALLBACK_API_KEY_ENV``,
         ``LLM_FALLBACK_PROVIDER``). The runtime ``.env`` tuning loop can
         flip this on without a rebuild.

    A bare ``mlx://`` URL (no model) resolves the model from
    ``LLM_FALLBACK_MODEL`` or the primary model — ``make_llm`` translates
    it through the ``mlx-community/`` org. Returns the same dict shape as
    ``_resolve_skill_llm`` so the caller builds it identically.
    """
    fb = (skill.llm.get("fallback") if skill and skill.llm else None) or {}
    if not fb:
        env_url = os.environ.get("LLM_FALLBACK_URL")
        if not env_url:
            return None
        fb = {
            "url": env_url,
            "model": os.environ.get("LLM_FALLBACK_MODEL"),
            "api_key": os.environ.get("LLM_FALLBACK_API_KEY"),
            "api_key_env": os.environ.get("LLM_FALLBACK_API_KEY_ENV"),
            "provider": os.environ.get("LLM_FALLBACK_PROVIDER"),
        }
    url = fb.get("url")
    if not url:
        return None
    model = str(fb.get("model") or LLM_SERVED_NAME)
    api_key = _resolve_api_key(fb, what="llm.fallback")
    # Same endpoint-capability rule as the primary: explicit override
    # wins, else derive from the resolved URL. (A fallback is typically
    # a local Ollama/MLX, which routes to an adapter that handles
    # `think` itself — see _resolve_ollama_think.)
    fb_provider = fb.get("provider")
    if "extra_body" in fb:
        extra_body = fb["extra_body"] or None
    elif _wants_thinking_suppression(str(url), fb_provider):
        extra_body = {"chat_template_kwargs": {"enable_thinking": False}}
    else:
        extra_body = None
    return {
        "url": str(url),
        "model": model,
        "api_key": api_key,
        "extra_body": extra_body,
        "provider": fb_provider,
    }


def _build_speaker_gate(sg_cfg: dict) -> SpeakerGate:
    """Construct the per-session SpeakerGate from persona behavior config.

    Schema (under ``persona.behavior.speaker_gate``):
      - ``enabled``: bool — defaults to True only when the block exists.
        Empty/missing block → resolved by ``_resolve_behavior_block`` to
        ``{enabled: True}``; a literal ``false`` disables.
      - ``voiceprint_path``: str — file containing the cached owner
        voiceprint. Default ``data/voiceprint.npy`` (relative to the
        ORBIS data dir). Missing file → owner-trust mode (preserves
        no-auth single-user deployment).
      - ``threshold``: float — cosine similarity floor for owner.
        Default 0.62 per the speechbrain ECAPA tuning in #35.
      - ``stranger_action``: ``"warn"`` | ``"refuse"`` | ``"delegate_guest"``.
        Labels the StrangerDetectedFrame; downstream consumers
        (currently none — wired in PR 1.3+) decide enforcement.

    A corrupt voiceprint file (vs simply missing) does NOT silently
    fall back — surfaces the error so the operator can re-enroll.
    """
    if not sg_cfg.get("enabled", True):
        return SpeakerGate(enabled=False)

    # Resolution: persona config → SPEAKER_GATE_VOICEPRINT_PATH env →
    # platform-aware default (~/Library/Application Support/orbis on
    # macOS etc., per agent/paths.py). The wizard enrollment endpoint
    # writes to the same default path so a fresh install Just Works.
    voiceprint_path = sg_cfg.get("voiceprint_path") or str(get_voiceprint_path())
    # threshold is user-configurable (config or runtime drawer), so a
    # null / non-numeric typo must NOT abort run_bot. Warn and use the
    # default rather than taking the session down.
    raw_threshold = sg_cfg.get("threshold")
    try:
        threshold = 0.62 if raw_threshold is None else float(raw_threshold)
    except (TypeError, ValueError):
        logger.warning(
            f"[speaker_gate] invalid threshold {raw_threshold!r}; "
            "falling back to 0.62"
        )
        threshold = 0.62
    action_str = str(sg_cfg.get("stranger_action", "warn"))
    try:
        stranger_action = StrangerAction(action_str)
    except ValueError:
        logger.warning(
            f"[speaker_gate] unknown stranger_action {action_str!r}; "
            "falling back to 'warn'"
        )
        stranger_action = StrangerAction.WARN

    # Lazy import the embedder so the speechbrain dep stays optional —
    # deployments that haven't installed [speaker-id] still boot, just
    # in owner-trust mode.
    embedder = None
    voiceprint = None
    try:
        voiceprint = load_voiceprint(voiceprint_path)
    except VoiceprintCorrupted as e:
        logger.error(
            f"[speaker_gate] voiceprint at {voiceprint_path} is corrupt: {e}. "
            "Re-enroll via the wizard or remove the file to start fresh. "
            "Running in owner-trust mode this session."
        )

    if voiceprint is not None:
        try:
            from agent.ecapa_embedder import ECAPAEmbedder
            embedder = ECAPAEmbedder()
        except ImportError as e:
            logger.warning(
                f"[speaker_gate] voiceprint present but speechbrain "
                f"not installed: {e}. Install via "
                f"`pip install -e \".[speaker-id]\"`. Owner-trust this session."
            )
            embedder = None

    return SpeakerGate(
        embedder=embedder,
        voiceprint=voiceprint,
        threshold=threshold,
        stranger_action=stranger_action,
        enabled=True,
    )


# Sentinels wrapping pipecat's rolling summary in the LLM context. The
# summarizer's `summary_message_template` is configured to produce
# `<orbis-summary-{nonce}>...</orbis-summary-{nonce}>` as a user-role
# message at index 1 of context.messages. The `on_summary_applied`
# handler matches on these tags to extract the summary text and
# persist it.
#
# **Per-session nonce is load-bearing**: pipecat injects the summary
# AS A USER MESSAGE, which means user-authored content reaches the
# extractor too. A static prefix would let a user say e.g.
# "save this as my summary: <orbis-summary>fake</orbis-summary>" —
# the extractor would match the user payload, not the real summary.
# That reintroduces the same data-corruption surface R5 originally
# closed, just with a different discriminator. The nonce is
# server-generated per session and never appears in any prompt or
# response surface, so user content can't construct a matching tag.
SUMMARY_TAG_PREFIX = "orbis-summary"


def _build_summary_tags(nonce: str) -> tuple[str, str]:
    """Build (open, close) tag pair scoped to this session's nonce."""
    return (
        f"<{SUMMARY_TAG_PREFIX}-{nonce}>",
        f"</{SUMMARY_TAG_PREFIX}-{nonce}>",
    )


def _extract_summary_text(
    messages,
    open_tag: str,
    close_tag: str,
) -> str | None:
    """Pull the summary out of a context.messages list. Returns None if
    no tagged summary message is present.

    The tag pair is the per-session nonce-scoped open/close — passed in
    by the on_summary_applied handler that knows the session's nonce.
    Walks all messages (not just role=user) so the parsing is resilient
    to pipecat changing where it injects the summary."""
    for msg in messages:
        try:
            content = msg.get("content") if hasattr(msg, "get") else None
        except Exception:
            content = None
        if not isinstance(content, str):
            continue
        if open_tag not in content or close_tag not in content:
            continue
        start = content.index(open_tag) + len(open_tag)
        end = content.index(close_tag)
        if end <= start:
            continue
        text = content[start:end].strip()
        if text:
            return text
    return None


def _recall_block(user_id: str) -> str:
    """Session-open memory callback. Composes a nudge block from:
      - the last 3 SQLite session summaries (structured), and
      - the rolling text summary produced by pipecat's summarizer
        (fallback when SQLite is empty — first-boot / fresh install).

    Formatting (incl. the #625 trust-boundary framing) lives in the shared
    ``agent.filler.recall_block``; this only loads the pieces from storage.
    """
    # Prior-N block from SQLite. Newest first, ~3 sessions keeps the
    # prompt affordable while still giving cross-session continuity.
    try:
        mem = get_memory()
        prior = mem.sessions.prior_n(3)
    except Exception as e:
        logger.warning(f"[memory] prior_n read failed: {e}")
        prior = []

    prior_sessions_xml = ""
    if prior:
        sessions_xml: list[str] = ["<prior_sessions>"]
        for row in prior:
            sid = row.get("session_id", "?")
            ended = row.get("ended_at", "?")
            final = (row.get("final_output") or "")[:400]
            sessions_xml.append(f'  <session id="{sid}" ended="{ended}">')
            if final:
                sessions_xml.append(f"    <final_output>{final}</final_output>")
            sessions_xml.append("  </session>")
        sessions_xml.append("</prior_sessions>")
        prior_sessions_xml = "\n".join(sessions_xml)

    # Fallback / complement: the rolling text summary file. Both pieces are
    # passed to the shared formatter, which wraps them in the trust-boundary
    # framing (#625) and is also imported by the eval harness.
    summary = load_last_summary(user_id) or ""
    return recall_block(summary=summary, prior_sessions_xml=prior_sessions_xml)


def _filler_gen_for(user_id: str) -> FillerGenerator:
    """Lazy per-user FillerGenerator. Each user owns their own LLM client
    + recency history; the settings are the per-user FillerSettings
    stored on UserState.

    Routes to the same LLM as the active persona — without this, the
    filler defaulted to env LLM_URL (localhost:8100/v1, vLLM) and
    spammed connection errors when only a remote gateway is configured.
    """
    state = user_state_for(user_id)
    if state.filler_generator is None:
        llm_cfg = _resolve_skill_llm(_active_skill(user_id))
        state.filler_generator = FillerGenerator(
            llm_url=llm_cfg["micro_url"],        # decoupled micro endpoint
            model=llm_cfg["micro_model"],        # dedicated micro-task tier
            api_key=llm_cfg["micro_api_key"],
            extra_body=llm_cfg["micro_extra_body"],
            settings=state.filler_settings,
        )
        logger.info(
            f"[filler] micro tier → {llm_cfg['micro_url']} model={llm_cfg['micro_model']}"
        )
    return state.filler_generator


# ---------------------------------------------------------------------------
# Audio + turn enhancements (echo guard already imported above)
# Env-driven so the heavy/optional deps stay opt-in.
# ---------------------------------------------------------------------------

NOISE_FILTER = os.environ.get("NOISE_FILTER", "off").lower()  # off | rnnoise
SMART_TURN = os.environ.get("SMART_TURN", "off").lower()      # off | local
# Shared secret for the unauth /api/inbox ingest path; read by
# server/routers/comms.py as app.INBOX_INGEST_TOKEN (monkeypatched in tests).
INBOX_INGEST_TOKEN = os.environ.get("INBOX_INGEST_TOKEN", "")


def _build_audio_in_filter():
    """Return a BaseAudioFilter for TransportParams.audio_in_filter, or None."""
    if NOISE_FILTER == "rnnoise":
        try:
            from pipecat.audio.filters.rnnoise_filter import RNNoiseFilter
        except ImportError as e:
            logger.error(
                "NOISE_FILTER=rnnoise but pipecat[rnnoise] not installed: %s", e
            )
            return None
        logger.info("Audio in-filter: RNNoise")
        return RNNoiseFilter()
    if NOISE_FILTER != "off":
        logger.warning(f"Unknown NOISE_FILTER={NOISE_FILTER!r}; disabling")
    return None


def _build_user_turn_strategies():
    """Return a `UserTurnStrategies` object wrapping a smart-turn analyzer,
    or None for naive VAD-only behaviour. Smart-turn discriminates real
    turn-ends from mid-thought pauses + echo bleed."""
    if SMART_TURN in ("local", "v3"):
        try:
            from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import (
                LocalSmartTurnAnalyzerV3,
            )
            from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy
            from pipecat.turns.user_turn_strategies import UserTurnStrategies
        except ImportError as e:
            logger.error(
                "SMART_TURN=local but pipecat[local-smart-turn] not installed: %s", e
            )
            return None
        logger.info("Turn analyzer: LocalSmartTurnAnalyzerV3 (bundled CPU model)")
        return UserTurnStrategies(
            stop=[TurnAnalyzerUserTurnStopStrategy(turn_analyzer=LocalSmartTurnAnalyzerV3())]
        )
    if SMART_TURN != "off":
        logger.warning(f"Unknown SMART_TURN={SMART_TURN!r}; disabling")
    return None


# Echo-guard state — shared across observer and suppressor for THIS session.
# Module-level since pipeline is single-tenant for now.
_ECHO_STATE = EchoGuardState()

# Simple in-process counters for /api/metrics. Reset on process restart.
_METRICS: dict = {
    "boot_at": time.time(),
    "sessions_total": 0,
    "sessions_active": 0,
    "a2a_inbound_total": 0,
    "tool_calls_total": 0,
    "tool_calls_by_name": {},
}


def _resolve_behavior_block(raw) -> dict:
    """Normalize a skill.behavior sub-block into {enabled: bool, ...overrides}.

    Accepts:
      - None / missing    → enabled with defaults
      - False             → disabled
      - True              → enabled with defaults
      - dict              → enabled (unless dict has 'enabled: false'), overrides passed through
    """
    if raw is False:
        return {"enabled": False}
    if raw is True or raw is None:
        return {"enabled": True}
    if isinstance(raw, dict):
        out = dict(raw)
        out.setdefault("enabled", True)
        return out
    return {"enabled": True}


def _fleet_block(delegates) -> str:
    """Dynamic roster of the agents THIS session can actually reach via
    delegate_to — the single source of truth so the LLM never invents or
    denies the wrong fleet. Built from the live (post-filter) registry, so
    it always matches the delegate_to tool's real targets instead of a
    hard-coded guess in the persona prompt."""
    try:
        items = list(delegates.all()) if delegates is not None else []
    except Exception:
        items = []
    if not items:
        return (
            "## YOUR FLEET\n\n"
            "No agents are wired up right now, so you cannot hand work off. "
            "Answer everything yourself; never offer to delegate or claim to "
            "reach another agent."
        )
    roster = "\n".join(
        f"- {d.name} — {' '.join((d.description or '').split())}" for d in items
    )
    return (
        "## YOUR FLEET\n\n"
        "These are the ONLY agents you can reach, via the delegate_to tool. "
        "When a request clearly fits one, hand it off; otherwise just answer "
        "yourself. Never claim to reach an agent that is not on this list, and "
        "do not invent capabilities beyond what each line says:\n\n"
        f"{roster}"
    )


def _effective_prompt(
    skill, tts_backend: str, *, verbosity, user_id: str, delegates=None,
    tools_schema=None,
) -> str:
    """Compose the system prompt = persona + TOOL USE block.

    ``skill`` is kept as a positional arg for call-site compatibility
    with the skills-system era. It accepts the Persona dataclass
    (duck-typed). SYSTEM_PROMPT env override is applied inside the
    Persona loader, so skill.system_prompt already reflects it.
    """
    from agent.personality import render_personality_block
    from agent.neglect import apply_soft_neglect
    base = skill.system_prompt
    plan = plan_block(verbosity)
    recall = _recall_block(user_id)
    neglect_nudge = ""
    inbox_block = ""
    personality = ""
    try:
        mem = get_memory()
        # Emotional/companion layer (personality mood + soft-neglect drift) is
        # PAUSED by default — set ORBIS_EMOTIONAL_LAYER=1 to re-enable. The
        # current focus is ORBIS as a capable AGENT; the neglect mood was
        # making it act guarded/sulky, which got in the way. Recall, inbox, and
        # the user-name block stay (they're agent-useful, not mood).
        if os.environ.get("ORBIS_EMOTIONAL_LAYER", "0") == "1":
            # Neglect adjusts mood; render reads mood — so neglect runs first.
            _days, neglect_nudge = apply_soft_neglect(mem)
            personality = render_personality_block(mem)
        inbox_block = _render_inbox_pending_block(mem)
    except Exception as e:
        logger.warning(f"[personality] render failed: {e}")

    # User-addressing block — tells the orb who the user is by name
    # so greetings / recall feel personal. Empty user_name = no block.
    user_block = ""
    user_name = (getattr(skill, "user_name", "") or "").strip()
    if user_name:
        user_block = (
            f"## USER\n\n"
            f"The user's name is {user_name}. Use it occasionally — "
            f"not in every turn, but when a greeting, callback, or "
            f"gentle correction calls for it."
        )

    return (
        base
        # MEMORY (recall) renders BEFORE the capability/tool blocks, not after:
        # it carries historical user/model claims, and the code-derived
        # capabilities below must be the authoritative, most-recent word on what
        # the agent can do. Injected last, a summary like "orb control is
        # wedged" read as capability truth and suppressed the tool for the rest
        # of the session (#625). The block's own framing states the rule too.
        + (("\n\n" + recall) if recall else "")
        + "\n\n"
        + tool_use_block(verbosity, tts_backend)
        + "\n\n"
        + tool_response_block(verbosity)
        # Code-driven capability list — generated from the tools actually
        # registered this session, so it never drifts from the code. Tells a
        # small/fast model to CALL the tool rather than just promise to.
        + (("\n\n" + _caps) if (_caps := capabilities_block(tools_schema)) else "")
        + "\n\n"
        + _fleet_block(delegates)
        # Honesty guardrail — pairs with the fleet block: delegate-or-admit,
        # never fabricate a fact or force-fit an unrelated agent. Measured by
        # evals/ (grounding_invented_delegate et al.).
        + "\n\n"
        + grounding_block()
        + (("\n\n" + plan) if plan else "")
        + "\n\n"
        + repair_block()
        # Audio-context block teaches the LLM what the [audio] line
        # AudioTagsTap injects means — but ONLY when the active STT
        # backend can actually emit one. Only SenseVoice carries
        # EmotionFrame; on local/parakeet/openai the annotation never
        # arrives.
        #
        # This used to be unconditional, on the theory that "the block
        # tells the LLM to ignore the annotation when missing, so this
        # is safe-by-default". It isn't: a small model handed a literal
        # worked example doesn't ignore it, it COPIES it. The orb read
        # `[live] audio=emotion=neutral lang=en speaker=owner ...` aloud
        # to the user on 2026-07-15 — a format it had only ever seen in
        # this block, on a backend that emits no tags at all.
        # `skill` is duck-typed (see this function's docstring) — callers
        # pass Persona or a bare namespace, so read `stt` defensively.
        + (
            ("\n\n" + audio_context_block())
            if stt_emits_audio_tags((getattr(skill, "stt", None) or {}).get("backend"))
            else ""
        )
        + (("\n\n" + user_block) if user_block else "")
        + (("\n\n" + personality) if personality else "")
        + (("\n\n## RETURN\n\n" + neglect_nudge) if neglect_nudge else "")
        + (("\n\n" + inbox_block) if inbox_block else "")
    )


def _render_inbox_pending_block(mem) -> str:
    """Surface now-priority unread inbox messages at session start."""
    try:
        msgs = mem.inbox.list_unread(priority_floor="now", limit=10)
    except Exception:
        return ""
    if not msgs:
        return ""

    try:
        mem.inbox.mark_delivered([m["id"] for m in msgs])
    except Exception:
        pass

    lines: list[str] = [
        "## PENDING NOTIFICATIONS",
        "",
        (
            "External systems pushed these messages to your inbox while "
            "you were offline. Surface them at a natural break; do not "
            "lead with them unless they are urgent. The user can also "
            "ask explicitly ('anything new?')."
        ),
        "",
    ]
    for m in msgs:
        sender = m.get("sender") or "unknown"
        subject = m.get("subject") or ""
        body = m.get("body") or ""
        snippet = body[:300] + ("..." if len(body) > 300 else "")
        lines.append(f"- from {sender}: {subject}")
        lines.append(f"  {snippet}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Text-only agent — used by inbound A2A (no voice, no tools, one-shot).
# Keeps dependence on the pipeline decoupled so callers can hit /a2a even
# when no native voice session is active.
# ---------------------------------------------------------------------------

from openai import AsyncOpenAI

# Cache by (url, key) so repeated A2A turns don't rebuild the underlying
# httpx connection pool. Keyed on the resolved tuple — when persona.llm
# overrides land, we naturally route to a different cached client.
_text_clients: dict[tuple[str, str], AsyncOpenAI] = {}
_A2A_CONTEXTS: dict[str, list[dict]] = {}
_A2A_MAX_TURNS = int(os.environ.get("A2A_MAX_TURNS", "10"))


def _get_text_client(url: str, api_key: str) -> AsyncOpenAI:
    """Return a cached AsyncOpenAI for this (url, key). Honors per-skill
    LLM overrides — the voice path and A2A path now hit the same
    configured endpoint."""
    cache_key = (url, api_key)
    client = _text_clients.get(cache_key)
    if client is None:
        client = AsyncOpenAI(api_key=api_key, base_url=url)
        _text_clients[cache_key] = client
    return client


_TEXT_REACT_MAX_ITERATIONS = int(os.environ.get("TEXT_AGENT_MAX_ITER", "3"))
# Which user to attribute inbound A2A traffic to. A2A auth is separately
# gated by A2A_AUTH_TOKEN (shared-secret across the fleet); this var picks
# an ORBIS user whose persona / memory / verbosity the inbound turn
# should read from.
_A2A_USER_ID = os.environ.get("A2A_USER_ID", "default")

# worldstate-delta-v1 source: state-mutating tools the inbound turn can call,
# mapped to their (domain, op). Calling these changes ORBIS's world, which the
# hub wants as telemetry. Read-only / delegating tools aren't here.
_A2A_MUTATING_TOOLS = {
    "schedule_reminder": ("reminders", "add"),
    "schedule_recurring_reminder": ("reminders", "add"),
    "cancel_reminder": ("reminders", "remove"),
}
# Substrings that mark a tool result as a failed mutation — skip the delta then.
_A2A_MUTATION_FAIL_MARKERS = ("couldn't", "could not", "failed", "no reminder", "error")


def _worldstate_delta_for(name: str, args: dict, result: str) -> dict | None:
    """A worldstate-delta {domain, path, op, value} for a successful mutation,
    or None for read-only tools / failed mutations."""
    spec = _A2A_MUTATING_TOOLS.get(name)
    if spec is None:
        return None
    if any(m in (result or "").lower() for m in _A2A_MUTATION_FAIL_MARKERS):
        return None
    domain, op = spec
    path = str(args.get("text") or args.get("id") or args.get("query") or name)
    return {"domain": domain, "path": path, "op": op, "value": args}


async def text_stream_factory(
    message: str,
    context_id: str,
    *,
    resume: bool = False,
    caller_trace: dict | None = None,
):
    """Inbound A2A turn as a producer-event stream for the a2a-sdk executor.

    Bounded ReAct loop (same brain the voice side uses: calculator, datetime,
    web_search, delegate_to, minus async tools that need a live voice session).
    Yields ``(event_type, payload)`` tuples the ``OrbisAgentExecutor`` maps onto
    the SDK event queue + the protoLabs extensions:

      ("usage", {...})       per LLM call  → cost-v1
      ("tool_start"/"tool_end", {...})     → tool-call-v1
      ("done", reply)        terminal

    Loop is capped at TEXT_AGENT_MAX_ITER (default 3); on exhaustion the last
    text (possibly empty) is the answer.
    """
    import json as _json

    _METRICS["a2a_inbound_total"] += 1
    user_id = _A2A_USER_ID
    current_user_id.set(user_id)
    state = user_state_for(user_id)
    skill = _active_skill(user_id)
    session_id = f"a2a:{context_id}"
    history = _A2A_CONTEXTS.setdefault(session_id, [])
    history.append({"role": "user", "content": message})

    # Respect per-skill delegate filter for inbound A2A too.
    session_delegates = _DELEGATES.filtered(skill.delegates if skill else None)

    # System prompt shared with the voice path — TOOL USE, response shape,
    # plan, repair blocks all apply equally to a text reply.
    messages: list[dict] = [
        {
            "role": "system",
            "content": _effective_prompt(
                skill, TTS_BACKEND,
                verbosity=state.filler_settings.verbosity,
                user_id=user_id,
                delegates=session_delegates,
            ),
        },
        *history[-(_A2A_MAX_TURNS * 2):],
    ]
    tools_openai = build_text_tool_schemas(session_delegates)
    # Resolve persona.llm overrides — voice + A2A share routing via
    # _resolve_skill_llm so a custom config/orbis.yaml LLM applies to both.
    llm_cfg = _resolve_skill_llm(skill)
    client = _get_text_client(llm_cfg["url"], llm_cfg["api_key"])

    reply = ""
    hit_max = False  # set in the loop's else when the step budget is exhausted
    for _ in range(max(1, _TEXT_REACT_MAX_ITERATIONS)):
        kwargs: dict = {
            "model": llm_cfg["model"],
            "messages": messages,
            "max_tokens": skill.max_tokens,
            "temperature": skill.temperature,
        }
        if llm_cfg["extra_body"] is not None:
            kwargs["extra_body"] = llm_cfg["extra_body"]
        if tools_openai:
            kwargs["tools"] = tools_openai
            kwargs["tool_choice"] = "auto"
        r = await client.chat.completions.create(**kwargs)

        # cost-v1: token usage per LLM call (previously discarded).
        u = getattr(r, "usage", None)
        if u is not None:
            yield ("usage", {
                "input_tokens": int(getattr(u, "prompt_tokens", 0) or 0),
                "output_tokens": int(getattr(u, "completion_tokens", 0) or 0),
                "model": llm_cfg["model"],
            })

        msg = r.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None) or []

        if not tool_calls:
            reply = (msg.content or "").strip()
            break

        # Carry the assistant turn (with tool_calls) + each tool result
        # into the next iteration so the model sees the full trace.
        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments or "{}",
                    },
                }
                for tc in tool_calls
            ],
        })
        for tc in tool_calls:
            try:
                args = _json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}
            logger.info(f"[a2a/react] tool={tc.function.name} args={args!r}")
            # tool-call-v1: started → completed around each tool.
            yield ("tool_start", {"id": tc.id, "name": tc.function.name, "input": args})
            result = await run_text_tool(
                tc.function.name,
                args,
                delegates=session_delegates,
            )
            yield ("tool_end", {"id": tc.id, "name": tc.function.name, "output": result})
            # worldstate-delta-v1: a state-mutating tool changed ORBIS's world.
            _delta = _worldstate_delta_for(tc.function.name, args, result)
            if _delta is not None:
                yield ("delta", _delta)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })
    else:
        hit_max = True
        logger.warning(
            f"[a2a/react] hit max iterations ({_TEXT_REACT_MAX_ITERATIONS}) — "
            "returning last partial"
        )

    history.append({"role": "assistant", "content": reply})
    if len(history) > _A2A_MAX_TURNS * 2:
        del history[: len(history) - _A2A_MAX_TURNS * 2]

    # confidence-v1: a coarse, honest self-assessment of how cleanly this turn
    # resolved (completion-based — ORBIS has no logprob/confidence signal, so
    # this reflects whether it answered within its step budget).
    if reply:
        conf, expl = (
            (0.5, "answered but hit the step limit")
            if hit_max
            else (0.9, "resolved within the step budget")
        )
    else:
        conf, expl = 0.2, "produced no answer"
    yield ("confidence", {"confidence": conf, "explanation": expl})
    yield ("done", reply)

STATIC_DIR = Path(__file__).parent / "static"
WEB_DIST = Path(__file__).parent / "web" / "dist"
# FRONTEND=react serves the SPA built from web/; FRONTEND=vanilla keeps the
# legacy static/index.html. `auto` (default) picks react when web/dist exists.
FRONTEND = os.environ.get("FRONTEND", "auto").lower()



# ---------------------------------------------------------------------------
# SSE state observer (subclass of pipecat's RTVIObserver)
# ---------------------------------------------------------------------------

class SseBusObserver(RTVIObserver):
    """RTVI observer subclass that fans every message out via /api/events SSE.

    Subclassing ``RTVIObserver`` keeps us aligned with pipecat's
    canonical event vocabulary — the same one ``pipecat-client-react``
    speaks — so when we eventually adopt that client, the wire format
    is already compatible. We just translate each RTVI message into
    our existing SSE event names instead of pushing it into a WebRTC
    data channel.

    Pre-2026-04-28 this was a hand-rolled Frame observer that
    duplicated the RTVIObserver mapping logic against ``LLMTextFrame``
    /  ``BotStartedSpeakingFrame`` / etc. directly. The third research
    stream (see ``docs/internal/native-audio-direction.md``) flagged that as
    forking the RTVI vocabulary; this rewrite keeps the dispatch
    surface in pipecat's hands and only customizes the egress.

    Frontend wire format (unchanged) — these are what ``useVoiceBridge``
    on the React side subscribes to:

        bot-state   {"state": "idle"|"listening"|"thinking"|"speaking"}
        transcript  {"source": "user"|"bot", "text": str, "final": bool}
        session     {"event": "start"|"end", "session_id": str?}
    """

    def __init__(self, rtvi, *, params: RTVIObserverParams | None = None) -> None:
        super().__init__(rtvi, params=params or RTVIObserverParams())
        self._bot_text_buf: list[str] = []
        # Debounce the speaking→idle transition: the native output transport
        # doesn't emit BotStarted/StoppedSpeakingFrame, so we derive "speaking"
        # from the TTS lifecycle (BotTTSStarted/Stopped) — which fires per
        # sentence. Without debounce the pill would flicker speaking↔idle
        # between sentences mid-answer.
        self._idle_task: asyncio.Task | None = None

    def _cancel_idle(self) -> None:
        if self._idle_task is not None and not self._idle_task.done():
            self._idle_task.cancel()
        self._idle_task = None

    def _schedule_idle(self) -> None:
        self._cancel_idle()

        async def _go_idle() -> None:
            try:
                await asyncio.sleep(0.6)
            except asyncio.CancelledError:
                return
            await sse_bus.publish("bot-state", {"state": "idle"})

        self._idle_task = asyncio.create_task(_go_idle())

    async def send_rtvi_message(self, model, exclude_none: bool = True) -> None:
        # Skip the WebRTC transport push (no transport in native mode
        # anyway — super() guards on ``self._rtvi``, but we'd just be
        # paying the dispatch cost). Translate to SSE instead.
        await self._fan_to_sse(model)

    async def _fan_to_sse(self, model) -> None:
        # Lazy import keeps the message classes off the module-load path.
        from pipecat.processors.frameworks.rtvi.models import (
            BotLLMStartedMessage,
            BotLLMTextMessage,
            BotStartedSpeakingMessage,
            BotStoppedSpeakingMessage,
            BotTTSStartedMessage,
            BotTTSStoppedMessage,
            UserStartedSpeakingMessage,
            UserTranscriptionMessage,
        )
        if isinstance(model, BotStartedSpeakingMessage):
            self._cancel_idle()
            self._bot_text_buf.clear()
            await sse_bus.publish("bot-state", {"state": "speaking"})
        elif isinstance(model, BotStoppedSpeakingMessage):
            if self._bot_text_buf:
                text = "".join(self._bot_text_buf)
                await sse_bus.publish(
                    "transcript",
                    {"source": "bot", "text": text, "final": True},
                )
                self._bot_text_buf.clear()
            await sse_bus.publish("bot-state", {"state": "idle"})
        elif isinstance(model, BotTTSStartedMessage):
            # The native output transport (voice/local_transport.py) is a thin
            # FrameProcessor, not BaseOutputTransport, so it never emits
            # BotStarted/StoppedSpeakingFrame. Derive "speaking" from the TTS
            # lifecycle instead; the debounced idle below bridges the
            # per-sentence TTSStopped gaps so the pill doesn't flicker.
            self._cancel_idle()
            await sse_bus.publish("bot-state", {"state": "speaking"})
        elif isinstance(model, BotTTSStoppedMessage):
            self._schedule_idle()
        elif isinstance(model, UserStartedSpeakingMessage):
            self._cancel_idle()
            await sse_bus.publish("bot-state", {"state": "listening"})
        elif isinstance(model, BotLLMStartedMessage):
            self._cancel_idle()
            await sse_bus.publish("bot-state", {"state": "thinking"})
        elif isinstance(model, UserTranscriptionMessage):
            data = model.data
            if data and getattr(data, "text", None):
                await sse_bus.publish(
                    "transcript",
                    {"source": "user", "text": data.text, "final": bool(getattr(data, "final", True))},
                )
        elif isinstance(model, BotLLMTextMessage):
            # Accumulate bot response text; emitted as one transcript
            # on BotStoppedSpeakingMessage above.
            data = model.data
            if data and getattr(data, "text", None):
                self._bot_text_buf.append(data.text)


def prewarm_llm() -> None:
    try:
        httpx.post(
            f"{LLM_URL}/chat/completions",
            json={
                "model": LLM_SERVED_NAME,
                "messages": [{"role": "user", "content": "Hi"}],
                "max_tokens": 1,
                "temperature": 0,
                "chat_template_kwargs": {"enable_thinking": False},
            },
            headers={"Authorization": f"Bearer {LLM_API_KEY}"} if LLM_API_KEY else {},
            timeout=30.0,
        )
        logger.info("LLM warm")
    except Exception as e:
        # Expected when the configured LLM is a remote gateway that doesn't
        # need (or want) a boot-time warmup ping, or a local vLLM that isn't
        # up. Not warning-worthy — the LLM still works at request time.
        logger.info(f"LLM prewarm skipped ({type(e).__name__})")


def prewarm_all() -> None:
    logger.info(f"Prewarming (tts_backend={TTS_BACKEND})")
    # Real boot stages — each marker is emitted as that component begins
    # loading, so the UI loading screen reflects actual work, not a timer.
    # Each step is guarded so a failure never strands the loading gate
    # (which releases on the final "ready" marker).
    # Keep the word "model" in the local-load strings — the UI loading gate
    # sniffs /loading…model/ to show the "first launch loads local models" note.
    stt_detail = {
        "parakeet": "Loading on-device speech model (Parakeet)…",
        "sensevoice": "Loading on-device speech model (SenseVoice)…",
        "openai": "Connecting speech recognition…",
    }.get(STT_BACKEND, "Loading on-device speech model (Whisper)…")
    tts_detail = (
        "Loading on-device voice model (Kokoro)…"
        if TTS_BACKEND == "kokoro"
        else "Loading speech synthesis…"
    )
    # Defer the on-device STT/TTS download until the user opts in via the setup
    # wizard's "voice models" step (voice.local_models == "on_device"). Keeps a
    # fresh boot from silently pulling ~900 MB before the user has chosen; "byo"
    # means they'll configure their own backend in Settings. Cloud backends
    # (openai/elevenlabs/fish) carry no big local download, so they always warm.
    try:
        from agent.config_store import read_config

        _vm = (read_config().get("voice") or {}).get("local_models")
    except Exception:  # noqa: BLE001
        _vm = None
    _opted_in = _vm == "on_device"
    _local_stt = STT_BACKEND in ("local", "parakeet", "sensevoice")
    _local_tts = TTS_BACKEND == "kokoro"

    for stage, detail, fn in (
        ("stt", stt_detail, prewarm_stt),
        ("tts", tts_detail, prewarm_tts),
        ("llm", "Warming up the language model…", prewarm_llm),
    ):
        is_local = (stage == "stt" and _local_stt) or (stage == "tts" and _local_tts)
        if is_local and not _opted_in:
            logger.info(f"prewarm {stage} deferred — on-device models not opted in")
            _emit_boot(stage, "On-device speech loads on first use…")
            continue
        _emit_boot(stage, detail)
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"prewarm {stage} failed: {e}")
    _emit_boot("ready", "Ready")


# ---------------------------------------------------------------------------
# FastAPI
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _native_pipeline_task, _native_transport

    _native_pipeline_task = None
    _native_transport = None

    # Prewarm off the event loop so the startup handshake isn't blocked by
    # TTS / STT / LLM cold starts; we just begin work in the background.
    asyncio.get_running_loop().run_in_executor(None, prewarm_all)

    # Curator task — 90-day half-life decay on facts + prune below 0.2
    # confidence, plus a session-transcript retention sweep (#482: full
    # transcripts are JSON blobs that otherwise grow unbounded). Runs once at
    # boot, then weekly. Uses Memory.facts.decay_and_prune() + sessions.prune().
    async def _curator_loop() -> None:
        while True:
            try:
                mem = get_memory()
                result = mem.facts.decay_and_prune()
                if result.get("decayed") or result.get("pruned"):
                    logger.info(
                        f"[curator] decayed={result['decayed']} pruned={result['pruned']}"
                    )
                mem.sessions.prune()
            except Exception as e:
                logger.warning(f"[curator] run failed: {e}")
            # Sleep 7 days. Cancelled cleanly on lifespan shutdown.
            await asyncio.sleep(7 * 24 * 3600)

    curator_task = asyncio.create_task(_curator_loop(), name="orbis-curator")

    # Reminder scheduler (orbis-2a0) — the orb's first proactive-initiation
    # surface. Polls the reminders table and speaks due reminders at the next
    # natural pause via the live DeliveryController (TIME_SENSITIVE +
    # cooldown_key, so B1 keeps it from double-firing). Stale reminders past
    # the staleness window are dropped silently rather than vocalised late.
    from agent.scheduler import ReminderScheduler
    _reminder_scheduler = ReminderScheduler(
        memory_provider=get_memory,
        delivery_provider=lambda: user_state_for(_A2A_USER_ID).active_delivery,
    )
    reminder_task = asyncio.create_task(
        _reminder_scheduler.run(), name="orbis-reminders"
    )

    from agent.delegates import health_loop as _delegate_health_loop
    delegate_health_task = asyncio.create_task(
        _delegate_health_loop(_DELEGATES), name="orbis-delegate-health",
    )

    # mDNS advertise (protoAgent ADR 0042 §I interop) — announce ORBIS as a
    # `_protoagent._tcp` service so fleet agents discover it the way ORBIS
    # discovers them. Skipped when bound to loopback: the advert carries the
    # LAN IP, and a loopback-bound server is unreachable there — advertising
    # it would surface a dead entry in every sibling's scan. Off the loop
    # (to_thread): sync zeroconf on a running loop blocks it ~10s at boot.
    from agent import discovery as _discovery
    _bound_host = os.environ.get("ORBIS_BOUND_HOST", "")
    _bound_port = int(os.environ.get("ORBIS_BOUND_PORT", "0") or 0)
    if _bound_port and _bound_host not in ("127.0.0.1", "localhost", ""):
        from a2a_server import AGENT_NAME as _agent_name
        try:
            await asyncio.to_thread(_discovery.advertise, _agent_name, _bound_port)
        except Exception:
            logger.exception("[discovery] mDNS advertise failed")
    else:
        logger.info(
            "[discovery] mDNS advertise skipped (loopback or unknown bind) — "
            "discovery of other agents still works"
        )

    # Start the persistent native voice pipeline. The Rust shell sets
    # ORBIS_AUDIO_SOCK to the unix socket the native audio engine is listening
    # on — direct `python app.py` runs without that env var (e.g. the
    # test suite, A2A-only deployments) skip the pipeline cleanly.
    sock_path = os.environ.get("ORBIS_AUDIO_SOCK", "")
    if sock_path:
        native_transport = LocalAudioTransport(sock_path=sock_path)
        _native_transport = native_transport
        _native_pipeline_task = asyncio.create_task(
            run_bot(transport=native_transport, user_id="default"),
            name="orbis-native-pipeline",
        )

        def _on_pipeline_done(task: asyncio.Task) -> None:
            # Without this the pipeline can die (build error, unhandled
            # frame-processing exception escaping the runner) and voice goes
            # silently dead for the rest of the process life — the exception
            # only surfaces as "Task exception was never retrieved" at GC /
            # shutdown. Log it loudly the moment it happens. /healthz already
            # reports pipeline_running=false for an external watcher. See
            # audit M2.
            if task.cancelled():
                return
            exc = task.exception()
            if exc is not None:
                logger.error(
                    "[native audio] persistent pipeline EXITED with an "
                    "exception — voice is now dead until relaunch",
                    exc_info=exc,
                )
            else:
                logger.warning(
                    "[native audio] persistent pipeline returned unexpectedly "
                    "(no exception) — voice is now idle until relaunch"
                )

        _native_pipeline_task.add_done_callback(_on_pipeline_done)
        logger.info(f"[native audio] persistent pipeline started (sock={sock_path})")
    else:
        logger.info(
            "[native audio] ORBIS_AUDIO_SOCK not set — native pipeline skipped "
            "(this is normal for `python app.py` outside the Tauri shell)"
        )

    try:
        yield
    finally:
        if _native_pipeline_task and not _native_pipeline_task.done():
            _native_pipeline_task.cancel()
            try:
                await _native_pipeline_task
            except (asyncio.CancelledError, Exception):
                pass
        _native_pipeline_task = None
        _native_transport = None
        for t in (curator_task, reminder_task, delegate_health_task):
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        # Withdraw the mDNS advertisement. Off the loop — same deadlock as
        # advertise (zc.close() posts to and waits on the loop it's called from).
        try:
            await asyncio.to_thread(_discovery.stop_advertise)
        except Exception:
            pass


app = FastAPI(title="ORBIS", lifespan=lifespan)


def _delegate_health_payload(delegate, *, public: bool = False) -> dict:
    """Serialize a delegate with cached reachability state."""
    h = _DELEGATES.health(delegate.name)
    payload: dict = {
        "name": delegate.name,
        "type": delegate.type,
        "ok": h.ok if h else None,
        "latency_ms": h.latency_ms if h else None,
        "last_checked": h.last_checked if h else None,
        "consecutive_failures": h.consecutive_failures if h else 0,
    }
    if not public:
        payload["last_error"] = h.last_error if h else None
    return payload




# Speaker-verification enrollment (#35 PR 1.3) ----------------------------
#
# The wizard captures ~10s of owner audio + posts it here. We decode +
# encode via ECAPAEmbedder and save the resulting embedding so the
# speaker_gate has a voiceprint to compare against on the next session.
#
# Single-owner deployment: the voiceprint is shared across all auth'd
# clients of this install. Re-enrolling overwrites; remove the file to
# revert to owner-trust mode.

def _switch_live_voice(voice_id: str, *, user_id: str | None = None) -> dict:
    """Switch the voice on the LIVE TTS service so the next spoken line uses
    it — no restart. Works for backends whose service exposes ``set_voice``
    (Kokoro reads the voice per-utterance). Returns a status dict; never
    raises so callers can surface the message."""
    voice_id = (voice_id or "").strip()
    if not voice_id:
        return {"ok": False, "error": "voice is required"}
    state = user_state_for(user_id or _A2A_USER_ID)
    tts = state.active_tts
    if tts is None:
        return {"ok": False, "error": "no live voice session — connect first"}
    setter = getattr(tts, "set_voice", None)
    if not callable(setter):
        return {
            "ok": False,
            "error": f"{type(tts).__name__} doesn't support live voice switching",
        }
    return setter(voice_id)


def _apply_persona_switch(persona) -> dict:
    """Live-apply the (re)composed active persona (epic #611 P2):
    refresh every live voice session (prompt / LLM / voice / filler via
    the run_bot ``refresh_persona`` hook), and sync the persona's orb
    into the yaml ``orb:`` block — the orb's boot source of truth, same
    posture as /api/orb/select_starter. Returns the applies/notes/viz
    payload shared by the API response and the SSE announcement."""
    from agent.config_store import merge_patch
    notes: list[str] = []
    applied_live = False
    for st in all_user_states():
        fn = getattr(st, "refresh_persona", None)
        if callable(fn):
            try:
                r = fn()
                applied_live = applied_live or bool(r.get("ok"))
                notes.extend(r.get("notes") or [])
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[personas/hot] session refresh failed: {e}")
    viz: dict = {
        k: v
        for k, v in {
            "variant": persona.orb_variant,
            "palette": persona.orb_palette,
            "params": dict(persona.orb_params or {}),
        }.items()
        if v is not None
    }
    if viz.get("variant"):
        try:
            merge_patch({"orb": viz})
        except ValueError as e:
            logger.warning(f"[personas] orb sync failed: {e}")
    return {
        "applies": "live" if applied_live else "restart",
        "notes": notes,
        "viz": viz,
    }


def _reconfigure_live_llm(llm_cfg: dict, *, user_id: str | None = None) -> dict:
    """Reconfigure the LIVE LLM service in place so a model/endpoint change
    applies to the next turn — no pipeline rebuild, no audio reconnect. Mirrors
    the runtime TTS-voice swap (_switch_live_voice) and the delegate-roster
    refresh: the codebase hot-swaps session config rather than restarting.

    Works for OpenAI-compatible services (cloud gateways, Ollama). In-process
    MLX can't be retargeted to a remote endpoint in place, so those changes
    still need a restart (``needs_restart``). Never raises — the caller surfaces
    the status as ``llm_applied_live``.
    """
    state = user_state_for(user_id or _A2A_USER_ID)
    svc = state.active_llm
    if svc is None:
        return {"ok": False, "error": "no live LLM session"}
    name = type(svc).__name__
    if "MLX" in name:
        return {
            "ok": False,
            "needs_restart": True,
            "error": "MLX runs in-process; restart to switch models",
        }
    model = str(llm_cfg.get("model") or "").strip()
    url = llm_cfg.get("url")
    api_key = llm_cfg.get("api_key") or ""
    try:
        # url / key — recreate the AsyncOpenAI client (OpenAILLMService.create_client).
        if url and hasattr(svc, "create_client"):
            svc._client = svc.create_client(
                api_key=api_key or "not-needed", base_url=str(url)
            )
        # model — base_llm reads self._settings.model per request.
        settings = getattr(svc, "_settings", None)
        if model and settings is not None and hasattr(settings, "model"):
            settings.model = model
        # two-model routing tiers (router decides / content narrates); collapse
        # to the chosen model unless an explicit split is in the config.
        if hasattr(svc, "_router_model"):
            svc._router_model = str(llm_cfg.get("router_model") or model)
        if hasattr(svc, "_content_model"):
            svc._content_model = str(llm_cfg.get("content_model") or model)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[llm] live reconfigure failed: {e}")
        return {"ok": False, "error": str(e)}
    logger.info(f"[llm] live reconfigure → {name} url={url} model={model}")
    return {"ok": True, "model": model, "url": url}


def _serve_react() -> bool:
    if FRONTEND == "vanilla":
        return False
    if FRONTEND == "react":
        return True
    # auto — use react when the bundle is present.
    return WEB_DIST.exists() and (WEB_DIST / "index.html").exists()


# --- Extracted routers -------------------------------------------------------
# Registered here — after every name they `from app import` is defined, but
# BEFORE the SPA "/{path:path}" catch-all below (earlier routes win, so the API
# must precede it). Routers read app-level mutable/monkeypatched state as
# ``app.<name>`` at call time; see server/routers/*.py. (#app.py-decomposition)
from server.routers import (  # noqa: E402
    comms as _comms_routes,
    config as _config_routes,
    delegates as _delegates_routes,
    llm as _llm_routes,
    orbs as _orbs_routes,
    personas as _personas_routes,
    system as _system_routes,
    voicemodels as _voicemodels_routes,
    voiceprint as _voiceprint_routes,
)

for _r in (
    _system_routes,
    _voiceprint_routes,
    _delegates_routes,
    _personas_routes,
    _llm_routes,
    _comms_routes,
    _voicemodels_routes,
    _orbs_routes,
    _config_routes,
):
    app.include_router(_r.router)

# The Pipecat voice pipeline (run_bot) was extracted to voice/pipeline.py in the
# app.py decomposition (Phase 2). Imported here — after the app-level helpers it
# pulls via `from app import` are defined — and bound as a module global so
# lifespan can launch it (asyncio.create_task(run_bot(...))). (#app.py-decomposition)
from voice.pipeline import run_bot  # noqa: E402, F401


@app.get("/")
async def index():
    # React SPA when web/dist exists; legacy vanilla static fallback
    # otherwise.
    if _serve_react():
        return FileResponse(str(WEB_DIST / "index.html"))
    return FileResponse(str(STATIC_DIR / "index.html"))


# Legacy vanilla shell stays mounted for a deprecation window.
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# React/Tauri SPA assets — /assets/* plus any root-level icons or metadata
# emitted by the Vite build.
if _serve_react():
    app.mount(
        "/assets",
        StaticFiles(directory=str(WEB_DIST / "assets")),
        name="assets",
    )
    # Root-level SPA artifacts. Enumerated from dist/ at startup so new
    # Vite-emitted files don't require a route update.
    for fpath in WEB_DIST.iterdir():
        if not fpath.is_file() or fpath.name == "index.html":
            continue

        async def _serve_fixed(path=str(fpath)):
            return FileResponse(path)

        app.add_api_route(f"/{fpath.name}", _serve_fixed, methods=["GET"])


# Inbound A2A — other agents can send us JSON-RPC `message/send`.
# Delivery + skill attribution on the A2A path currently resolves to the
# A2A_USER_ID user; true per-caller A2A auth lives in a future phase.
register_a2a_routes(
    app,
    text_stream_factory=text_stream_factory,
    version=os.environ.get("AGENT_VERSION", "1.0.0"),
    delivery_provider=lambda: user_state_for(_A2A_USER_ID).active_delivery,
    user_id_provider=lambda: _A2A_USER_ID,
)


# SPA deep-link fallback — any GET that didn't match an earlier route
# returns the react shell so client-side routes resolve correctly after
# a hard reload. Registered LAST; earlier routes win. Skips /api, /.well-known,
# /static, and anything with a file extension (lets 404s propagate cleanly
# for missing assets instead of shadowing them with HTML).
if _serve_react():
    @app.get("/{path:path}")
    async def spa_fallback(path: str):
        if (
            path.startswith("api/")
            or path.startswith(".well-known/")
            or path.startswith("static/")
            or path.startswith("a2a")
            or "." in path.split("/")[-1]
        ):
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(str(WEB_DIST / "index.html"))


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def _bind_listen_socket(host: str, port: int):
    """Pre-bound listen socket for uvicorn (fd handoff — see main()).

    When the requested port is taken (another fleet agent grabbed it), walk
    the rest of the discovery port range so ORBIS stays inside the window
    protoAgent port-scans (agent/discovery.py PORT_RANGE) — being discoverable
    is why a fixed port was requested in the first place. Last resort is an
    ephemeral port: the app must boot even if the whole range is occupied
    (mDNS discovery still works there; only the port-scan channels go blind).
    Requests outside the fleet range fall straight to ephemeral.
    """
    import socket

    from agent.discovery import PORT_RANGE

    def _try(p: int):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, p))
        except OSError:
            s.close()
            return None
        s.listen(128)
        return s

    sock = _try(port)
    if sock is None and PORT_RANGE[0] <= port <= PORT_RANGE[1]:
        for p in range(PORT_RANGE[0], PORT_RANGE[1] + 1):
            if p == port:
                continue
            sock = _try(p)
            if sock is not None:
                logger.warning(
                    f"[boot] port {port} taken — fell back to {p} "
                    f"(still inside the discovery range)"
                )
                break
    if sock is None:
        sock = _try(0)
        if port:
            logger.warning(
                f"[boot] port {port} (and the discovery range) taken — "
                f"using an ephemeral port; port-scan discovery of this "
                f"instance won't work, mDNS still will"
            )
    if sock is None:  # ephemeral bind failed too — host is unusable
        raise OSError(f"could not bind any port on {host}")
    return sock


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=PORT,
                        help="TCP port; 0 = ephemeral (Tauri sidecar default)")
    parser.add_argument("--host", default=os.environ.get("ORBIS_HOST", "0.0.0.0"),
                        help="Bind host. Docker: 0.0.0.0 (default). Desktop "
                             "bundles: 127.0.0.1 (loopback only).")
    args = parser.parse_args()

    logger.info(f"[boot] cache dir: {_cache_dir}")

    # Hard-fail on unsupported hardware — see agent/hardware.py. Docker
    # CPU profile sets ORBIS_ALLOW_CPU=1 to opt back in; desktop bundles
    # leave it unset so the shell can surface a friendly "you need a GPU"
    # dialog when the sidecar exits non-zero.
    from agent.hardware import detect_device, HardwareError
    try:
        device = detect_device()
        logger.info(f"[boot] accelerator: {device}")
    except HardwareError as e:
        print(f"\n[orbis] {e}\n", file=sys.stderr, flush=True)
        sys.exit(2)

    def _shutdown(_sig, _frame):
        logger.info("Shutting down")
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    # Reap the whole worker tree cleanly on quit (#485). Become our own
    # process-group leader so the Tauri shell can signal the entire group
    # (kill -TERM/-KILL -<pgid>) instead of SIGKILLing just the direct child
    # and orphaning the grandchildren that hold port 7866 / the GPU. Only
    # advertise ORBIS_PGID when setsid actually succeeded — the shell then
    # reaps a group we own, else falls back to the direct-child kill. A
    # parent-death watchdog covers the case where the shell panics and never
    # runs its exit handler at all.
    from agent.process_guard import establish_own_process_group, start_parent_death_watchdog
    _pgid = establish_own_process_group()
    if _pgid is not None:
        print(f"ORBIS_PGID {_pgid}", flush=True)
        start_parent_death_watchdog(_pgid)

    # Port 0 → OS assigns. Pre-bind a socket so we can print the real
    # port BEFORE uvicorn starts (the Tauri shell reads stdout for the
    # readiness line). uvicorn's Config accepts a pre-bound fd, which
    # closes the race window between knowing the port and listening on it.
    import uvicorn

    sock = _bind_listen_socket(args.host, args.port)
    bound_host, bound_port = sock.getsockname()[:2]

    # Hand the real bind address to lifespan (mDNS advertise) + the
    # delegates discovery endpoint (self-exclusion) — they run after
    # uvicorn starts and can't see the pre-bound socket.
    os.environ["ORBIS_BOUND_HOST"] = str(bound_host)
    os.environ["ORBIS_BOUND_PORT"] = str(bound_port)

    # Canonical readiness line — Tauri + other supervisors grep for this
    # prefix to learn where to connect. Keep it first on stdout; any
    # logger output is on stderr. The reader is always on THIS machine, so
    # an all-interfaces bind (0.0.0.0, the discoverable mode) is reported
    # as loopback — WKWebView can't reliably navigate to 0.0.0.0.
    ready_host = "127.0.0.1" if bound_host == "0.0.0.0" else bound_host
    print(f"ORBIS_READY http://{ready_host}:{bound_port}", flush=True)

    # Reclaim stale pyapp sidecar envs left by previous versions (#489). Each is
    # ~1.8 GB and every in-app update lands a new one while the old lingers
    # forever. Runs in a daemon thread so the multi-second rmtree never delays
    # serving; keeps only the env we're executing from and no-ops in dev
    # (.venv, not under the pyapp base). Opt out with ORBIS_ENV_GC=0.
    if os.environ.get("ORBIS_ENV_GC", "1") != "0":
        import threading as _threading

        def _gc_envs():
            try:
                from agent.env_gc import gc_stale_envs
                gc_stale_envs()
            except Exception as e:  # cleanup must never take down a boot
                logger.warning(f"[env-gc] skipped: {e}")

        _threading.Thread(target=_gc_envs, name="orbis-env-gc", daemon=True).start()

    config = uvicorn.Config(app, fd=sock.fileno())
    server = uvicorn.Server(config)
    server.run()


if __name__ == "__main__":
    main()
