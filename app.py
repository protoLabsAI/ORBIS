#!/usr/bin/env python3
"""protoVoice — Pipecat pipeline with duplex filler (through M2).

Pipeline:

  browser mic → SmallWebRTCTransport.input()
              → LocalWhisperSTT
              → user aggregator (VAD attached here in pipecat 1.0)
              → OpenAILLMService — has `deep_research` tool registered
              → TTS (Fish sidecar by default, Kokoro fallback)
              → SmallWebRTCTransport.output()
              → assistant aggregator

Duplex (M2):
  - on `on_function_calls_started`: queue a TTSSpeakFrame opening filler
  - `_progress_loop()`: emit periodic progress phrases while the tool runs
  - tool handlers are wrapped so they cancel the progress loop on return

Still ahead: M3 async tool inbox + push-interrupt (`cancel_on_interruption=False`),
M4 real tool set, M5 memory + skills + SOUL.
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

# Load .env BEFORE any other module reads os.environ. python-dotenv leaves
# already-set env vars alone (shell env wins over .env — standard).
# For deployed boxes, Infisical (or whichever secrets manager) injects
# env vars at container start; this block then no-ops because the file
# isn't there. Local dev + CI keep a .env; production doesn't.
try:
    from dotenv import load_dotenv
    load_dotenv()
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
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMAssistantAggregatorParams,
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frameworks.rtvi import (
    RTVIObserverParams,
    RTVIProcessor,
)
from pipecat.utils.context.llm_context_summarization import (
    LLMAutoContextSummarizationConfig,
    LLMContextSummaryConfig,
)
from pipecat.services.openai.llm import OpenAILLMService

from voice.llm import make_llm
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.request_handler import (
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

from a2a.server import register_a2a_routes
from agent.backchannel import BackchannelController
from agent.bargein import BargeInGate
from agent.delegates import DelegateRegistry
from agent.micro_ack import MicroAckInjector
from agent.echo_guard import (
    ECHO_GUARD_MS,
    HALF_DUPLEX,
    EchoGuardObserver,
    EchoGuardState,
    EchoGuardSuppressor,
)
from agent.delivery import DeliveryController
from agent.prosody import ProsodyTagStripper
from agent.filler import (
    FillerGenerator,
    Latency,
    Settings as FillerSettings,
    Verbosity,
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
from agent.tools import (
    ASYNC_TOOL_NAMES,
    build_text_tool_schemas,
    latency_for,
    register_tools,
    run_text_tool,
)
from auth import load_users, require_user, user_registry
from auth.users import DEFAULT_USER, User
from auth.context import current_session_id, current_user_id
from agent.user_state import active_user_states, user_state_for, UserState
from voice import lifecycle
from voice.stt import STT_BACKEND, make_stt, prewarm as prewarm_stt, transcribe_bytes
from voice.tts import TTS_BACKEND, make_tts, prewarm as prewarm_tts

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("protovoice")

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
    using_custom_url. Callers compose request kwargs from this.
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
    return {
        "url": url,
        "model": model,
        "api_key": api_key,
        "extra_body": extra_body,
        "using_custom_url": using_custom_url,
    }


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
    stored on UserState."""
    state = user_state_for(user_id)
    if state.filler_generator is None:
        state.filler_generator = FillerGenerator(
            llm_url=LLM_URL,
            model=LLM_SERVED_NAME,
            api_key=LLM_API_KEY,
            settings=state.filler_settings,
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
    "clone_requests_total": 0,
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


def _effective_prompt(
    skill, tts_backend: str, *, verbosity, user_id: str,
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
    try:
        mem = get_memory()
        # Run the neglect computation BEFORE rendering the personality
        # block — it adjusts mood, and render_personality_block reads
        # mood. So the session's opening vibe reflects the gap.
        _days, neglect_nudge = apply_soft_neglect(mem)
        personality = render_personality_block(mem)
    except Exception as e:
        logger.warning(f"[personality] render failed: {e}")
        personality = ""

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
        + (("\n\n" + plan) if plan else "")
        + "\n\n"
        + repair_block()
        + (("\n\n" + user_block) if user_block else "")
        + (("\n\n" + personality) if personality else "")
        + (("\n\n## RETURN\n\n" + neglect_nudge) if neglect_nudge else "")
        + (("\n\n" + recall) if recall else "")
    )


# ---------------------------------------------------------------------------
# Text-only agent — used by inbound A2A (no voice, no tools, one-shot).
# Keeps dependence on the pipeline decoupled so callers can hit /a2a even
# when no WebRTC session is active.
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
# a protoVoice user whose skill / memory / verbosity the inbound turn
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

_handler = SmallWebRTCRequestHandler()


async def run_bot(webrtc_connection, user_id: str = "default") -> None:
    """One bot instance per connected WebRTC client.

    `user_id` is resolved at `/api/offer` time from the X-API-Key header
    and passed in via a closure. Defaults to "default" for direct callers
    that bypass the auth layer (unlikely in practice — the only entry
    point is `/api/offer`).
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

    transport = SmallWebRTCTransport(
        webrtc_connection=webrtc_connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_out_10ms_chunks=2,
            # Optional in-filter (rnnoise) for noise reduction on the mic
            # stream. Wired only when NOISE_FILTER is enabled in env.
            audio_in_filter=_build_audio_in_filter(),
        ),
    )

    stt = make_stt()

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
        provider=skill_llm.get("provider"),
        using_custom_url=using_custom_llm,
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
    def _cancel_progress():
        while progress_tasks:
            t = progress_tasks.pop()
            t.cancel()

    # D17: attach pushNotificationConfig on outbound A2A so remote agents
    # can call us back via /a2a/push even if the SSE stream dropped.
    # Env-driven — if A2A_PUSH_URL isn't set (typical local dev), the
    # config is omitted and outbound A2A is stream-only.
    _push_url = os.environ.get("A2A_PUSH_URL") or None
    _push_token = os.environ.get("A2A_PUSH_TOKEN") or None
    tools_schema = register_tools(
        llm,
        on_finish=_cancel_progress,
        delivery=delivery,
        delegates=session_delegates,
        push_notification_url=_push_url,
        push_notification_token=_push_token,
    )

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
            ),
        }],
        tools=tools_schema,
    )

    _turn_strategies = _build_user_turn_strategies()
    _user_agg_kwargs: dict = {"vad_analyzer": SileroVADAnalyzer()}
    if _turn_strategies is not None:
        # Only pass user_turn_strategies when we actually built one — passing
        # None keeps the default (naive VAD endpointing).
        _user_agg_kwargs["user_turn_strategies"] = _turn_strategies

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

    pipeline = Pipeline([
        transport.input(),
        # Echo-guard sits IMMEDIATELY after transport.input — drops mic
        # audio while the bot is speaking (HALF_DUPLEX) and for ECHO_GUARD_MS
        # after it stops. VAD downstream never sees the suppressed audio.
        EchoGuardSuppressor(_ECHO_STATE),
        # RTVI processor near the top — forwards inbound client messages
        # (config, custom actions) into the pipeline and exposes the
        # push-channel for the observer.
        rtvi,
        stt,
        user_agg,
        # Adaptive barge-in gate — suppresses VAD-triggered interrupts
        # that resolve within the grace window as coughs / backchannels /
        # background noise. Real interrupts still fire, just confirmed.
        BargeInGate(
            enabled=bg_cfg["enabled"],
            **({"grace_ms": int(bg_cfg["grace_ms"])} if "grace_ms" in bg_cfg else {}),
        ),
        # Micro-ack injector — if the main pipeline hasn't produced audio
        # within ~1500 ms (default; per-persona override via
        # behavior.micro_ack.first_ms) of UserStoppedSpeaking, emit a
        # quiet "mm" / "hm" so the agent feels responsive on slow turns.
        # Cancels when the bot actually starts speaking. Vapi Fill
        # Injection pattern.
        MicroAckInjector(
            tts_backend=tts_backend,
            enabled=ma_cfg["enabled"],
            **({"trigger_ms": int(ma_cfg["first_ms"])} if "first_ms" in ma_cfg else {}),
        ),
        # Both placed after the gate — they need TranscriptionFrames and
        # VAD frames produced by the aggregator. Push downstream into TTS.
        backchannel,
        delivery,
        llm,
        # Non-Fish TTS services strip tags at the service level via their
        # text_filters= kwarg (see voice/tts/{kokoro,openai}.py). Fish
        # consumes `[softly]` / `[pause:300]` natively, so its adapter
        # doesn't filter.
        tts,
        transport.output(),
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
        user_id=None,  # multi-tenant work assigns per-client ids later
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True),
        # Observers see every frame at the pipeline level without
        # being a transformation node.
        observers=[
            EchoGuardObserver(_ECHO_STATE),
            turn_tracer,
            # RTVI observer emits structured client messages (bot-llm-*,
            # bot-tts-*, user-*, function-call-*). Client consumption
            # will land with the React frontend migration.
            rtvi.create_rtvi_observer(params=RTVIObserverParams()),
        ],
    )

    # Wire the delivery + backchannel controllers' out-of-band emit paths
    # now that the task exists. queue_frame is the only safe way to inject
    # frames from a foreign coroutine.
    delivery.set_emitter(task.queue_frame)
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

    @llm.event_handler("on_function_calls_started")
    async def _on_tool_start(_svc, function_calls):
        names = [fc.function_name for fc in function_calls]
        tier = max((latency_for(n) for n in names), key=lambda l: ["fast","medium","slow"].index(l.value))
        any_async = any(n in ASYNC_TOOL_NAMES for n in names)
        logger.info(
            f"[tool] {','.join(names)} tier={tier.value} async={any_async}"
        )
        _METRICS["tool_calls_total"] += len(names)
        for n in names:
            _METRICS["tool_calls_by_name"][n] = _METRICS["tool_calls_by_name"].get(n, 0) + 1

        # Only SLOW sync tools get the progress narration loop. The opening
        # acknowledgement is handled inline by the LLM via the TOOL USE
        # prompt block. Async tools narrate themselves via DeliveryController.
        if tier is Latency.SLOW and not any_async:
            progress_tasks.add(asyncio.create_task(_progress_loop(names[0])))

    @llm.event_handler("on_function_calls_cancelled")
    async def _on_tool_cancel(_svc, _calls):
        logger.info("[filler] tool cancelled (barge-in)")
        _cancel_progress()

    @transport.event_handler("on_client_connected")
    async def _on_connect(_t, _c):
        # Scope delivery + tracer + session to this user.
        state = user_state_for(user_id)
        state.active_delivery = delivery
        state.active_tracer = turn_tracer
        sid = turn_tracer.session_id if hasattr(turn_tracer, "session_id") else ""
        state.active_session_id = sid
        current_session_id.set(sid)
        _tracing.set_active_tracer(turn_tracer, user_id=user_id)
        _tracing.start_session(sid)
        _METRICS["sessions_total"] += 1
        _METRICS["sessions_active"] += 1
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
        if state.active_tracer is turn_tracer:
            state.active_tracer = None
            _tracing.set_active_tracer(None, user_id=user_id)
        state.active_session_id = None
        _tracing.flush()
        _METRICS["sessions_active"] = max(0, _METRICS["sessions_active"] - 1)
        _cancel_progress()
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
        logger.warning(f"LLM prewarm skipped: {e}")


def prewarm_all() -> None:
    logger.info(f"Prewarming (tts_backend={TTS_BACKEND})")
    prewarm_stt()
    prewarm_tts()
    prewarm_llm()


# ---------------------------------------------------------------------------
# FastAPI
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    lifecycle.start()
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

    try:
        yield
    finally:
        for t in (curator_task, entitlement_task):
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        await _handler.close()
        lifecycle.stop()


app = FastAPI(title="ORBIS", lifespan=lifespan)


@app.post("/api/offer")
async def offer(
    req: SmallWebRTCRequest,
    bg: BackgroundTasks,
    user: User = Depends(require_user),
):
    # Capture the resolved user id in the closure so run_bot can key
    # its per-user state correctly. pipecat's on_client_connected
    # fires synchronously inside handle_web_request after the SDP is
    # accepted, and can't read FastAPI request headers from there.
    user_id = user.id

    async def on_conn(conn):
        bg.add_task(run_bot, conn, user_id=user_id)
    return await _handler.handle_web_request(request=req, webrtc_connection_callback=on_conn)


@app.patch("/api/offer")
async def ice(req: SmallWebRTCPatchRequest, user: User = Depends(require_user)):
    await _handler.handle_patch_request(req)
    return {"status": "success"}


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
            {"name": d.name, "type": d.type} for d in _DELEGATES.all()
        ],
        "persona": get_active_persona().slug,
        "audio": {
            "half_duplex": HALF_DUPLEX,
            "echo_guard_ms": ECHO_GUARD_MS,
            "noise_filter": NOISE_FILTER,
            "smart_turn": SMART_TURN,
        },
    }


@app.get("/api/metrics")
async def metrics(user: User = Depends(require_user)):
    uptime = time.time() - _METRICS["boot_at"]
    return {
        **_METRICS,
        "uptime_secs": round(uptime, 1),
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
    return await ping_endpoint(
        url=str(body.get("url") or ""),
        model=str(body.get("model") or ""),
        api_key=str(body.get("api_key") or ""),
    )


@app.post("/api/llm/models")
async def llm_models(body: dict):
    """GET /models against a configured URL + API key. Returns
    ``{ok, models[], error?}``. Populates the wizard's model combobox.

    Unauth, same rationale as /api/llm/test.
    """
    from agent.llm_probe import list_models
    return await list_models(
        url=str(body.get("url") or ""),
        api_key=str(body.get("api_key") or ""),
    )


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
    return {"config": read_config()}


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
    # unlock. This is the authoritative gate; the tool-call path
    # (set_variant / apply_palette / adjust_param / save_preset)
    # also gates, but a direct /api/config POST would otherwise
    # bypass the tools entirely.
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
    # New (react) or legacy (vanilla) SPA. Canonical p2p-webrtc client
    # adds BOTH audio+video transceivers (required by SmallWebRTCTransport)
    # and queues ICE until pc_id is known.
    if _serve_react():
        return FileResponse(str(WEB_DIST / "index.html"))
    return FileResponse(str(STATIC_DIR / "index.html"))


# Legacy vanilla shell stays mounted for a deprecation window.
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# React SPA assets — /assets/*, /pwa-*.png, /manifest.webmanifest, /sw.js, etc.
if _serve_react():
    app.mount(
        "/assets",
        StaticFiles(directory=str(WEB_DIST / "assets")),
        name="assets",
    )
    # Root-level SPA artifacts — manifest, service worker + registration
    # shim, workbox chunks (hash-named so they change per build), icons,
    # favicon. Enumerated from dist/ at startup so new Vite-emitted files
    # don't require a route update.
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
        lifecycle.stop()
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
