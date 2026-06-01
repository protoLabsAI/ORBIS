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
import random
import signal
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

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
    from pathlib import Path as _RTPath
    _rt_env = _RTPath.home() / "Library/Application Support/studio.protolabs.orbis/.env"
    if _rt_env.is_file():
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

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.llm_switcher import LLMSwitcher
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.service_switcher import ServiceSwitcherStrategyFailover
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMAssistantAggregatorParams,
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
    UserTurnCompletionConfig,
)
from pipecat.processors.frameworks.rtvi import (
    RTVIObserver,
    RTVIObserverParams,
    RTVIProcessor,
)
from pipecat.utils.context.llm_context_summarization import (
    LLMAutoContextSummarizationConfig,
    LLMContextSummaryConfig,
)
from pipecat.services.openai.llm import OpenAILLMService

from voice.llm import make_llm
from voice.local_transport import LocalAudioTransport, audio_runtime_info
from voice.native_bargein import NativeBargeInObserver
from voice.sse_bus import sse_bus
from pipecat.frames.frames import TTSSpeakFrame

from a2a.server import register_a2a_routes
from agent.backchannel import BackchannelController
from agent.bargein import BargeInGate
from agent.delegates import DelegateRegistry
from agent.micro_ack import MicroAckInjector, opening_ack_line
from agent.stall_watchdog import StallWatchdog
from agent.echo_guard import (
    ECHO_GUARD_MS,
    HALF_DUPLEX,
    EchoGuardObserver,
    EchoGuardState,
    EchoGuardSuppressor,
)
from agent.audio_tags import make_audio_tags_tap
from agent.delivery import DeliveryController, Priority
from agent.paths import get_voiceprint_path
from agent.speaker_gate import (
    SpeakerGate,
    StrangerAction,
    VoiceprintCorrupted,
    load_voiceprint,
    save_voiceprint,
)
from agent.prosody import ProsodyTagStripper
from agent.filler import (
    FillerGenerator,
    Latency,
    Verbosity,
    audio_context_block,
    plan_block,
    repair_block,
    tool_response_block,
    tool_use_block,
)
from agent import tracing as _tracing
from agent.session_store import (
    drain_stashed_deliveries,
    load_last_summary,
    save_summary,
    stash_delivery,
)
from agent.orchestrate import run_orchestration
from agent.tools import (
    ASYNC_TOOL_NAMES,
    build_text_tool_schemas,
    capabilities_block,
    latency_for,
    register_tools,
    run_text_tool,
)
from auth import load_users, require_user, user_registry
from auth.users import User
from auth.context import current_session_id, current_user_id
from agent.user_state import active_user_states, user_state_for
from voice.stt import STT_BACKEND, make_stt, prewarm as prewarm_stt
from voice.tts import TTS_BACKEND, make_tts, prewarm as prewarm_tts

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
from agent.persona import get_active_persona, reload_persona  # noqa: E402

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


def _resolve_skill_llm(skill) -> dict:
    """Resolve LLM routing for a skill. Single source of truth shared by
    the voice path (run_bot) and the inbound A2A path (text_agent).

    Precedence per-field: persona.llm.{url,model,api_key,api_key_env}
    overrides; env var fallback; finally module-level defaults.

    `extra_body` follows the same kill-switch logic as the voice path:
    user override always wins; custom URL forces None (avoids LiteLLM
    400s on `chat_template_kwargs`); default endpoint sends
    `enable_thinking=False`.

    Returns a dict with keys: url, model, api_key, extra_body,
    using_custom_url, provider. Callers compose request kwargs from
    this. ``provider`` rides through to ``make_llm()`` so the adapter
    factory can route Ollama-native vs OpenAI-compat correctly.
    """
    skill_llm = (skill.llm if skill else None) or {}
    using_custom_url = bool(skill_llm.get("url"))
    url = str(skill_llm.get("url") or LLM_URL)
    model = str(skill_llm.get("model") or LLM_SERVED_NAME)
    if skill_llm.get("api_key"):
        api_key = str(skill_llm["api_key"])
    elif skill_llm.get("api_key_env"):
        api_key = os.environ.get(str(skill_llm["api_key_env"]), LLM_API_KEY)
    else:
        api_key = LLM_API_KEY
    if "extra_body" in skill_llm:
        extra_body = skill_llm["extra_body"] or None
    elif using_custom_url:
        extra_body = None
    else:
        extra_body = {"chat_template_kwargs": {"enable_thinking": False}}
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
        "using_custom_url": using_custom_url,
        "provider": skill_llm.get("provider"),
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
    using_custom_url = True  # a fallback is always an explicit, user-chosen URL
    model = str(fb.get("model") or LLM_SERVED_NAME)
    if fb.get("api_key"):
        api_key = str(fb["api_key"])
    elif fb.get("api_key_env"):
        api_key = os.environ.get(str(fb["api_key_env"]), LLM_API_KEY)
    else:
        api_key = LLM_API_KEY
    # Mirror the custom-URL extra_body kill-switch: a user-supplied
    # fallback endpoint may be a LiteLLM/vLLM that 400s on
    # chat_template_kwargs, so default it off unless explicitly set.
    extra_body = fb["extra_body"] or None if "extra_body" in fb else None
    return {
        "url": str(url),
        "model": model,
        "api_key": api_key,
        "extra_body": extra_body,
        "using_custom_url": using_custom_url,
        "provider": fb.get("provider"),
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
    """
    parts: list[str] = []

    # Prior-N block from SQLite. Newest first, ~3 sessions keeps the
    # prompt affordable while still giving cross-session continuity.
    try:
        mem = get_memory()
        prior = mem.sessions.prior_n(3)
    except Exception as e:
        logger.warning(f"[memory] prior_n read failed: {e}")
        prior = []

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
        parts.append("\n".join(sessions_xml))

    # Fallback / complement: the text summary file.
    summary = load_last_summary(user_id)
    if summary:
        parts.append(
            "## MEMORY — rolling summary\n\n"
            f"{summary}"
        )

    if not parts:
        return ""

    return (
        "\n\n".join(parts)
        + "\n\nIF any of this fits naturally, acknowledge it in your first "
        "turn. Otherwise IGNORE this block — do not force a callback."
    )


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
        + (("\n\n" + plan) if plan else "")
        + "\n\n"
        + repair_block()
        # Audio-context block teaches the LLM what the [audio] line
        # AudioTagsTap will inject means. Static — no runtime data, no
        # branch — so it sits in every persona's prompt regardless of
        # whether STT_BACKEND=sensevoice is enabled. When the tap isn't
        # running the [audio] line just won't appear; the block tells
        # the LLM to ignore the annotation when missing, so this is
        # safe-by-default.
        + "\n\n"
        + audio_context_block()
        + (("\n\n" + user_block) if user_block else "")
        + (("\n\n" + personality) if personality else "")
        + (("\n\n## RETURN\n\n" + neglect_nudge) if neglect_nudge else "")
        + (("\n\n" + inbox_block) if inbox_block else "")
        + (("\n\n" + recall) if recall else "")
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


async def text_agent(message: str, session_id: str) -> str:
    """Text turn with a bounded ReAct loop — used by the A2A inbound
    handler (both message/send and message/stream).

    The text agent sees the same tool registry the voice side does
    (calculator, datetime, web_search, delegate_to), minus async tools
    like slow_research that need a live voice session to narrate back.
    Loop is capped at TEXT_AGENT_MAX_ITER iterations (default 3) to
    prevent runaway — on exhaustion we return whatever text the model
    last produced (may be empty).
    """
    import json as _json

    _METRICS["a2a_inbound_total"] += 1
    user_id = _A2A_USER_ID
    current_user_id.set(user_id)
    state = user_state_for(user_id)
    skill = _active_skill(user_id)
    history = _A2A_CONTEXTS.setdefault(session_id, [])
    history.append({"role": "user", "content": message})

    # Respect per-skill delegate filter for inbound A2A too.
    session_delegates = _DELEGATES.filtered(skill.delegates if skill else None)

    # System prompt shared with the voice path — blocks for TOOL USE,
    # response shape, plan, repair all apply equally to a text reply.
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
    # Resolve persona.llm overrides — voice path and A2A path now share
    # the same routing logic via _resolve_skill_llm. Closes R14: a user
    # who configures a custom LLM in config/orbis.yaml gets that LLM for
    # both voice turns AND inbound A2A turns.
    llm_cfg = _resolve_skill_llm(skill)
    client = _get_text_client(llm_cfg["url"], llm_cfg["api_key"])

    reply = ""
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
            result = await run_text_tool(
                tc.function.name,
                args,
                delegates=session_delegates,
            )
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })
    else:
        logger.warning(
            f"[a2a/react] hit max iterations ({_TEXT_REACT_MAX_ITERATIONS}) — "
            "returning last partial"
        )

    history.append({"role": "assistant", "content": reply})
    if len(history) > _A2A_MAX_TURNS * 2:
        del history[: len(history) - _A2A_MAX_TURNS * 2]
    return reply

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
            UserStartedSpeakingMessage,
            UserTranscriptionMessage,
        )
        if isinstance(model, BotStartedSpeakingMessage):
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
        elif isinstance(model, UserStartedSpeakingMessage):
            await sse_bus.publish("bot-state", {"state": "listening"})
        elif isinstance(model, BotLLMStartedMessage):
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


async def run_bot(user_id: str = "default", *, transport: LocalAudioTransport | None = None) -> None:
    """Run the persistent native voice pipeline.

    Called once from lifespan with a pre-built LocalAudioTransport that
    bridges to the Rust native audio engine over a Unix socket. The pipeline
    runs for the lifetime of the app — `cancel_on_idle_timeout=False`
    on the PipelineTask keeps it alive across UI idle periods.
    """
    # Set context vars so deep-stack code (tracing spans, session_store
    # lookups, filler generators) can pick up the right user/session
    # without needing the id threaded through.
    current_user_id.set(user_id)
    user_state = user_state_for(user_id)

    # Snapshot the active skill at connect time; the session keeps it even
    # if the operator flips the dropdown mid-call. Matches UX expectation.
    skill = _active_skill(user_id)
    tts_backend = skill.tts_backend or TTS_BACKEND

    # Skills may override per-user filler verbosity.
    if skill.filler_verbosity:
        try:
            user_state.filler_settings.verbosity = Verbosity(skill.filler_verbosity)
        except ValueError:
            pass

    logger.info(
        f"[session] user={user_id!r} skill={skill.slug!r} tts_backend={tts_backend} "
        f"voice={skill.voice!r} verbosity={user_state.filler_settings.verbosity.value}"
    )

    # Caller (lifespan) builds the transport. Direct invocations (tests
    # or alternate entry points) can also pass a pre-built one.
    if transport is None:
        sock_path = os.environ.get("ORBIS_AUDIO_SOCK", "")
        if not sock_path:
            raise RuntimeError(
                "run_bot() requires either a transport= kwarg or "
                "ORBIS_AUDIO_SOCK env var pointing at the Rust native "
                "audio engine's unix socket."
            )
        transport = LocalAudioTransport(sock_path=sock_path)

    # The Rust-side AEC (src-tauri/src/audio/aec.rs) is currently a thin
    # delay-line subtractor — not strong enough on its own once we apply
    # the software mic gain in voice/local_transport.py. Keep the Python
    # echo guard active with a longer window so amplified speaker bleed
    # doesn't false-trigger VAD/MicroAck on the bot's own tail. Phase 2
    # (AVAudioEngine voice-processing IO) supersedes this entirely.
    # Override via NATIVE_ECHO_GUARD_MS env var.
    _ECHO_STATE.guard_ms = int(os.environ.get("NATIVE_ECHO_GUARD_MS", "800"))

    stt = make_stt(**(skill.stt or {}))

    # LLM routing — resolved by _resolve_skill_llm so the voice path and
    # the A2A inbound text path share a single source of truth. Per-field
    # precedence: persona.llm.{url,model,api_key,api_key_env,extra_body}
    # → env vars (LLM_URL / LLM_SERVED_NAME / LLM_API_KEY) → defaults.
    # The extra_body kill-switch protects custom URLs from LiteLLM 400s
    # on chat_template_kwargs; see _resolve_skill_llm for the full
    # rationale.
    llm_cfg = _resolve_skill_llm(skill)
    llm_url = llm_cfg["url"]
    llm_model = llm_cfg["model"]
    llm_api_key = llm_cfg["api_key"]
    extra_body = llm_cfg["extra_body"]
    using_custom_llm = llm_cfg["using_custom_url"]

    settings_kwargs: dict = {
        "model": llm_model,
        "temperature": skill.temperature if skill else LLM_TEMPERATURE,
        "max_tokens": skill.max_tokens if skill else LLM_MAX_TOKENS,
    }
    if extra_body is not None:
        settings_kwargs["extra"] = {"extra_body": extra_body}

    # voice/llm/__init__.py picks the right adapter — Ollama instances
    # get the native /api/chat path (which honors `think: false`),
    # everything else routes through pipecat's OpenAI-compat service.
    # The factory also handles the supports_developer_role swap for
    # the project's default endpoint.
    llm = make_llm(
        base_url=llm_url,
        model=llm_model,
        api_key=llm_api_key,
        settings=OpenAILLMService.Settings(**settings_kwargs),
        provider=llm_cfg["provider"],
        using_custom_url=using_custom_llm,
        router_model=llm_cfg["router_model"],
        content_model=llm_cfg["content_model"],
    )

    # Optional failover backup (orbis-1dd). When a fallback LLM is
    # configured (persona.llm.fallback or LLM_FALLBACK_URL), wrap primary
    # + backup in a pipecat LLMSwitcher with the failover strategy: a
    # non-fatal ErrorFrame from the active LLM (e.g. the cloud gateway is
    # down) automatically switches to the next member for the rest of the
    # session. When unconfigured, `_llm_members == [llm]` and the pipeline
    # uses the bare `llm` — byte-for-byte the single-LLM path as before.
    _fallback_cfg = _resolve_fallback_llm(skill)
    _llm_members = [llm]
    if _fallback_cfg is not None:
        _fb_settings = dict(settings_kwargs)
        _fb_settings["model"] = _fallback_cfg["model"]
        if _fallback_cfg["extra_body"] is not None:
            _fb_settings["extra"] = {"extra_body": _fallback_cfg["extra_body"]}
        else:
            _fb_settings.pop("extra", None)
        llm_fallback = make_llm(
            base_url=_fallback_cfg["url"],
            model=_fallback_cfg["model"],
            api_key=_fallback_cfg["api_key"],
            settings=OpenAILLMService.Settings(**_fb_settings),
            provider=_fallback_cfg["provider"],
            using_custom_url=_fallback_cfg["using_custom_url"],
        )
        _llm_members.append(llm_fallback)
        logger.info(
            "[llm] failover enabled: primary=%s (%s) → backup=%s (%s)",
            llm_url, llm_model, _fallback_cfg["url"], _fallback_cfg["model"],
        )

    # Per-skill delegate filter. Empty list / None = all delegates exposed.
    session_delegates = _DELEGATES.filtered(skill.delegates if skill else None)
    tts_kwargs: dict = {"backend": tts_backend}
    if skill.voice:
        if tts_backend == "kokoro":
            tts_kwargs["voice"] = skill.voice
            # Persona has no lang field today — KOKORO_LANG env handles
            # language overrides. getattr keeps this branch forward-
            # compatible if a lang field is added later.
            lang = getattr(skill, "lang", None)
            if lang:
                tts_kwargs["lang"] = lang
        elif tts_backend == "fish":
            tts_kwargs["reference_id"] = skill.voice
        elif tts_backend == "openai":
            tts_kwargs["voice"] = skill.voice
    if tts_backend == "openai":
        if skill.tts_url:
            tts_kwargs["url"] = skill.tts_url
        if skill.tts_model:
            tts_kwargs["model"] = skill.tts_model
        if skill.tts_api_key:
            tts_kwargs["api_key"] = skill.tts_api_key
    tts = make_tts(**tts_kwargs)

    # Delivery controller — observes VAD + transcripts, drains push deliveries.
    delivery = DeliveryController()

    # Per-skill behavior overrides. Each key can be:
    #   false                — disable the controller for this skill
    #   true (or omitted)    — enabled with env/module defaults
    #   dict                 — enabled, with specific timing overrides
    behavior = skill.behavior or {}
    bc_cfg = _resolve_behavior_block(behavior.get("backchannel"))
    ma_cfg = _resolve_behavior_block(behavior.get("micro_ack"))
    bg_cfg = _resolve_behavior_block(behavior.get("bargein"))
    sg_cfg = _resolve_behavior_block(behavior.get("speaker_gate"))
    # Backchannel + micro-ack were originally tuned against the WebRTC
    # mic path's AGC + browser echo cancellation. On the native CPAL
    # path the speaker-bleed-into-mic crosses VAD threshold (especially
    # with software mic gain), so the listener-acks fire on the bot's
    # own tail. Default both off unless the persona explicitly enabled
    # them. Phase 2 (real AEC via AVAudioEngine) lets us flip the
    # default back to on.
    if behavior.get("backchannel") is None:
        bc_cfg["enabled"] = False
    if behavior.get("micro_ack") is None:
        ma_cfg["enabled"] = False

    # Backchannel controller — emits brief listener-acks ("mm-hmm") during
    # long user utterances. Uses the per-user FillerGenerator.
    bc_kwargs: dict = {
        "generator": _filler_gen_for(user_id),
        "tts_backend": tts_backend,
        "enabled": bc_cfg["enabled"],
    }
    if "first_ms" in bc_cfg:
        bc_kwargs["first_after_secs"] = bc_cfg["first_ms"] / 1000.0
    if "interval_ms" in bc_cfg:
        bc_kwargs["interval_secs"] = bc_cfg["interval_ms"] / 1000.0
    backchannel = BackchannelController(**bc_kwargs)

    # `_cancel_progress` is defined below; register_tools captures it via
    # closure so each SYNC tool handler auto-stops the progress loop on return.
    def _cancel_progress(*, publish_end: bool = True):
        while progress_tasks:
            t = progress_tasks.pop()
            t.cancel()
        if publish_end:
            asyncio.create_task(
                sse_bus.publish("tool-call", {"event": "end", "outcome": "success"})
            )

    # D17: attach pushNotificationConfig on outbound A2A so remote agents
    # can call us back via /a2a/push even if the SSE stream dropped.
    # Env-driven — if A2A_PUSH_URL isn't set (typical local dev), the
    # config is omitted and outbound A2A is stream-only.
    _push_url = os.environ.get("A2A_PUSH_URL") or None
    _push_token = os.environ.get("A2A_PUSH_TOKEN") or None
    # Register handlers on every failover member — whichever LLM is
    # active must be able to invoke the tools. The returned schema is
    # identical per member (derived from the same registry), so we keep
    # the primary's for the LLMContext below.
    # D1 orchestrate runner — the bounded multi-step delegation loop, closed
    # over the session's text LLM client. Built once and shared across failover
    # members. Only when delegates + a DeliveryController exist (the synthesis
    # has to be speakable). See agent/orchestrate.py.
    _orch_runner = None
    if session_delegates and session_delegates.names() and delivery is not None:
        _orch_client = _get_text_client(llm_cfg["url"], llm_cfg["api_key"])

        async def _orch_runner(goal: str, *, progress=None) -> str:
            return await run_orchestration(
                goal,
                delegates=session_delegates,
                client=_orch_client,
                model=llm_cfg["model"],
                extra_body=llm_cfg["extra_body"],
                max_tokens=skill.max_tokens,
                temperature=skill.temperature,
                progress=progress,
            )

    tools_schema = None
    for _member in _llm_members:
        _schema = register_tools(
            _member,
            on_finish=_cancel_progress,
            delivery=delivery,
            delegates=session_delegates,
            push_notification_url=_push_url,
            push_notification_token=_push_token,
            orchestrate_runner=_orch_runner,
        )
        if tools_schema is None:
            tools_schema = _schema

    # Per-skill tool restriction. If skill.tools is non-empty, scope the
    # ToolsSchema down to that allow-list — the LLM only SEES (and so
    # only calls) the listed names. Handlers stay registered on the LLM
    # service either way; if the schema doesn't expose them, they can't
    # be reached.
    if skill.tools:
        from pipecat.adapters.schemas.tools_schema import ToolsSchema
        allowed = set(skill.tools)
        kept = [s for s in tools_schema.standard_tools if s.name in allowed]
        unknown = allowed - {s.name for s in tools_schema.standard_tools}
        if unknown:
            logger.warning(
                f"[skill] {skill.slug!r}: tools={list(unknown)} not in registry; "
                "ignored"
            )
        if kept:
            logger.info(
                f"[skill] {skill.slug!r} restricted to tools: "
                f"{[s.name for s in kept]}"
            )
            tools_schema = ToolsSchema(standard_tools=kept)
        else:
            logger.warning(
                f"[skill] {skill.slug!r}: tools list matched zero registered tools; "
                "exposing all (refuse to leave the agent toolless)"
            )

    context = LLMContext(
        [{
            "role": "system",
            "content": _effective_prompt(
                skill,
                tts_backend,
                verbosity=user_state.filler_settings.verbosity,
                user_id=user_id,
                delegates=session_delegates,
                tools_schema=tools_schema,
            ),
        }],
        tools=tools_schema,
    )

    _turn_strategies = _build_user_turn_strategies()
    # Env-tunable so turn-end latency can be A/B'd without a rebuild (via
    # the runtime .env). stop_secs is the dominant fixed per-turn delay;
    # Smart Turn (SMART_TURN=local) lets it drop without clipping pauses.
    _vad_stop = float(os.environ.get("VAD_STOP_SECS", "0.4"))
    logger.info(
        "[tuning] vad stop_secs=%s start_secs=%s min_volume=%s | echo_guard=%sms | smart_turn=%s",
        _vad_stop, os.environ.get("VAD_START_SECS", "0.2"),
        os.environ.get("VAD_MIN_VOLUME", "0.2"),
        os.environ.get("NATIVE_ECHO_GUARD_MS", "800"), SMART_TURN,
    )
    _user_agg_kwargs: dict = {"vad_analyzer": SileroVADAnalyzer(
        params=VADParams(
            confidence=float(os.environ.get("VAD_CONFIDENCE", "0.7")),
            start_secs=float(os.environ.get("VAD_START_SECS", "0.2")),
            stop_secs=_vad_stop,  # default 0.4 — longer pause before cutting
            min_volume=float(os.environ.get("VAD_MIN_VOLUME", "0.2")),  # native desktop audio
                               # The legacy CPAL input path applies MIC_GAIN in
                               # voice/local_transport.py; the macOS voice-processing
                               # path defaults to unity gain because Apple AGC is
                               # already active. STT_MIN_RMS (in voice/stt.py) is
                               # the second gate against Whisper silence-hallucinations.
        )
    )}
    if _turn_strategies is not None:
        # Only pass user_turn_strategies when we actually built one — passing
        # None keeps the default (naive VAD endpointing).
        _user_agg_kwargs["user_turn_strategies"] = _turn_strategies

    # Incomplete-turn filtering (pipecat-native — fixes orbis-ioz queued-intent
    # staggering): the LLM emits a turn-completion marker (✓/○/◐) and the
    # aggregator SUPPRESSES the response to an incomplete fragment ("hey, so I
    # uh…") instead of firing a premature reply, re-prompting on timeout. Off by
    # default (changes LLM behavior + relies on the model emitting the marker);
    # flip FILTER_INCOMPLETE_TURNS=1 in the runtime .env to A/B it. The
    # user-turn coalescing window is tunable via USER_TURN_STOP_TIMEOUT.
    if os.environ.get("FILTER_INCOMPLETE_TURNS", "0") == "1":
        _user_agg_kwargs["filter_incomplete_user_turns"] = True
        # orbis-3ss: pipecat's UserTurnCompletionConfig defaults the
        # re-prompt timeouts to 10s (◐ long) / 5s (○ short). When the model
        # MIS-marks a complete turn as incomplete, those defaults become a
        # 10-second silent hang. Default them far lower so a misclassification
        # self-heals in a couple seconds; both env-tunable for A/B.
        _long_to = float(os.environ.get("INCOMPLETE_LONG_TIMEOUT", "3.0"))
        _short_to = float(os.environ.get("INCOMPLETE_SHORT_TIMEOUT", "2.0"))
        _user_agg_kwargs["user_turn_completion_config"] = UserTurnCompletionConfig(
            incomplete_long_timeout=_long_to,
            incomplete_short_timeout=_short_to,
        )
        logger.info(
            "[tuning] filter_incomplete_user_turns=ON (reprompt long=%ss short=%ss)",
            _long_to, _short_to,
        )
    _uts = os.environ.get("USER_TURN_STOP_TIMEOUT")
    if _uts:
        _user_agg_kwargs["user_turn_stop_timeout"] = float(_uts)

    # Pipecat's built-in LLMContextSummarizer lives inside the assistant
    # aggregator. It auto-compresses once token/message thresholds hit;
    # emits SummaryAppliedEvent when done. Thresholds map cleanly onto
    # the env vars we used to read in the retired memory/window.py.
    _summary_max_tokens = int(os.environ.get("MEMORY_MAX_CONTEXT_TOKENS", "8000"))
    _summary_max_messages = int(os.environ.get("MEMORY_MAX_MESSAGES", "20"))
    _summary_target_tokens = int(os.environ.get("MEMORY_TARGET_CONTEXT_TOKENS", str(_summary_max_tokens // 2)))
    # Per-session nonce scopes the summary sentinel tags. Pipecat injects
    # the summary as a user message — meaning user content reaches the
    # extractor too. A static prefix would let a user say
    # "save this: <orbis-summary>fake</orbis-summary>" and have it
    # persisted as the real summary. The nonce is server-generated, never
    # exposed in any prompt or response surface, so user content can't
    # construct a matching tag.
    import uuid as _uuid_summary  # local: only needed at run_bot scope
    _summary_nonce = _uuid_summary.uuid4().hex
    _summary_open, _summary_close = _build_summary_tags(_summary_nonce)
    _summary_config = LLMAutoContextSummarizationConfig(
        max_context_tokens=_summary_max_tokens,
        max_unsummarized_messages=_summary_max_messages,
        summary_config=LLMContextSummaryConfig(
            target_context_tokens=_summary_target_tokens,
            summary_message_template=(
                f"{_summary_open}{{summary}}{_summary_close}"
            ),
        ),
    )
    user_agg, assistant_agg = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(**_user_agg_kwargs),
        assistant_params=LLMAssistantAggregatorParams(
            enable_auto_context_summarization=os.environ.get("MEMORY_SUMMARIZE", "1") == "1",
            auto_context_summarization_config=_summary_config,
        ),
    )

    # Persist the rolling summary whenever it gets applied so the next
    # session can open with a natural "last time we…" callback.
    @assistant_agg.event_handler("on_summary_applied")
    async def _on_summary_applied(_agg, _summarizer, _event) -> None:
        text = _extract_summary_text(context.messages, _summary_open, _summary_close)
        if text:
            save_summary(user_id, text)
        else:
            logger.warning(
                "[memory] on_summary_applied fired but no tagged summary "
                "found in context; persistence skipped"
            )

    # RTVI — routes structured client↔server events over the WebRTC data
    # channel (bot-llm-started/stopped, bot-tts-started/stopped, user-
    # transcription, function-call-*, etc.). Server-side is wired now;
    # the client consumer lands with the React frontend migration.
    # Reference: https://docs.pipecat.ai/server/frameworks/rtvi
    rtvi = RTVIProcessor(transport=transport)

    speaker_gate = _build_speaker_gate(sg_cfg)

    # AudioTagsTap (#66 Phase 4) — consumes EmotionFrame /
    # AudioEventFrame from STT, writes per-turn mood deltas (owner only),
    # injects [audio] system message before each TranscriptionFrame.
    # No-op when STT_BACKEND=local|openai (no EmotionFrame source);
    # safe to leave wired regardless of backend. Disable via
    # AUDIO_TAGS=off env.
    audio_tags = make_audio_tags_tap(mem=get_memory())

    # Single transport, single output node — no fanout needed now that
    # the WebRTC client path is removed (DECISIONS.md amendment 2026-04-28).
    _output_node = transport.output()

    # Failover wrapper (orbis-1dd). With a single member this stays the
    # bare LLMService — no LLMSwitcher overhead on the default path. With
    # a configured backup, the switcher routes frames to the active LLM
    # and fails over to the next on a non-fatal ErrorFrame.
    if len(_llm_members) == 1:
        pipeline_llm = llm
    else:
        pipeline_llm = LLMSwitcher(
            llms=_llm_members,
            strategy_type=ServiceSwitcherStrategyFailover,
        )

        @pipeline_llm.strategy.event_handler("on_service_switched")
        async def _on_llm_failover(_strategy, service):
            # Surface failover so a dropped primary is visible, not silent.
            idx = _llm_members.index(service) if service in _llm_members else -1
            role = "primary" if idx == 0 else f"backup#{idx}"
            logger.warning("[llm] failover → now using %s (%s)", role, service.name)
            _METRICS["llm_failovers_total"] = _METRICS.get("llm_failovers_total", 0) + 1
            await sse_bus.publish("llm", {"event": "failover", "active": role})

    pipeline = Pipeline([
        transport.input(),
        # Echo-guard sits IMMEDIATELY after transport.input — drops mic
        # audio while the bot is speaking (HALF_DUPLEX) and for ECHO_GUARD_MS
        # after it stops. VAD downstream never sees the suppressed audio.
        EchoGuardSuppressor(_ECHO_STATE),
        # Speaker-verification gate (#35 PR 1.2) — observes echo-guarded
        # audio between UserStartedSpeakingFrame and UserStoppedSpeakingFrame,
        # cosine-compares the per-utterance embedding to the cached
        # voiceprint, emits OwnerVerifiedFrame / StrangerDetectedFrame
        # alongside the audio (originals always pass through). Disabled
        # by default (no voiceprint → owner-trust); enable by enrolling
        # via the wizard (PR 1.3) and setting persona.behavior.speaker_gate
        # in config/orbis.yaml.
        speaker_gate,
        # RTVI processor near the top — forwards inbound client messages
        # (config, custom actions) into the pipeline and exposes the
        # push-channel for the observer.
        rtvi,
        stt,
        # AudioTagsTap reads EmotionFrame / AudioEventFrame /
        # TranscriptionFrame from STT, writes per-turn mood deltas
        # (owner only — gated on speaker_verified from SpeakerGate),
        # and injects an [audio] system message before each user
        # transcription so the LLM sees affect context BEFORE the
        # words. audio_context_block() in the persona prompt teaches
        # the LLM what to do with the [audio] line and forbids
        # parroting it.
        audio_tags,
        user_agg,
        # Adaptive barge-in gate — suppresses VAD-triggered interrupts
        # that resolve within the grace window as coughs / backchannels /
        # background noise. Real interrupts still fire, just confirmed.
        BargeInGate(
            enabled=bg_cfg["enabled"],
            **({"grace_ms": int(bg_cfg["grace_ms"])} if "grace_ms" in bg_cfg else {}),
        ),
        # Micro-ack injector — if the main pipeline hasn't produced audio
        # within ~2500 ms (default; per-persona override via
        # behavior.micro_ack.first_ms) of UserStoppedSpeaking, emit a
        # quiet "mm" / "hm" so the agent feels responsive on slow turns.
        # Cancels when the bot actually starts speaking. Vapi Fill
        # Injection pattern.
        MicroAckInjector(
            tts_backend=tts_backend,
            enabled=ma_cfg["enabled"],
            # Live verbosity getter — a /api/verbosity flip to SILENT
            # silences the acoustic ack on the very next turn without
            # a session reconnect. Closure captures user_state, which
            # is the same object the API mutates.
            verbosity_getter=lambda: user_state.filler_settings.verbosity,
            # Occasional LLM-generated acks for variety (orbis-29e) — same
            # micro generator the fillers + announcer use.
            generator=_filler_gen_for(user_id),
            **({"trigger_ms": int(ma_cfg["first_ms"])} if "first_ms" in ma_cfg else {}),
        ),
        # Stall watchdog (E2) — if the agent produces no sign of work within
        # STALL_SECS of the user's turn ending (frozen LLM/TTS), speak one
        # canned recovery line so the user isn't left in dead air. Coarse +
        # once-per-turn; the micro-ack covers the normal sub-3s gap.
        # Both placed after the gate — they need TranscriptionFrames and
        # VAD frames produced by the aggregator. Push downstream into TTS.
        backchannel,
        delivery,
        pipeline_llm,
        # Stall watchdog (E2) sits AFTER the LLM: it arms on UserStopped
        # (which flows downstream to here) and cancels the moment the LLM
        # emits text / a tool call (LLMTextFrame / FunctionCallsStartedFrame
        # flow downstream from pipeline_llm to here). Placed BEFORE the LLM it
        # never saw those cancel frames and fired on every turn. Pushes its
        # recovery line downstream into TTS.
        StallWatchdog(
            stall_secs=float(os.environ.get("STALL_SECS", "8")),
            enabled=os.environ.get("STALL_WATCHDOG", "1") == "1",
            tts_backend=tts_backend,
        ),
        # Non-Fish TTS services strip tags at the service level via their
        # text_filters= kwarg (see voice/tts/{kokoro,openai}.py). Fish
        # consumes `[softly]` / `[pause:300]` natively, so its adapter
        # doesn't filter.
        tts,
        # Single transport.output() — sends TTS PCM frames over the
        # unix socket to the Rust CPAL playback ring.
        _output_node,
        # Strip Fish-style prosody tags from TextFrames before the
        # assistant aggregator sees them, so tags don't accumulate in LLM
        # context for future turns. Applies regardless of backend — safety
        # net for whatever the LLM emitted.
        ProsodyTagStripper(),
        # Context summarization is wired INTO assistant_agg itself via
        # LLMAssistantAggregatorParams — no separate pipeline processor.
        assistant_agg,
    ])

    # Langfuse TurnTracer — owns the per-user-turn trace lifecycle. Noop
    # observer if LANGFUSE_* isn't configured. Session id is regenerated
    # per connect (future G9 multi-tenant work will key it on client).
    import uuid as _uuid
    turn_tracer = _tracing.make_turn_tracer(
        session_id=_uuid.uuid4().hex,
        user_id=user_id,
        llm_model=llm_model,
        stt_backend=(skill.stt or {}).get("backend") or STT_BACKEND,
        tts_backend=tts_backend,
    )

    # Barge-in observer flushes the Rust CPAL playback ring immediately
    # when the user interrupts the bot.
    _native_observers = [NativeBargeInObserver(transport)]

    task = PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True),
        # The native audio pipeline is persistent for the lifetime of the
        # app: mic frames flow continuously in production builds, so
        # Pipecat's default "cancel after 5 min idle" tears it down
        # mid-wizard / between turns. Disable.
        cancel_on_idle_timeout=False,
        # We construct RTVIProcessor explicitly above and SseBusObserver
        # is itself a subclass of RTVIObserver — Pipecat 1.1 auto-adds
        # both by default and logs "RTVIProcessor and RTVIObserver found,
        # skipping default ones" when it finds ours. Disabling the
        # auto-add silences the warning without changing behavior.
        enable_rtvi=False,
        # Observers see every frame at the pipeline level without
        # being a transformation node.
        observers=[
            EchoGuardObserver(_ECHO_STATE),
            turn_tracer,
            # SseBusObserver subclasses RTVIObserver — pipecat handles
            # frame → RTVI-message dispatch, the subclass redirects the
            # egress to the SSE bus on /api/events. One observer covers
            # what used to be two.
            SseBusObserver(rtvi),
            *_native_observers,
        ],
    )

    # Wire the delivery + backchannel controllers' out-of-band emit paths
    # now that the task exists. queue_frame is the only safe way to inject
    # frames from a foreign coroutine.
    delivery.set_emitter(task.queue_frame)
    # Per-delivery voice override (kokoro): lets a delivery speak in another
    # voice for one utterance then revert — e.g. notifications attributed to
    # different agents in distinct voices. Only kokoro honours the frame.
    if tts_backend == "kokoro":
        from voice.tts.kokoro import KokoroVoiceFrame
        delivery.set_voice_framer(lambda v: KokoroVoiceFrame(voice=v))
    # Record real proactive deliveries into conversation history (orbis-3ta)
    # so the orb remembers saying them and can reference them in talk.
    delivery.set_context(context)
    # Naturalize proactive deliveries via the micro LLM (orbis-2mh): phrase
    # reminders/pings/results in-character instead of speaking raw text. Falls
    # back to the raw line on timeout, so a slow micro-LLM never blocks a
    # delivery. Default on; NATURALIZE_DELIVERIES=0 speaks them verbatim.
    if os.environ.get("NATURALIZE_DELIVERIES", "1") == "1":
        _announce_gen = _filler_gen_for(user_id)

        async def _announce(content, kind, source):
            return await _announce_gen.announce(
                content, kind=kind or "update", source=source, tts_backend=tts_backend,
            )

        delivery.set_announcer(_announce)
    delivery.set_message_emitter(
        lambda payload: sse_bus.publish("delegation-progress", payload)
    )
    backchannel.set_emitter(task.queue_frame)

    # --- Duplex speak-while-thinking ---
    # Pre-tool acknowledgement ("hmm, let me check") is now emitted INLINE
    # by the LLM — see the TOOL USE block in tool_use_block(). Pipecat's
    # OpenAILLMService streams those tokens to TTS BEFORE running the
    # function call, so the user hears them naturally.
    #
    # This file only handles the channels pipecat's main response stream
    # cannot cover:
    #   - SLOW tools: LLM is blocked on the result, can't narrate.
    #     We synthesize "still working" lines via _FILLER_GEN.progress().
    #   - Backchannels during the user's turn: see BackchannelController.
    progress_tasks: set[asyncio.Task] = set()
    ack_tasks: set[asyncio.Task] = set()
    # No-repeat state for the instant opening ack (router-first D1 Phase 2).
    _opening_ack = {"last": None}
    # Chance the opening ack is freshly generated by the micro LLM (natural,
    # reacts to what the user actually asked) vs the canned pool. Defaults to
    # 1.0 — with the micro tier on a FAST LOCAL model (Ollama/MLX, ~250ms),
    # every non-fast opening ack should be live-generated; the canned pool is
    # only a timeout/failure fallback. Fast tools always use the instant pool.
    _opening_llm_chance = float(os.environ.get("OPENING_ACK_LLM_CHANCE", "1.0"))

    def _last_user_text() -> str | None:
        for m in reversed(context.messages):
            if m.get("role") == "user" and m.get("content"):
                c = m["content"]
                return c if isinstance(c, str) else str(c)
        return None

    async def _progress_loop(tool_name: str):
        """Two-tier cadence: ~2 s first ack, ~6 s later second ack, then
        silence. Over-narrating past ~8 s starts feeling performative.
        Cancelled on tool completion or barge-in via `_cancel_progress`."""
        try:
            _fs = user_state.filler_settings
            _fg = _filler_gen_for(user_id)
            for idx, sleep_secs in enumerate((
                _fs.progress_first_secs,
                _fs.progress_second_secs,
            )):
                await asyncio.sleep(sleep_secs)
                with _tracing.span(
                    "filler.progress",
                    input={"tool": tool_name, "tier": "first" if idx == 0 else "second"},
                ) as sp:
                    try:
                        phrase = await _fg.progress(
                            tool_name=tool_name,
                            user_utterance=_last_user_text(),
                            tts_backend=tts_backend,
                            # Ground the spoken check-in in the delegate's real
                            # latest streamed status (visual rail) when we have
                            # one — paraphrased, not narrated verbatim.
                            status_hint=delivery.last_progress,
                        )
                    except Exception as e:
                        sp.update(level="WARNING", status_message=str(e))
                        logger.warning(f"[filler:progress] generator raised: {e}")
                        phrase = None
                    if phrase:
                        sp.update(output=phrase)
                        logger.info(f"[filler:progress] {phrase!r}")
                        await task.queue_frame(
                            TTSSpeakFrame(phrase, append_to_context=False)
                        )
        except asyncio.CancelledError:
            pass

    async def _emit_opening_ack(tier: Latency) -> None:
        """Speak ONE opening ack for a starting tool call. Usually drawn
        instantly from the canned pool; occasionally (medium/slow tools only)
        freshly generated by the micro LLM for a surprise custom line, with a
        fallback to the pool on miss/timeout. Fire-and-forget so it never
        delays tool execution."""
        line: str | None = None
        if tier is not Latency.FAST and random.random() < _opening_llm_chance:
            try:
                line = await _filler_gen_for(user_id).opening(
                    user_utterance=_last_user_text(),
                    tts_backend=tts_backend,
                )
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[filler:opening] llm gen failed: {e}")
                line = None
        if not line:
            line = opening_ack_line(tts_backend, exclude=_opening_ack["last"])
        _opening_ack["last"] = line
        logger.info(f"[filler:opening] {line!r}")
        await task.queue_frame(TTSSpeakFrame(line, append_to_context=False))

    # Attach the function-call lifecycle handlers to every failover member
    # (orbis-1dd): the progress-narration loop + tool-call SSE events must
    # fire whichever LLM is active. On the default single-LLM path this is
    # just `llm`.
    def _wire_llm_callbacks(_member):
        @_member.event_handler("on_function_calls_started")
        async def _on_tool_start(_svc, function_calls):
            names = [fc.function_name for fc in function_calls]
            tier = max((latency_for(n) for n in names), key=lambda l: ["fast","medium","slow"].index(l.value))
            any_async = any(n in ASYNC_TOOL_NAMES for n in names)
            # Fresh status accumulator per tool turn so a delegate's spoken
            # check-in never grounds in the previous turn's leftover status.
            delivery.clear_progress()
            logger.info(
                f"[tool] {','.join(names)} tier={tier.value} async={any_async}"
            )
            _METRICS["tool_calls_total"] += len(names)
            for n in names:
                _METRICS["tool_calls_by_name"][n] = _METRICS["tool_calls_by_name"].get(n, 0) + 1
            first = function_calls[0] if function_calls else None
            args = getattr(first, "arguments", None) if first is not None else None
            await sse_bus.publish(
                "tool-call",
                {"event": "start", "name": names[0] if names else "", "args": args},
            )

            # Instant opening acknowledgement (router-first D1 Phase 2).
            # Phase 1 removed the inline LLM preamble that used to cover this
            # moment, so a tool turn would otherwise be silent until the
            # result. Speak ONE short ack the instant the call starts —
            # decoupled from the LLM (fired here, not narrated, so it can't
            # re-break tool emission) and AEC-safe because it triggers on a
            # definite tool-start signal, not VAD. Usually canned+instant;
            # occasionally micro-LLM-generated for variety (see
            # _emit_opening_ack). Fire-and-forget so it never delays the tool;
            # honours verbosity=silent. SKIP fast tools: their result returns
            # almost immediately, so the opening ack (fire-and-forget, may await
            # the micro-LLM) loses the race and queues BEHIND the response in
            # TTS order — the user hears the answer, then a pointless "on it".
            # A fast tool's own result is the acknowledgement.
            if (
                user_state.filler_settings.verbosity is not Verbosity.SILENT
                and tier is not Latency.FAST
            ):
                _t = asyncio.create_task(_emit_opening_ack(tier))
                ack_tasks.add(_t)
                _t.add_done_callback(ack_tasks.discard)

            # SLOW sync tools additionally get the progress narration loop
            # (~2 s / ~6 s "still working" lines). Async tools narrate
            # themselves via DeliveryController, so they skip the loop.
            if tier is Latency.SLOW and not any_async:
                # Tell the filler WHO it's waiting on (the delegate target) so
                # the check-in is "still waiting on Ava", not a generic/
                # self-action line.
                waiting_on = names[0]
                if names[0] == "delegate_to":
                    _a = args
                    if isinstance(_a, str):
                        try:
                            import json as _json
                            _a = _json.loads(_a)
                        except Exception:
                            _a = {}
                    if isinstance(_a, dict) and _a.get("target"):
                        waiting_on = str(_a["target"]).strip() or names[0]
                progress_tasks.add(asyncio.create_task(_progress_loop(waiting_on)))

        @_member.event_handler("on_function_calls_cancelled")
        async def _on_tool_cancel(_svc, _calls):
            logger.info("[filler] tool cancelled (barge-in)")
            _cancel_progress(publish_end=False)
            # Cancel any opening ack still being generated so it can't speak
            # over the user who just barged in (already-queued acks are out
            # of our hands, but a pending micro-LLM line is killed here).
            while ack_tasks:
                ack_tasks.pop().cancel()
            await sse_bus.publish("tool-call", {"event": "end", "outcome": "error"})

    for _member in _llm_members:
        _wire_llm_callbacks(_member)

    @transport.event_handler("on_client_connected")
    async def _on_connect(_t, _c):
        # Scope delivery + tracer + session to this user.
        state = user_state_for(user_id)
        state.active_delivery = delivery
        state.active_tracer = turn_tracer
        state.active_tts = tts  # for runtime voice switching via /api/tts/voice
        sid = turn_tracer.session_id if hasattr(turn_tracer, "session_id") else ""
        state.active_session_id = sid
        current_session_id.set(sid)
        # Record session activity NOW, not just on a clean disconnect. The dev
        # loop hard-kills the app (pkill -9), so the disconnect handler often
        # never runs and no session gets persisted — which made soft-neglect
        # read a weeks-old timestamp and wrongly turn the persona guarded/sulky
        # every session. INSERT OR REPLACE keeps it one row; the disconnect
        # handler fills in the real transcript later. (orbis neglect-freshness)
        if sid:
            try:
                from datetime import datetime, timezone
                _now_iso = datetime.now(timezone.utc).isoformat()
                get_memory().sessions.add(
                    session_id=sid, started_at=_now_iso, ended_at=_now_iso,
                    messages=[],
                )
            except Exception as e:  # noqa: BLE001 — best-effort presence touch
                logger.debug(f"[session] connect-touch failed: {e}")
        _tracing.set_active_tracer(turn_tracer, user_id=user_id)
        _tracing.start_session(sid)
        _METRICS["sessions_total"] += 1
        _METRICS["sessions_active"] += 1
        # Phase 5: notify SSE subscribers that a session has started.
        await sse_bus.publish("session", {"event": "start", "session_id": sid})
        await sse_bus.publish("bot-state", {"state": "idle"})
        # Reset echo-guard state so stale bot_stopped_at from a previous
        # session doesn't suppress audio at the start of a new one.
        _ECHO_STATE.bot_speaking = False
        _ECHO_STATE.bot_stopped_at = None
        logger.info(f"client connected (user={user_id!r})")
        # Replay any deliveries that arrived while we were disconnected
        # (a2a pushes, slow_research completions, scheduled messages).
        # The controller's bid-then-drain will ask before flushing if
        # there are ≥2 queued items.
        stashed = drain_stashed_deliveries(user_id)
        if stashed:
            logger.info(f"[replay] replaying {len(stashed)} stashed delivery(ies)")
            await delivery.replay_stashed(stashed)

    @transport.event_handler("on_client_disconnected")
    async def _on_disconnect(_t, _c):
        logger.info("client disconnected")
        # Persist anything still pending so the next session can replay.
        snapshot = delivery.snapshot_pending()
        for item in snapshot:
            stash_delivery(user_id, item)
        state = user_state_for(user_id)

        # Persist the session to SQLite so prior_n() sees it next boot.
        turns_for_analyzer: list[dict] = []
        try:
            mem = get_memory()
            sid = state.active_session_id or ""
            if sid:
                # Extract user+assistant turns + derive final_output from
                # the live context. Tool calls aren't timing-instrumented
                # here; leave tool_calls empty for now.
                final_output: str | None = None
                for msg in context.messages:
                    role = msg.get("role")
                    content = msg.get("content")
                    if role in ("user", "assistant") and isinstance(content, str) and content:
                        turns_for_analyzer.append({"role": role, "content": content})
                        if role == "assistant":
                            final_output = content
                mem.sessions.add(
                    session_id=sid,
                    started_at=None,  # DAL fills _now() when None
                    ended_at=None,
                    messages=turns_for_analyzer,
                    tool_calls=[],
                    final_output=final_output,
                    trace_id=getattr(turn_tracer, "trace_id", None),
                )
        except Exception as e:
            logger.warning(f"[memory] session persist failed: {e}")

        # Kick off post-session personality-drift analysis in the
        # background. Uses the session-level LLM endpoint; failures
        # are silent. Doesn't block the disconnect teardown.
        async def _run_drift_analysis() -> None:
            try:
                from agent.personality import analyze_session_drift, apply_drift
                deltas = await analyze_session_drift(
                    turns_for_analyzer,
                    llm_url=llm_url,
                    model=llm_model,
                    api_key=llm_api_key,
                )
                if deltas:
                    apply_drift(get_memory(), deltas)
                    logger.info(f"[personality] applied {len(deltas)} drift delta(s)")
            except Exception as e:
                logger.info(f"[personality] drift analysis errored: {e}")

        if turns_for_analyzer:
            asyncio.create_task(_run_drift_analysis(), name="orbis-drift-analysis")

        if state.active_delivery is delivery:
            state.active_delivery = None
        if state.active_tts is tts:
            state.active_tts = None
        if state.active_tracer is turn_tracer:
            state.active_tracer = None
            _tracing.set_active_tracer(None, user_id=user_id)
        state.active_session_id = None
        _tracing.flush()
        _METRICS["sessions_active"] = max(0, _METRICS["sessions_active"] - 1)
        # Phase 5: notify SSE subscribers that the session has ended.
        await sse_bus.publish("session", {"event": "end"})
        await sse_bus.publish("bot-state", {"state": "idle"})
        _cancel_progress(publish_end=False)
        await task.cancel()

    await PipelineRunner(handle_sigint=False).run(task)


# ---------------------------------------------------------------------------
# Prewarm
# ---------------------------------------------------------------------------

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


def _emit_boot(stage: str, detail: str) -> None:
    """Print a boot-progress marker for the Rust shell to parse and
    forward to the UI loading gate. Written to stdout because the shell
    reads the sidecar pipe even while Python's event loop is GIL-stalled
    by model loads (so progress keeps flowing during the slow steps)."""
    import json as _json

    try:
        print(f"ORBIS_BOOT {_json.dumps({'stage': stage, 'detail': detail})}", flush=True)
    except Exception:
        pass


def prewarm_all() -> None:
    logger.info(f"Prewarming (tts_backend={TTS_BACKEND})")
    # Real boot stages — each marker is emitted as that component begins
    # loading, so the UI loading screen reflects actual work, not a timer.
    # Each step is guarded so a failure never strands the loading gate
    # (which releases on the final "ready" marker).
    stt_detail = {
        "parakeet": "Loading Parakeet speech model…",
        "sensevoice": "Loading SenseVoice speech model…",
        "openai": "Connecting speech recognition…",
    }.get(STT_BACKEND, "Loading Whisper speech model…")
    tts_detail = (
        "Loading Kokoro voice…" if TTS_BACKEND == "kokoro" else "Loading speech synthesis…"
    )
    for stage, detail, fn in (
        ("stt", stt_detail, prewarm_stt),
        ("tts", tts_detail, prewarm_tts),
        ("llm", "Warming up the language model…", prewarm_llm),
    ):
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
    # confidence. Runs once at boot, then weekly. Uses Memory.facts.decay_and_prune().
    async def _curator_loop() -> None:
        while True:
            try:
                mem = get_memory()
                result = mem.facts.decay_and_prune()
                if result.get("decayed") or result.get("pruned"):
                    logger.info(
                        f"[curator] decayed={result['decayed']} pruned={result['pruned']}"
                    )
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

    # Entitlement refresh — re-query Stripe for the owner's latest
    # payment and extend the local cache. Runs once at boot, then
    # every REFRESH_INTERVAL_HOURS (default 24). Runs even when Stripe
    # is unconfigured — the function no-ops in that case.
    async def _entitlement_refresh_loop() -> None:
        from agent.entitlement import REFRESH_INTERVAL_HOURS, configured, refresh_from_stripe
        while True:
            if configured():
                try:
                    refresh_from_stripe(get_memory())
                except Exception as e:
                    logger.info(f"[entitlement] refresh failed: {e}")
            await asyncio.sleep(REFRESH_INTERVAL_HOURS * 3600)

    entitlement_task = asyncio.create_task(
        _entitlement_refresh_loop(), name="orbis-entitlement"
    )

    from agent.delegates import health_loop as _delegate_health_loop
    delegate_health_task = asyncio.create_task(
        _delegate_health_loop(_DELEGATES), name="orbis-delegate-health",
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
        for t in (curator_task, reminder_task, entitlement_task, delegate_health_task):
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
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


@app.get("/healthz")
async def health():
    """Public — no auth. Reports process-wide shape, not per-user state."""
    return {
        "status": "ok",
        "stt_backend": STT_BACKEND,
        "tts_backend": TTS_BACKEND,
        "auth_source": user_registry.source,
        "owner_configured": not user_registry.single_user_mode(),
        "active_sessions": len(active_user_states()),
        "delegates": [
            _delegate_health_payload(d, public=True) for d in _DELEGATES.all()
        ],
        "persona": get_active_persona().slug,
        "audio": {
            "transport": "native",
            **audio_runtime_info(),
            "socket_configured": bool(os.environ.get("ORBIS_AUDIO_SOCK")),
            "socket_connected": bool(_native_transport and _native_transport.connected),
            "mic_frames_received": (
                _native_transport.mic_frames_received if _native_transport else 0
            ),
            "speaker_frames_sent": (
                _native_transport.speaker_frames_sent if _native_transport else 0
            ),
            "pipeline_running": bool(
                _native_pipeline_task and not _native_pipeline_task.done()
            ),
            "half_duplex": HALF_DUPLEX,
            "echo_guard_ms": ECHO_GUARD_MS,
            "noise_filter": NOISE_FILTER,
            "smart_turn": SMART_TURN,
        },
    }


@app.get("/api/events")
async def events(user: User = Depends(require_user)):
    """Server-Sent Events stream of real-time bot state.

    The frontend (VoiceStateBridge) subscribes here so it can animate
    the orb and update the status pill. Drives the native audio path —
    WebRTC was removed in DECISIONS.md amendment 2026-04-28.

    Events emitted:
        bot-state   {"state": "idle"|"listening"|"thinking"|"speaking"}
        transcript  {"source": "user"|"bot", "text": "...", "final": true|false}
        session     {"event": "start"|"end", "session_id": "..."}
    """
    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        sse_bus.subscribe(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/metrics")
async def metrics(user: User = Depends(require_user)):
    uptime = time.time() - _METRICS["boot_at"]
    from agent import metrics as metrics_mod
    snap = metrics_mod.snapshot()
    return {
        **_METRICS,
        "uptime_secs": round(uptime, 1),
        "counters": snap["counters"],
        "gauges": snap["gauges"],
    }


@app.get("/api/whoami")
async def whoami(user: User = Depends(require_user)):
    """Return the resolved owner. Clients call this at boot to confirm
    their API key is valid and get the display name for UI chrome."""
    return {
        "id": user.id,
        "display_name": user.display_name,
        "auth_source": user_registry.source,
    }


@app.get("/api/verbosity")
async def get_verbosity(user: User = Depends(require_user)):
    return {"verbosity": user_state_for(user.id).filler_settings.verbosity.value}


@app.post("/api/verbosity")
async def set_verbosity(body: dict, user: User = Depends(require_user)):
    from agent.filler import Verbosity
    state = user_state_for(user.id)
    try:
        state.filler_settings.verbosity = Verbosity(body.get("level", "").lower())
    except ValueError:
        return {"error": "level must be silent|brief|narrated|chatty"}
    return {"verbosity": state.filler_settings.verbosity.value}


# Speaker-verification enrollment (#35 PR 1.3) ----------------------------
#
# The wizard captures ~10s of owner audio + posts it here. We decode +
# encode via ECAPAEmbedder and save the resulting embedding so the
# speaker_gate has a voiceprint to compare against on the next session.
#
# Single-owner deployment: the voiceprint is shared across all auth'd
# clients of this install. Re-enrolling overwrites; remove the file to
# revert to owner-trust mode.

_VOICEPRINT_MIN_DURATION_SECS = 3.0
_VOICEPRINT_MAX_DURATION_SECS = 30.0


@app.get("/api/voiceprint/status")
async def voiceprint_status(_user: User = Depends(require_user)):
    """Tells the wizard whether enrollment is needed."""
    p = get_voiceprint_path()
    return {
        "enrolled": p.exists(),
        "path": str(p),
        # speechbrain may not be installed even when a voiceprint exists,
        # so the wizard can show a different "install [speaker-id]"
        # message. Probed cheaply.
        "embedder_available": _is_speechbrain_available(),
    }


def _is_speechbrain_available() -> bool:
    import importlib.util
    return importlib.util.find_spec("speechbrain") is not None


@app.post("/api/voiceprint/enroll")
async def voiceprint_enroll(
    request: Request,
    user: User = Depends(require_user),
):
    """Encode + save an enrollment recording. Body is raw WAV bytes.

    Returns 200 with metadata on success, 4xx on validation, 501 if
    speechbrain isn't installed (bundled deployments can opt-in via
    ``pip install -e ".[speaker-id]"``)."""
    if not _is_speechbrain_available():
        raise HTTPException(
            status_code=501,
            detail=(
                "speechbrain not installed; cannot enroll. Install via "
                "`pip install -e \".[speaker-id]\"` or set "
                "`persona.behavior.speaker_gate: false` to keep owner-trust mode."
            ),
        )

    audio = await request.body()
    if not audio:
        raise HTTPException(status_code=400, detail="empty audio body")

    import io
    import soundfile as sf
    try:
        wav, sample_rate = sf.read(io.BytesIO(audio), dtype="float32")
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"could not decode audio: {e}; expected WAV bytes",
        )

    # Downmix to mono if needed.
    import numpy as _np
    if wav.ndim == 2:
        wav = wav.mean(axis=1)

    duration = len(wav) / float(sample_rate) if sample_rate else 0.0
    if duration < _VOICEPRINT_MIN_DURATION_SECS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"recording is {duration:.1f}s; need at least "
                f"{_VOICEPRINT_MIN_DURATION_SECS}s of audio for a stable embedding"
            ),
        )
    if duration > _VOICEPRINT_MAX_DURATION_SECS:
        # Truncate rather than reject — better UX than 400-ing on 31s.
        wav = wav[: int(_VOICEPRINT_MAX_DURATION_SECS * sample_rate)]
        duration = _VOICEPRINT_MAX_DURATION_SECS

    try:
        from agent.ecapa_embedder import ECAPAEmbedder
        embedder = ECAPAEmbedder()
        emb = embedder.encode(wav.astype(_np.float32, copy=False), sample_rate=sample_rate)
    except ImportError as e:
        # Should be unreachable given the precheck, but defend in depth.
        raise HTTPException(status_code=501, detail=str(e))
    except Exception as e:
        logger.exception("[enroll] embedding failed")
        raise HTTPException(
            status_code=500,
            detail=f"failed to encode embedding: {e}",
        )

    target = get_voiceprint_path()
    try:
        save_voiceprint(target, emb)
    except Exception as e:
        logger.exception("[enroll] save failed")
        raise HTTPException(status_code=500, detail=f"could not save voiceprint: {e}")

    logger.info(
        f"[enroll] saved voiceprint for user={user.id!r} "
        f"({duration:.1f}s @ {sample_rate} Hz, dim={emb.shape[0]}, path={target})"
    )
    return {
        "enrolled": True,
        "path": str(target),
        "duration_secs": round(duration, 2),
        "sample_rate": int(sample_rate),
        "embedding_dim": int(emb.shape[0]),
    }


@app.delete("/api/voiceprint")
async def voiceprint_delete(user: User = Depends(require_user)):
    """Remove the cached voiceprint — gate falls back to owner-trust on
    the next session. Use to start a fresh enrollment from the drawer
    UI or via curl when the audio sample needs to be replaced."""
    p = get_voiceprint_path()
    if p.exists():
        try:
            p.unlink()
            logger.info(f"[enroll] deleted voiceprint at {p} for user={user.id!r}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    return {"deleted": True, "path": str(p)}


@app.post("/api/users/reload")
async def reload_users_endpoint(user: User = Depends(require_user)):
    """Re-fetch the user roster from Infisical (if configured) or the
    YAML file. Safe to call mid-session — active clients keep their
    authenticated state until they reconnect; new connections use the
    refreshed registry."""
    names = user_registry.reload()
    return {"ok": True, "users": names, "source": user_registry.source}


@app.post("/api/delegates/reload")
async def reload_delegates_endpoint(user: User = Depends(require_user)):
    """Re-read config/delegates.yaml from disk.

    Safe mid-session — delegate lookup happens per `delegate_to()` call,
    so in-flight sessions see the new registry on their next dispatch.
    """
    names = _DELEGATES.reload()
    return {"ok": True, "delegates": names}


# --- Delegates CRUD ---------------------------------------------------------


@app.get("/api/delegates")
async def list_delegates_endpoint(user: User = Depends(require_user)):
    from agent.delegate_config_store import (
        DelegateValidationError,
        read_delegates,
    )
    try:
        # Uses the config-store DEFAULT_PATH, now unified to resolve the same
        # way the live registry does (DELEGATES_YAML) so it reads the file the
        # registry loaded. The registry fallback below covers any residual
        # path divergence in the bundle.
        entries = read_delegates()
    except DelegateValidationError as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

    parsed_names = set(_DELEGATES.names())
    parsed_by_name = {d.name: d for d in _DELEGATES.all()}

    # Belt-and-suspenders: if the file read produced nothing but the live
    # registry has delegates, surface those so the UI reflects reality.
    if not entries and parsed_by_name:
        entries = [
            {"name": d.name, "type": d.type, "url": d.url,
             "description": d.description}
            for d in _DELEGATES.all()
        ]

    def _decorate(entry: dict) -> dict:
        name = entry.get("name")
        d = parsed_by_name.get(name)
        h = _DELEGATES.health(name) if isinstance(name, str) else None
        return {
            **entry,
            "configured": name in parsed_names,
            "health": {
                "ok": h.ok if h else None,
                "latency_ms": h.latency_ms if h else None,
                "last_checked": h.last_checked if h else None,
                "last_error": h.last_error if h else None,
                "consecutive_failures": h.consecutive_failures if h else 0,
            } if d else None,
        }

    return {"delegates": [_decorate(e) for e in entries]}


@app.post("/api/delegates")
async def create_delegate_endpoint(
    body: dict, user: User = Depends(require_user),
):
    from agent.delegate_config_store import (
        DelegateValidationError,
        read_delegates,
        upsert_delegate,
    )
    try:
        name = (body or {}).get("name")
        if isinstance(name, str) and any(e.get("name") == name for e in read_delegates()):
            return JSONResponse(
                status_code=409,
                content={"error": f"delegate {name!r} already exists; use PUT to update"},
            )
        entries = upsert_delegate(body)
    except DelegateValidationError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    _DELEGATES.reload()
    return {"ok": True, "delegates": entries}


@app.put("/api/delegates/{name}")
async def update_delegate_endpoint(
    name: str, body: dict, user: User = Depends(require_user),
):
    from agent.delegate_config_store import (
        DelegateValidationError,
        read_delegates,
        upsert_delegate,
    )
    body = body or {}
    body_name = body.get("name")
    if body_name and body_name != name:
        return JSONResponse(
            status_code=400,
            content={
                "error": (
                    f"path name {name!r} does not match body name {body_name!r}; "
                    "DELETE then POST to rename"
                ),
            },
        )
    body["name"] = name
    if not any(e.get("name") == name for e in read_delegates()):
        return JSONResponse(
            status_code=404, content={"error": f"delegate {name!r} not found"},
        )
    try:
        entries = upsert_delegate(body)
    except DelegateValidationError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    _DELEGATES.reload()
    return {"ok": True, "delegates": entries}


@app.delete("/api/delegates/{name}")
async def delete_delegate_endpoint(name: str, user: User = Depends(require_user)):
    from agent.delegate_config_store import delete_delegate
    try:
        entries = delete_delegate(name)
    except KeyError:
        return JSONResponse(
            status_code=404, content={"error": f"delegate {name!r} not found"},
        )
    _DELEGATES.reload()
    return {"ok": True, "delegates": entries}


@app.post("/api/delegates/test")
async def test_delegate_endpoint(body: dict, user: User = Depends(require_user)):
    return await _probe_delegate(body or {})


async def _probe_delegate(entry: dict) -> dict:
    from agent.delegate_config_store import (
        DelegateValidationError,
        validate_entry,
    )
    from agent.delegates import _parse_entry, probe
    try:
        normalized = validate_entry(entry)
    except DelegateValidationError as e:
        return {"ok": False, "error": str(e)}
    delegate = _parse_entry(normalized)
    if delegate is None:
        return {"ok": False, "error": "registry rejected normalized entry"}
    return await probe(delegate)


@app.post("/api/persona/reload")
async def reload_persona_endpoint(user: User = Depends(require_user)):
    """Re-read config/orbis.yaml from disk.

    Applied on the next voice session (persona is snapshotted at
    connect time). Returns the loaded persona's slug + name."""
    persona = reload_persona()
    return {"ok": True, "slug": persona.slug, "name": persona.name}


@app.post("/api/llm/test")
async def llm_test(body: dict):
    """Real round-trip ping against a configured LLM endpoint.

    Body: ``{url, model, api_key?}``. Returns ``{ok, latency_ms?,
    error?, status?}``. Unauth on purpose — the setup wizard may run
    before the owner API key is set, and the user's LLM credentials
    are what's really being validated here, not their ORBIS auth.
    """
    from agent.llm_probe import ping_endpoint
    url = str(body.get("url") or "")
    api_key = str(body.get("api_key") or "")
    # "Leave blank to keep the saved key" must apply to the test too —
    # otherwise a blank field reports a false 'auth rejected' even when a
    # valid key is saved. Fall back to the resolved key, but only for the
    # already-configured URL so we never leak it to a different provider.
    if not api_key:
        try:
            saved = _resolve_skill_llm(get_active_persona())
            if not url or url == saved.get("url"):
                api_key = saved.get("api_key") or ""
        except Exception:
            pass
    return await ping_endpoint(
        url=url,
        model=str(body.get("model") or ""),
        api_key=api_key,
    )


@app.post("/api/llm/models")
async def llm_models(body: dict):
    """GET /models against a configured URL + API key. Returns
    ``{ok, models[], error?}``. Populates the wizard's model combobox.

    Unauth, same rationale as /api/llm/test.
    """
    from agent.llm_probe import list_models
    url = str(body.get("url") or "")
    api_key = str(body.get("api_key") or "")
    # Same "leave blank to keep" fallback as /api/llm/test (see there).
    if not api_key:
        try:
            saved = _resolve_skill_llm(get_active_persona())
            if not url or url == saved.get("url"):
                api_key = saved.get("api_key") or ""
        except Exception:
            pass
    return await list_models(url=url, api_key=api_key)


# ---------------------------------------------------------------------------
# Inbox — external systems push messages in for the agent to pull
# ---------------------------------------------------------------------------

INBOX_INGEST_TOKEN = os.environ.get("INBOX_INGEST_TOKEN", "")


def _inbox_writer_ok(request: Request) -> bool:
    """Owner key OR scoped ingest token."""
    x_api_key = request.headers.get("X-API-Key", "") or ""
    bearer = request.headers.get("Authorization", "") or ""
    if bearer.lower().startswith("bearer "):
        x_api_key = x_api_key or bearer[7:].strip()
    if user_registry.single_user_mode():
        return True
    if x_api_key and user_registry.resolve(x_api_key):
        return True
    if INBOX_INGEST_TOKEN and x_api_key == INBOX_INGEST_TOKEN:
        return True
    return False


@app.post("/api/inbox")
async def post_inbox(body: dict, request: Request):
    """Ingest a message into the agent inbox."""
    if not _inbox_writer_ok(request):
        raise HTTPException(status_code=401, detail="unauthorized")
    sender = (body.get("sender") or "").strip()
    subject = (body.get("subject") or "").strip()
    msg_body = body.get("body") or ""
    if not sender or not subject:
        raise HTTPException(
            status_code=400, detail="sender and subject are required",
        )
    channel = body.get("channel")
    created_at = body.get("created_at")
    priority = (body.get("priority") or "next").strip().lower()
    if priority not in ("now", "next", "later"):
        raise HTTPException(
            status_code=400,
            detail=f"priority must be one of now|next|later (got {priority!r})",
        )
    try:
        msg_id = get_memory().inbox.add(
            sender=sender,
            subject=subject,
            body=str(msg_body),
            channel=str(channel) if channel else None,
            priority=priority,  # type: ignore[arg-type]
            created_at=str(created_at) if created_at else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "id": msg_id}


_SAY_URGENCY_TO_PRIORITY = {
    "urgent": Priority.CRITICAL,       # interrupt now, even mid-speech
    "normal": Priority.TIME_SENSITIVE, # speak at the next natural pause
    "low": Priority.ACTIVE,            # surface when the topic comes up
}


@app.post("/api/say")
async def post_say(body: dict, request: Request):
    """Make the orb speak an externally-supplied message (orbis-wox).

    The external "ping ORBIS to speak / inhabit / message" primitive. Same
    auth as /api/inbox (owner key or ingest token). Unlike /api/inbox —
    which is pull-only and never proactively spoken — this routes straight
    into the DeliveryController so the orb actually voices it: immediately
    if a session is live (urgency-gated), else stashed and spoken on next
    connect.

    Body:
      text     — what to say (required)
      urgency  — urgent | normal (default) | low
      source   — optional attribution; if set, spoken as "<source> says — …",
                 if omitted the orb speaks it in its own voice (inhabit).
      voice    — optional Kokoro voice id (e.g. "af_bella"); speaks THIS
                 message in that voice and reverts after — so notifications
                 from different agents can each have their own voice without
                 changing the orb's own. Ignored if the voice is unknown.
    """
    if not _inbox_writer_ok(request):
        raise HTTPException(status_code=401, detail="unauthorized")
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    voice = (body.get("voice") or "").strip()
    voice_err = None
    if voice:
        from voice.tts.kokoro import KOKORO_VOICES, download_voice
        if voice not in KOKORO_VOICES:
            voice_err = f"unknown voice: {voice!r}"
            logger.warning(f"[say] {voice_err} — speaking in the current voice")
            voice = ""
        else:
            download_voice(voice)  # warm the tensor so the line doesn't stall
    urgency = (body.get("urgency") or "normal").strip().lower()
    if urgency not in _SAY_URGENCY_TO_PRIORITY:
        raise HTTPException(
            status_code=400,
            detail=f"urgency must be urgent|normal|low (got {urgency!r})",
        )
    priority = _SAY_URGENCY_TO_PRIORITY[urgency]
    source = (body.get("source") or "").strip() or None

    delivery = user_state_for(_A2A_USER_ID).active_delivery
    if delivery is None:
        # No live voice session — stash for replay on next connect (mirrors
        # the /a2a/push path: pre-attribute since replay passes source=None).
        from agent.session_store import stash_delivery
        attributed = f"{source} says — {text}" if source else text
        stash_delivery(_A2A_USER_ID, {
            "phrase": attributed,
            "policy": "now" if priority is Priority.CRITICAL else "next_silence",
            "priority": priority.value,
            "keywords": [],
        })
        return {"ok": True, "delivered": False, "stashed": True}

    await delivery.deliver(
        text, priority=priority, source=source, kind="ping",
        voice=voice or None,
    )
    resp = {"ok": True, "delivered": True}
    if voice:
        resp["voice"] = voice
    if voice_err:
        resp["voice_error"] = voice_err
    return resp


@app.get("/api/inbox")
async def get_inbox(
    user: User = Depends(require_user),
    unread_only: bool = False,
    priority_floor: str = "later",
    limit: int = 50,
):
    """List inbox messages, newest-first."""
    n = max(1, min(int(limit), 200))
    mem = get_memory()
    if unread_only:
        if priority_floor not in ("now", "next", "later"):
            raise HTTPException(
                status_code=400,
                detail=f"priority_floor must be now|next|later (got {priority_floor!r})",
            )
        msgs = mem.inbox.list_unread(
            priority_floor=priority_floor,  # type: ignore[arg-type]
            limit=n,
        )
    else:
        msgs = mem.inbox.list_all(limit=n)
    return {
        "messages": msgs,
        "unread_count": mem.inbox.count_unread(),
    }


@app.post("/api/inbox/deliver")
async def post_inbox_deliver(body: dict, user: User = Depends(require_user)):
    """Mark message ids as delivered. Body: ``{"ids": [1, 2, 3]}``."""
    raw_ids = body.get("ids") or []
    if not isinstance(raw_ids, list):
        raise HTTPException(status_code=400, detail="ids must be a list")
    ids: list[int] = []
    for v in raw_ids:
        try:
            ids.append(int(v))
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400, detail=f"invalid id: {v!r}",
            )
    n = get_memory().inbox.mark_delivered(ids)
    return {"ok": True, "delivered": n}


@app.get("/api/reminders")
async def get_reminders(user: User = Depends(require_user)):
    """List the user's pending reminders (one-time + recurring), soonest
    first. Powers the reminders UI and lets scripts inspect what's scheduled."""
    pend = get_memory().reminders.pending()
    return {"ok": True, "reminders": [
        {
            "id": r["id"],
            "text": r["text"],
            "fire_at": r["fire_at"],
            "recurring": bool(r.get("repeat_secs")),
            "repeat_secs": r.get("repeat_secs"),
        }
        for r in pend
    ]}


@app.post("/api/reminders/cancel")
async def cancel_reminders(body: dict, user: User = Depends(require_user)):
    """Cancel reminders. Body: ``{"id": 3}`` | ``{"match": "water"}`` |
    ``{"all": true}``. Cancelling stops recurring reminders too."""
    dal = get_memory().reminders
    if body.get("all"):
        return {"ok": True, "cancelled": dal.cancel_all()}
    if body.get("id") is not None:
        try:
            rid = int(body["id"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="id must be an integer")
        ok = dal.cancel(rid)
        return {"ok": ok, "cancelled": 1 if ok else 0}
    match = (body.get("match") or "").strip()
    if match:
        cancelled = dal.cancel_matching(match)
        return {"ok": True, "cancelled": len(cancelled),
                "items": [{"id": r["id"], "text": r["text"]} for r in cancelled]}
    raise HTTPException(status_code=400, detail="pass id, match, or all")


def _ollama_url_is_safe(url: str) -> bool:
    """Reject non-local Ollama URLs to prevent the unauth ``/api/llm/pull``
    route from being weaponized as an SSRF gadget.

    The route is unauth (the wizard runs before an API key is set up).
    Without this guard, a malicious page in the WKWebView (or anyone
    on 127.0.0.1 with the ephemeral port) could pass any URL and have
    the sidecar POST to it — most concerningly the cloud-metadata
    endpoint at 169.254.169.254 if the user later runs ORBIS on a
    cloud host.

    Allowed:
      - http(s) scheme
      - loopback by name (``localhost``, ``ip6-localhost``)
      - loopback or RFC-1918 private IPs (127.0.0.0/8, 10/8, 172.16/12,
        192.168/16, fc00::/7, ::1)
      - mDNS/Tailscale-style hostnames (``*.local``, ``*.lan``,
        ``*.ts.net``) so users with Ollama on another box on their
        tailnet still work

    Rejected: everything else, including link-local 169.254.x.x
    (cloud metadata) and any public hostname/IP.
    """
    import ipaddress
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if host in ("localhost", "ip6-localhost", "ip6-loopback"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # Hostname (not an IP literal). Constrain to local-network suffixes
        # that aren't routable on the public internet.
        return host.endswith(".local") or host.endswith(".lan") or host.endswith(".ts.net")
    # IP literal — accept loopback + private; reject link-local, the
    # all-zeros unspecified address (which on Linux means "any
    # interface" and would be a confused-deputy invitation), and
    # multicast. Python's `is_private` includes 169.254.0.0/16 (and
    # IPv6 fe80::/10), which is exactly the cloud-metadata range we
    # need to keep blocked, so check those out explicitly.
    if ip.is_link_local or ip.is_unspecified or ip.is_multicast:
        return False
    return ip.is_loopback or ip.is_private


@app.post("/api/llm/pull")
async def llm_pull(body: dict):
    """Stream an Ollama pull as Server-Sent Events.

    The wizard calls this when a user picks Ollama and the
    recommended model isn't installed yet — instead of asking them
    to drop into a terminal and run ``ollama pull <name>``, we
    proxy Ollama's native ``/api/pull`` and forward each NDJSON
    progress chunk as an SSE message. The frontend renders a
    progress bar from the ``completed`` / ``total`` fields.

    Body::

        {"name": "gemma3n:e2b", "url": "http://127.0.0.1:11434"}

    The ``url`` defaults to the local Ollama instance; we trim any
    trailing ``/v1`` so the same value used as ``llm.url`` for the
    OpenAI-compat endpoint also works here.

    Unauth — same rationale as ``/api/llm/detect_local``: this runs
    before the wizard has set up an API key. URL is constrained by
    ``_ollama_url_is_safe`` so the route can't be turned into an
    SSRF gadget.
    """
    from fastapi.responses import StreamingResponse
    import httpx as _httpx

    name = str(body.get("name") or "").strip()
    if not name:
        return JSONResponse({"error": "missing model name"}, status_code=400)

    raw_url = str(body.get("url") or "http://127.0.0.1:11434").rstrip("/")
    if raw_url.endswith("/v1"):
        raw_url = raw_url[:-3]
    if not _ollama_url_is_safe(raw_url):
        # Reject before opening a connection. The error is intentionally
        # specific — the wizard prompts on the response, and there's
        # no information leak: the validator only inspects the URL the
        # caller already supplied.
        return JSONResponse(
            {"error": f"refusing to proxy non-local Ollama URL: {raw_url}"},
            status_code=400,
        )

    async def _stream():
        timeout = _httpx.Timeout(connect=5.0, read=None, write=None, pool=None)
        async with _httpx.AsyncClient(timeout=timeout) as client:
            try:
                async with client.stream(
                    "POST",
                    f"{raw_url}/api/pull",
                    json={"model": name, "stream": True},
                ) as resp:
                    if resp.status_code != 200:
                        msg = await resp.aread()
                        yield f"event: error\ndata: {msg.decode(errors='replace')[:200]}\n\n"
                        return
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        # Ollama emits NDJSON; pass each line through
                        # as the data of an SSE message. Frontend just
                        # JSON.parses each event.data.
                        yield f"data: {line}\n\n"
                    yield "event: done\ndata: {}\n\n"
            except _httpx.HTTPError as e:
                yield f"event: error\ndata: {str(e)[:200]}\n\n"

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/llm/mlx/pull")
async def llm_mlx_pull(body: dict):
    """Stream an MLX model download (via huggingface_hub) as SSE.

    Frontend equivalent of ``/api/llm/pull`` but for the MLX path —
    when the wizard's user picks the Built-in (MLX) preset, this
    endpoint downloads the chosen ``mlx-community/...`` repo into
    the HF cache directly so the first voice session doesn't pay
    the multi-GB download cost. Emits ``data: {status, completed,
    total}`` progress events while the download runs, then a final
    ``event: done``.

    Body: ``{"model": "mlx-community/gemma-3n-E2B-it-4bit"}``

    Unauth — same rationale as ``/api/llm/detect_local``.
    """
    from fastapi.responses import StreamingResponse
    import asyncio as _asyncio
    from pathlib import Path as _Path

    model_id = str(body.get("model") or "").strip()
    if not model_id or "/" not in model_id:
        return JSONResponse(
            {"error": "model id required (e.g. mlx-community/gemma-3n-E2B-it-4bit)"},
            status_code=400,
        )

    async def _stream():
        try:
            from huggingface_hub import snapshot_download, HfApi
        except ImportError as e:
            yield f"event: error\ndata: huggingface_hub not available: {e}\n\n"
            return

        loop = _asyncio.get_running_loop()
        # HF_HOME can be set to override; otherwise the default is the
        # XDG-ish cache. Read it from the env so we look in the same
        # place huggingface_hub will write to.
        hf_home = os.environ.get(
            "HF_HOME", str(_Path.home() / ".cache/huggingface")
        )
        cache_dir = _Path(hf_home) / "hub" / f"models--{model_id.replace('/', '--')}"

        def _dir_size(p: _Path) -> int:
            if not p.exists():
                return 0
            return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())

        # Yield an immediate "starting" so the frontend stops sitting
        # at zero while the size-probe runs.
        yield 'data: {"status": "fetching repo metadata", "completed": 0, "total": 0}\n\n'

        # Get total size in an executor — HfApi is sync.
        total_bytes = 0
        try:
            info = await loop.run_in_executor(
                None, lambda: HfApi().repo_info(model_id, files_metadata=True)
            )
            for f in info.siblings or []:
                if f.size:
                    total_bytes += f.size
        except Exception as e:
            yield (
                f'data: {{"status": "couldn\\u0027t read total size, '
                f'progress percent will be missing: {str(e)[:100]}", '
                f'"completed": 0, "total": 0}}\n\n'
            )

        yield (
            f'data: {{"status": "downloading", "completed": 0, '
            f'"total": {total_bytes}}}\n\n'
        )

        fut = loop.run_in_executor(None, snapshot_download, model_id)

        last = -1
        while not fut.done():
            completed = _dir_size(cache_dir)
            if completed != last:
                yield (
                    f'data: {{"status": "downloading", '
                    f'"completed": {completed}, "total": {total_bytes}}}\n\n'
                )
                last = completed
            await _asyncio.sleep(0.4)

        try:
            await fut
        except Exception as e:
            yield f"event: error\ndata: {str(e)[:200]}\n\n"
            return

        completed = _dir_size(cache_dir) or total_bytes
        yield (
            f'data: {{"status": "done", '
            f'"completed": {completed}, "total": {total_bytes or completed}}}\n\n'
        )
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/llm/detect_local")
async def llm_detect_local():
    """Parallel probe Ollama (:11434) + LM Studio (:1234) on localhost.
    Returns only the providers that responded — voice-first homelab
    users get a "we noticed your local Ollama" callout in the wizard.

    Unauth — localhost detection before auth is set is the whole point.
    """
    from agent.llm_probe import detect_local
    return await detect_local()


@app.get("/api/tts/voices")
async def tts_voices(
    backend: str = "kokoro",
    user: User = Depends(require_user),
):
    """Enumerate voices/references for a TTS backend.

    The native settings panel uses this to render a picker where the
    backend has a finite catalogue. Each entry is ``{id, label, ...}``;
    backends that cache voices on disk include ``cached``.
    """
    backend = (backend or "kokoro").strip().lower()
    if backend == "kokoro":
        from voice.tts.kokoro import list_voices
        return {"backend": "kokoro", "voices": list_voices()}
    if backend == "openai":
        from voice.tts.openai import OPENAI_TTS_VOICES
        return {
            "backend": "openai",
            "voices": [{"id": v, "label": v} for v in OPENAI_TTS_VOICES],
        }
    if backend == "fish":
        from voice.tts.fish import FISH_URL, list_references
        refs = list_references()
        return {
            "backend": "fish",
            "fish_url": FISH_URL,
            "voices": [{"id": r, "label": r} for r in refs],
        }
    if backend == "elevenlabs":
        return {"backend": "elevenlabs", "voices": []}
    return {"backend": backend, "voices": [], "error": f"unknown backend: {backend!r}"}


@app.post("/api/tts/voices/download")
async def tts_download_voice(
    payload: dict,
    user: User = Depends(require_user),
):
    """Eagerly download a Kokoro voice tensor into the local HF cache."""
    backend = (payload.get("backend") or "").strip().lower()
    voice = (payload.get("voice") or "").strip()
    if not voice:
        return {"ok": False, "error": "voice is required"}
    if backend == "kokoro":
        from voice.tts.kokoro import download_voice
        return download_voice(voice)
    return {"ok": False, "error": f"{backend!r} backend has no downloadable voices"}


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


@app.post("/api/tts/voice")
async def tts_set_voice(payload: dict, user: User = Depends(require_user)):
    """Switch the live TTS voice mid-session (no rebuild/reconnect). The next
    spoken line uses it. Body: ``{voice: "af_bella"}``."""
    return _switch_live_voice(payload.get("voice") or "")


@app.get("/api/starter_orbs")
async def get_starter_orbs():
    """Return the curated starter-orb pool. The setup wizard calls this
    at first boot so the user can pick one; no auth required so the
    wizard can run before the user has their API key.

    Response shape::
        {"starters": [{slug, name, description, variant, palette, params}, ...]}
    """
    from agent.starter_orbs import load_starters
    starters = load_starters()
    return {"starters": [s.to_dict() for s in starters]}


@app.get("/api/config")
async def get_config(user: User = Depends(require_user)):
    """Return the current config/orbis.yaml as a dict. Drawer UI
    consumes this to populate the settings form."""
    from agent.config_store import read_config
    cfg = read_config()
    # Surface the *effective* STT backend so Settings reflects what's
    # actually running. The native build defaults the backend via the
    # STT_BACKEND env (parakeet); if the user hasn't explicitly set
    # stt.backend in config, fill it in so the UI doesn't show a stale
    # default ("Whisper") while Parakeet is the live backend.
    stt = cfg.get("stt")
    if not isinstance(stt, dict):
        stt = {}
    if not stt.get("backend"):
        stt["backend"] = STT_BACKEND
        cfg["stt"] = stt
    return {"config": cfg}


@app.get("/api/personality")
async def get_personality(user: User = Depends(require_user)):
    """Return current personality state: axes + mood + recent drift
    events + session stats. Drives the drawer's Profile panel so the
    user can see why the orb feels a certain way."""
    mem = get_memory()
    try:
        axes = [
            {"axis": a.axis, "value": a.value, "updated_at": a.updated_at}
            for a in mem.personality.all_axes()
        ]
    except Exception:
        axes = []
    try:
        mood = mem.personality.get_mood()
        mood_dict = {
            "valence": mood.valence,
            "arousal": mood.arousal,
            "guardedness": mood.guardedness,
            "updated_at": mood.updated_at,
        }
    except Exception:
        mood_dict = None
    try:
        events = mem.personality.recent_events(limit=20)
    except Exception:
        events = []
    try:
        session_count = mem.sessions.count()
        last_session_ended_at = mem.sessions.last_ended_at()
    except Exception:
        session_count = 0
        last_session_ended_at = None
    return {
        "axes": axes,
        "mood": mood_dict,
        "recent_events": events,
        "sessions": {
            "count": session_count,
            "last_ended_at": last_session_ended_at,
        },
    }


@app.post("/api/orb/select_starter")
async def select_starter(body: dict, user: User = Depends(require_user)):
    """Commit a starter-orb pick to config/orbis.yaml. Called by the
    setup wizard after the user picks. Validates the slug against
    the pool, writes the orb block, reloads persona.

    Body: ``{"slug": "<starter_slug>"}``."""
    slug = (body.get("slug") or "").strip()
    if not slug:
        raise HTTPException(status_code=400, detail="slug is required")
    from agent.starter_orbs import find_starter
    from agent.config_store import merge_patch
    hit = find_starter(slug)
    if not hit:
        raise HTTPException(
            status_code=404, detail=f"unknown starter: {slug!r}",
        )
    merge_patch({
        "orb": {
            "variant": hit.variant,
            "palette": hit.palette,
            "params": dict(hit.params),
        },
    })
    reload_persona()
    return {
        "ok": True,
        "starter": hit.to_dict(),
    }


@app.get("/api/entitlement")
async def get_entitlement(user: User = Depends(require_user)):
    """Return the owner's current entitlement state — used by the UI
    to gate paid-tier features (customization editor, variant picker)."""
    from agent.entitlement import entitlement_state
    return entitlement_state(get_memory())


@app.post("/api/entitlement/checkout")
async def create_checkout(user: User = Depends(require_user)):
    """Create a Stripe Checkout Session for the customization unlock.
    Returns ``{"url": "<stripe-hosted checkout page>"}``. Client
    redirects the user there; success/cancel URLs come back via
    STRIPE_SUCCESS_URL / STRIPE_CANCEL_URL."""
    from agent.entitlement import EntitlementError, configured, create_checkout_session
    if not configured():
        raise HTTPException(
            status_code=503,
            detail="Stripe is not configured on this install.",
        )
    try:
        url = create_checkout_session()
    except EntitlementError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        logger.exception("[entitlement] checkout session creation failed")
        raise HTTPException(status_code=500, detail=f"checkout failed: {exc}")
    return {"url": url}


@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request):
    """Stripe webhook endpoint. Verified via signature header. NOT auth-
    gated — Stripe's webhook call doesn't carry our API key; the
    signature check is the authentication. Configure this URL in the
    Stripe dashboard + set STRIPE_WEBHOOK_SECRET in .env."""
    from agent.entitlement import EntitlementError, configured, handle_webhook_event
    if not configured():
        raise HTTPException(
            status_code=503, detail="Stripe is not configured."
        )
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    try:
        result = handle_webhook_event(payload, signature, get_memory())
    except EntitlementError as exc:
        # Signature failure → 400 (Stripe retries on non-2xx).
        raise HTTPException(status_code=400, detail=str(exc))
    return result


@app.post("/api/config")
async def put_config(patch: dict, user: User = Depends(require_user)):
    """Apply a shallow-merge patch to config/orbis.yaml. Returns the
    normalized post-write config. Reloads the in-memory persona so
    the next voice session uses the new values.

    Body shape is a partial config::

        {"persona": {"name": "Atlas"}}              # rename
        {"voice": {"tts_backend": "elevenlabs"}}    # swap provider
        {"orb": {"variant": "nebula", "palette": "Helios"}}   # paid

    ``persona`` + ``voice`` blocks are always editable. The ``orb``
    block requires the paid customization unlock — requests with an
    ``orb`` block while the caller lacks the entitlement return 403.
    Starter-orb selection happens via /api/orb/select_starter and is
    always allowed (restricted to the curated pool).

    Drops unknown keys with a warning. Raises 400 on typed failures
    (invalid tts_backend, non-numeric temperature, etc.).
    """
    from agent.config_store import merge_patch
    from agent.entitlement import has_customization

    # Paid-tier gate — orb block changes require the customization
    # unlock. This is the authoritative gate on direct /api/config
    # POST requests.
    if isinstance(patch, dict) and patch.get("orb"):
        if not has_customization(get_memory()):
            raise HTTPException(
                status_code=403,
                detail=(
                    "Orb customization is part of the paid unlock. "
                    "Use /api/orb/select_starter to pick from the free "
                    "starter pool, or purchase the unlock."
                ),
            )

    try:
        normalized = merge_patch(patch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    persona = reload_persona()
    return {"ok": True, "config": normalized, "persona": persona.slug}


def _serve_react() -> bool:
    if FRONTEND == "vanilla":
        return False
    if FRONTEND == "react":
        return True
    # auto — use react when the bundle is present.
    return WEB_DIST.exists() and (WEB_DIST / "index.html").exists()


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
    text_agent=text_agent,
    delivery_provider=lambda: user_state_for(_A2A_USER_ID).active_delivery,
    skill_slug_provider=lambda: get_active_persona().slug,
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

    # Port 0 → OS assigns. Pre-bind a socket so we can print the real
    # port BEFORE uvicorn starts (the Tauri shell reads stdout for the
    # readiness line). uvicorn's Config accepts a pre-bound fd, which
    # closes the race window between knowing the port and listening on it.
    import socket
    import uvicorn

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((args.host, args.port))
    sock.listen(128)
    bound_host, bound_port = sock.getsockname()[:2]

    # Canonical readiness line — Tauri + other supervisors grep for this
    # prefix to learn where to connect. Keep it first on stdout; any
    # logger output is on stderr.
    print(f"ORBIS_READY http://{bound_host}:{bound_port}", flush=True)

    config = uvicorn.Config(app, fd=sock.fileno())
    server = uvicorn.Server(config)
    server.run()


if __name__ == "__main__":
    main()
