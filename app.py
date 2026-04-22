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

# Route HF downloads to the mounted model cache before transformers imports anything.
os.environ.setdefault("HF_HOME", os.environ.get("MODEL_DIR", "/models"))

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
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
from skills.loader import load_skills, write_voice_clone_skill
from skills.models import DEFAULT_SOUL_SLUG, Skill
from auth import load_users, require_admin, require_user, user_registry
from auth.users import DEFAULT_USER, User
from auth.context import current_session_id, current_user_id
from agent.user_state import active_user_states, user_state_for, UserState
from voice import lifecycle
from voice.stt import STT_BACKEND, make_stt, prewarm as prewarm_stt, transcribe_bytes
from voice.tts import TTS_BACKEND, make_tts, prewarm as prewarm_tts
from voice.tts.fish import add_reference as fish_add_reference, list_references as fish_list_references

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

# Skills registry + the currently selected skill. A `SYSTEM_PROMPT` env
# override wins over the skill's prompt (kept for backwards compat).
_SKILLS: dict[str, Skill] = load_skills(CONFIG_DIR)
_SYSTEM_PROMPT_ENV_OVERRIDE = os.environ.get("SYSTEM_PROMPT") or None

# Delegate registry — A2A agents + OpenAI-compat endpoints the agent can
# hand off to via `delegate_to`. Loaded once at boot. Shared across users.
_DELEGATES_YAML = Path(os.environ.get("DELEGATES_YAML", "config/delegates.yaml"))
_DELEGATES = DelegateRegistry(_DELEGATES_YAML)


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


def _resolved_skill_slug(user: User | None, user_id: str | None = None) -> str:
    """Resolve which skill slug this user's next session will use.

    Admins + unconstrained users fall back to the mutable
    user_state.skill_slug. A non-admin with ``allowed_skills`` set gets
    their mutable slug only if it's in the allowed list — otherwise we
    snap to the first allowed entry (avoids a stale mutable value locking
    them out of their own allowed set).
    """
    uid = user.id if user else (user_id or "default")
    mutable = user_state_for(uid).skill_slug or DEFAULT_SOUL_SLUG
    if user and not user.is_admin and user.allowed_skills is not None:
        if mutable in user.allowed_skills:
            return mutable
        return user.allowed_skills[0]
    return mutable


def _active_skill(user_id: str) -> Skill:
    slug = _resolved_skill_slug(user_registry.by_id(user_id), user_id=user_id)
    return _SKILLS.get(slug) or _SKILLS[DEFAULT_SOUL_SLUG]


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
    skill: Skill, tts_backend: str, *, verbosity, user_id: str,
) -> str:
    """Compose the system prompt = persona + TOOL USE block.

    The TOOL USE block is verbosity-and-backend-aware — it instructs the
    LLM to emit a brief preamble before each tool call (the new "filler"
    primitive), with prosody guidance per backend. Verbosity is per-user
    and passed in by the caller.
    """
    base = _SYSTEM_PROMPT_ENV_OVERRIDE or skill.system_prompt
    plan = plan_block(verbosity)
    # Sesame CSM finding: session-open memory callbacks boost "presence";
    # mid-turn recall reads as creepy. If we have a saved summary from the
    # previous session with this skill, inject one nudge and let the LLM
    # decide whether to reference it naturally.
    recall = _recall_block(user_id, skill.slug)
    return (
        base
        + "\n\n"
        + tool_use_block(verbosity, tts_backend)
        + "\n\n"
        + tool_response_block(verbosity)
        + (("\n\n" + plan) if plan else "")
        + "\n\n"
        + repair_block()
        + (("\n\n" + recall) if recall else "")
    )


def _recall_block(user_id: str, skill_slug: str) -> str:
    summary = load_last_summary(user_id, skill_slug)
    if not summary:
        return ""
    return f"""\
## MEMORY — from the last session

Last time the user and this persona spoke, it went roughly: {summary}

IF it fits naturally, acknowledge this in your first turn of the new
session — e.g. "hey, last time we were working through X, any update?"
Only if the user's first message lines up with the topic. Otherwise
IGNORE this block completely; do not force a callback.
"""


# ---------------------------------------------------------------------------
# Text-only agent — used by inbound A2A (no voice, no tools, one-shot).
# Keeps dependence on the pipeline decoupled so callers can hit /a2a even
# when no WebRTC session is active.
# ---------------------------------------------------------------------------

from openai import AsyncOpenAI

_text_client: AsyncOpenAI | None = None
_A2A_CONTEXTS: dict[str, list[dict]] = {}
_A2A_MAX_TURNS = int(os.environ.get("A2A_MAX_TURNS", "10"))


def _get_text_client() -> AsyncOpenAI:
    global _text_client
    if _text_client is None:
        _text_client = AsyncOpenAI(api_key=LLM_API_KEY, base_url=LLM_URL)
    return _text_client


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
    client = _get_text_client()

    reply = ""
    for _ in range(max(1, _TEXT_REACT_MAX_ITERATIONS)):
        kwargs: dict = {
            "model": LLM_SERVED_NAME,
            "messages": messages,
            "max_tokens": skill.max_tokens,
            "temperature": skill.temperature,
            "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
        }
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

    # Per-skill LLM routing. When skill.llm.{url,model,api_key_env} is set,
    # this session's chat completions go to that endpoint instead of the
    # env default (typically the local vLLM). Lets a single skill route to
    # a LiteLLM gateway, OpenAI, Anthropic, etc. — without affecting other
    # skills running in the same process.
    skill_llm = skill.llm or {}
    using_custom_llm = bool(skill_llm.get("url"))
    llm_url = str(skill_llm.get("url") or LLM_URL)
    llm_model = str(skill_llm.get("model") or LLM_SERVED_NAME)
    llm_api_key = (
        os.environ.get(str(skill_llm["api_key_env"]), LLM_API_KEY)
        if skill_llm.get("api_key_env")
        else LLM_API_KEY
    )
    # vLLM-specific extras. Only inject when hitting the default (local)
    # endpoint; non-vLLM gateways (LiteLLM, OpenAI, Anthropic) either
    # ignore or reject `chat_template_kwargs`. Skills can override via
    # skill.llm.extra_body.
    if "extra_body" in skill_llm:
        extra_body = skill_llm["extra_body"] or None
    elif using_custom_llm:
        extra_body = None
    else:
        extra_body = {"chat_template_kwargs": {"enable_thinking": False}}

    settings_kwargs: dict = {
        "model": llm_model,
        "temperature": skill.temperature if skill else LLM_TEMPERATURE,
        "max_tokens": skill.max_tokens if skill else LLM_MAX_TOKENS,
    }
    if extra_body is not None:
        settings_kwargs["extra"] = {"extra_body": extra_body}

    llm = OpenAILLMService(
        api_key=llm_api_key,
        base_url=llm_url,
        settings=OpenAILLMService.Settings(**settings_kwargs),
    )
    # vLLM rejects `role: developer` (used for async-tool result injection);
    # OpenAI-compatible gateways generally accept it. Only strip when we
    # know we're hitting the default local endpoint.
    if not using_custom_llm:
        llm.supports_developer_role = False

    # Per-skill delegate filter. Empty list / None = all delegates exposed.
    session_delegates = _DELEGATES.filtered(skill.delegates if skill else None)
    tts_kwargs: dict = {"backend": tts_backend}
    if skill.voice:
        if tts_backend == "kokoro":
            tts_kwargs["voice"] = skill.voice
            if skill.lang:
                tts_kwargs["lang"] = skill.lang
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
    _summary_config = LLMAutoContextSummarizationConfig(
        max_context_tokens=_summary_max_tokens,
        max_unsummarized_messages=_summary_max_messages,
        summary_config=LLMContextSummaryConfig(target_context_tokens=_summary_target_tokens),
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
        # The latest summary is the first system message in the context
        # that isn't the persona's SOUL prompt. Pipecat's summarizer
        # inserts / updates it in-place.
        for msg in context.messages:
            if msg.get("role") != "system":
                continue
            content = msg.get("content") or ""
            if isinstance(content, str) and content and content != skill.system_prompt:
                save_summary(user_id, skill.slug, content)
                return

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
        # within ~500 ms of UserStoppedSpeaking, emit a quiet "mm" / "hm"
        # so the agent feels responsive on slow turns. Cancels when the
        # bot actually starts speaking. Vapi Fill Injection pattern.
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
        stashed = drain_stashed_deliveries(user_id, skill.slug)
        if stashed:
            logger.info(f"[replay] replaying {len(stashed)} stashed delivery(ies)")
            await delivery.replay_stashed(stashed)

    @transport.event_handler("on_client_disconnected")
    async def _on_disconnect(_t, _c):
        logger.info("client disconnected")
        # Persist anything still pending so the next session can replay.
        snapshot = delivery.snapshot_pending()
        for item in snapshot:
            stash_delivery(user_id, skill.slug, item)
        state = user_state_for(user_id)
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
    # Fish's ~2min cold compile; we just begin work in the background.
    asyncio.get_running_loop().run_in_executor(None, prewarm_all)
    try:
        yield
    finally:
        await _handler.close()
        lifecycle.stop()


app = FastAPI(title="protoVoice", lifespan=lifespan)


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
        "user_count": len(user_registry.all()) if not user_registry.single_user_mode() else 0,
        "active_sessions": len(active_user_states()),
        "delegates": [
            {"name": d.name, "type": d.type} for d in _DELEGATES.all()
        ],
        "skills": list(_SKILLS.keys()),
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
    """Returns the caller's resolved user id + display name + role +
    pinned persona/viz. Clients use this to confirm their API key is
    valid, show the user their name, and adapt the drawer UI based on
    role (admins see the skill selector + orb-settings; pinned regular
    users don't)."""
    return {
        "id": user.id,
        "display_name": user.display_name,
        "role": user.role,
        "allowed_skills": (
            list(user.allowed_skills) if user.allowed_skills is not None else None
        ),
        "pinned_viz": user.pinned_viz,
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


@app.get("/api/skills")
async def get_skills(user: User = Depends(require_user)):
    # For non-admins with allowed_skills set, filter the catalog so they
    # only see what they can activate. Admins + unconstrained users see
    # the full list. ``locked`` is true when there's exactly one choice —
    # clients use it to render a read-only chip instead of a dropdown.
    active = _resolved_skill_slug(user)
    visible = [
        s for s in _SKILLS.values()
        if user.is_admin or user.allowed_skills is None
        or s.slug in user.allowed_skills
    ]
    locked = (
        not user.is_admin
        and user.allowed_skills is not None
        and len(user.allowed_skills) == 1
    )
    return {
        "active": active,
        "locked": locked,
        "skills": [
            {
                "slug": s.slug,
                "name": s.name,
                "description": s.description,
                # Optional dedicated orb viz. Clients apply it on skill switch.
                "viz": dict(s.viz) if s.viz else {},
            }
            for s in visible
        ],
    }


@app.post("/api/skills")
async def set_skill(body: dict, user: User = Depends(require_user)):
    slug = (body.get("slug") or "").strip()
    if slug not in _SKILLS:
        return {"error": f"unknown skill: {slug}", "available": list(_SKILLS.keys())}
    # Non-admins must stay within their allowed_skills list. Admins are
    # never constrained.
    if not user.allows_skill(slug):
        raise HTTPException(
            status_code=403,
            detail=(
                f"skill '{slug}' not in allowed_skills for user '{user.id}'"
            ),
        )
    user_state_for(user.id).skill_slug = slug
    return {"active": slug}


@app.post("/api/admin/skills")
async def admin_set_skill(body: dict, admin: User = Depends(require_admin)):
    """Admin-only — set any user's active skill, bypassing pin.

    Body: { user_id: <str>, slug: <str> }. Uses the target user's
    mutable skill_slug field; does not modify allowed_skills (to do that,
    edit the user roster). Applies on the user's next connection —
    active sessions keep the skill they snapshotted at connect.
    """
    target_id = (body.get("user_id") or "").strip()
    slug = (body.get("slug") or "").strip()
    if not target_id:
        return {"error": "user_id is required"}
    if slug not in _SKILLS:
        return {"error": f"unknown skill: {slug}", "available": list(_SKILLS.keys())}
    if not user_registry.single_user_mode() and user_registry.by_id(target_id) is None:
        return {"error": f"unknown user: {target_id}"}
    user_state_for(target_id).skill_slug = slug
    return {"ok": True, "user_id": target_id, "active": slug}


@app.post("/api/skills/reload")
async def reload_skills_endpoint(user: User = Depends(require_user)):
    """Re-read every config/skills/*.yaml + SOUL.md from disk.

    Safe to call at any time. Active sessions already snapshotted their
    skill at connect; they keep their current snapshot until the next
    (re)connect. `/healthz` + GET `/api/skills` reflect the new set
    immediately.
    """
    global _SKILLS
    _SKILLS = load_skills(CONFIG_DIR)
    return {
        "ok": True,
        "skills": list(_SKILLS.keys()),
        "active": user_state_for(user.id).skill_slug or DEFAULT_SOUL_SLUG,
    }


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
    Skills with `delegates:` filters re-apply the filter at next session
    start (filter is captured per run_bot). Returns the post-reload
    delegate name list.
    """
    names = _DELEGATES.reload()
    return {"ok": True, "delegates": names}


# ---------------------------------------------------------------------------
# Voice cloning — upload a reference clip, optionally auto-transcribe, save
# on the Fish server, and stamp a new skill so it shows in the dropdown.
# ---------------------------------------------------------------------------

import re as _re
from fastapi import File, Form, UploadFile

_SLUG_RE = _re.compile(r"^[a-z0-9][a-z0-9\-_]{1,63}$")


@app.get("/api/voice/references")
async def voice_references(user: User = Depends(require_user)):
    """List the Fish server's saved voice references."""
    if TTS_BACKEND != "fish":
        return {"backend": TTS_BACKEND, "references": []}
    return {"backend": "fish", "references": fish_list_references()}


@app.post("/api/voice/clone")
async def voice_clone(
    audio: UploadFile = File(...),
    slug: str = Form(...),
    name: str | None = Form(None),
    transcript: str | None = Form(None),
    description: str = Form(""),
    user: User = Depends(require_user),
):
    """Upload a reference clip, optionally auto-transcribe, save on Fish,
    and create a new skill that uses it."""
    global _SKILLS
    if TTS_BACKEND != "fish":
        return {
            "error": (
                f"voice cloning requires TTS_BACKEND=fish (currently {TTS_BACKEND!r}). "
                "OpenAI/Kokoro backends use preset voices only."
            )
        }
    slug = slug.strip().lower()
    if not _SLUG_RE.match(slug):
        return {"error": "slug must be lowercase letters/numbers/hyphens (2-64 chars)"}
    if slug in _SKILLS:
        return {"error": f"slug '{slug}' already exists"}

    audio_bytes = await audio.read()
    if not audio_bytes:
        return {"error": "empty audio upload"}

    # Auto-transcribe if the user didn't provide one.
    final_transcript = (transcript or "").strip()
    auto_transcribed = False
    if not final_transcript:
        try:
            final_transcript = await asyncio.to_thread(transcribe_bytes, audio_bytes)
            auto_transcribed = True
        except Exception as e:
            logger.exception("[voice/clone] whisper transcription failed")
            return {"error": f"auto-transcribe failed: {e}"}
        if not final_transcript:
            return {"error": "auto-transcribe produced empty text — provide a transcript manually"}

    # Save on Fish using the slug as the reference id. Fish's regex requires
    # `^[a-zA-Z0-9\-_ ]+$` which our stricter slug_re already satisfies.
    ok = await asyncio.to_thread(fish_add_reference, slug, audio_bytes, final_transcript)
    if not ok:
        return {"error": "Fish server rejected the reference — check the sidecar logs"}

    # Stamp a skill YAML and hot-reload the skills dict.
    display_name = (name or slug.replace("-", " ").title()).strip()
    write_voice_clone_skill(
        slug=slug,
        name=display_name,
        reference_id=slug,
        description=description.strip(),
        config_dir=CONFIG_DIR,
    )
    _SKILLS = load_skills(CONFIG_DIR)
    _METRICS["clone_requests_total"] += 1
    return {
        "ok": True,
        "slug": slug,
        "name": display_name,
        "transcript": final_transcript,
        "auto_transcribed": auto_transcribed,
    }


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
    skill_slug_provider=lambda: (
        user_state_for(_A2A_USER_ID).skill_slug or DEFAULT_SOUL_SLUG
    ),
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
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()

    def _shutdown(_sig, _frame):
        logger.info("Shutting down")
        lifecycle.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
