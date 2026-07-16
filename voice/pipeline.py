"""ORBIS voice pipeline — the Pipecat graph assembly + run loop (run_bot).

Extracted verbatim from app.py (#app.py-decomposition, Phase 2): the run_bot
body is byte-identical to its former home; only the import wiring changed.
Library deps import from their origin module; app-level helpers/state that stay
in app.py import via `from app import`. Load-bearing voice tripwires live here
(append_to_context=False on out-of-band TTS, cancel_on_interruption defaults,
native-mode gating) — see STATUS.md "Known tripwires" before touching them.
"""

from __future__ import annotations

import asyncio
import os
import random
import time

from agent import presence, tracing as _tracing
from agent.audio_tags import make_audio_tags_tap
from agent.backchannel import BackchannelController
from agent.bargein import BargeInGate
from agent.delivery import DeliveryController
from agent.echo_guard import EchoGuardObserver, EchoGuardSuppressor
from agent.filler import Latency, Verbosity
from agent.llm_error_announcer import LLMErrorAnnouncer
from agent.micro_ack import MicroAckInjector, opening_ack_line
from agent.orchestrate import run_orchestration
from agent.prosody import ProsodyTagStripper
from agent.spoken_logger import SpokenTextLogger
from agent.session_store import drain_stashed_deliveries, save_summary, stash_delivery
from agent.stall_watchdog import StallWatchdog
from agent.tools import ASYNC_TOOL_NAMES, latency_for, register_tools
from agent.user_state import user_state_for
from auth.context import current_session_id, current_user_id
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import LLMRunFrame, TTSSpeakFrame
from pipecat.pipeline.llm_switcher import LLMSwitcher
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.service_switcher import ServiceSwitcherStrategyFailover
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMAssistantAggregatorParams, LLMContextAggregatorPair, LLMUserAggregatorParams, UserTurnCompletionConfig
from pipecat.processors.frameworks.rtvi import RTVIProcessor
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.utils.context.llm_context_summarization import LLMAutoContextSummarizationConfig, LLMContextSummaryConfig
from voice.ask_gate import AskGate
from voice.cancel_gate import CancelGate
from voice.llm import make_llm
from voice.local_transport import LocalAudioTransport
from voice.native_bargein import NativeBargeInObserver
from voice.sse_bus import sse_bus
from voice.stt import STT_BACKEND, make_stt
from voice.tts import TTS_BACKEND, make_tts

from app import (
    LLM_MAX_TOKENS,
    LLM_TEMPERATURE,
    SMART_TURN,
    SseBusObserver,
    _DELEGATES,
    _ECHO_STATE,
    _METRICS,
    _active_skill,
    _build_speaker_gate,
    _build_summary_tags,
    _build_user_turn_strategies,
    _effective_prompt,
    _extract_summary_text,
    _filler_gen_for,
    _get_text_client,
    _reconfigure_live_llm,
    _resolve_behavior_block,
    _resolve_fallback_llm,
    _resolve_skill_llm,
    _switch_live_voice,
    get_memory,
    logger,
)


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
    llm = make_llm(
        base_url=llm_url,
        model=llm_model,
        api_key=llm_api_key,
        settings=OpenAILLMService.Settings(**settings_kwargs),
        provider=llm_cfg["provider"],
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
    # LLM error announcer (#576): a dead/401'd LLM otherwise leaves the orb
    # in "thinking" forever — the ErrorFrame flows upstream where the (long
    # since disarmed) StallWatchdog never looks. Must be an observer, not a
    # processor: LLMSwitcher re-propagates the ErrorFrame upstream even on a
    # successful failover, so anything positional would announce recovered
    # outages. Constructed here (before the switcher) so the failover event
    # handler below can reclassify a pending announcement; registered in the
    # task's observers list; emitter (task.queue_frame) wired after task
    # creation.
    llm_error_announcer = LLMErrorAnnouncer(
        debounce_secs=float(os.environ.get("LLM_ERROR_DEBOUNCE_SECS", "2.5")),
        throttle_secs=float(os.environ.get("LLM_ERROR_THROTTLE_SECS", "20")),
        enabled=os.environ.get("LLM_ERROR_ANNOUNCER", "1") == "1",
        tts_backend=tts_backend,
    )

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
    # own tail. Default both off unless explicitly opted in — either the
    # persona behavior block, or the BACKCHANNEL / MICRO_ACK env flags
    # (the runtime .env tuning loop). Speaker-mode users stay off; a
    # headphone / real-AEC setup can opt in until Phase 2 (AEC via
    # AVAudioEngine) lets us flip the default back to on.
    if behavior.get("backchannel") is None:
        bc_cfg["enabled"] = os.environ.get("BACKCHANNEL", "0").lower() in ("1", "true", "on")
    if behavior.get("micro_ack") is None:
        ma_cfg["enabled"] = os.environ.get("MICRO_ACK", "0").lower() in ("1", "true", "on")

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
    # Built whenever a DeliveryController exists (not gated on the boot-time
    # delegate count) so orchestrate is available after a hot-swap adds the
    # first delegate. Resolves the registry LIVE per run so it sees current
    # delegates even when the session booted with none.
    _orch_runner = None
    if delivery is not None:
        _orch_client = _get_text_client(llm_cfg["url"], llm_cfg["api_key"])

        async def _orch_runner(goal: str, *, progress=None, ask_user=None) -> str:
            return await run_orchestration(
                goal,
                delegates=_DELEGATES.filtered(skill.delegates if skill else None),
                client=_orch_client,
                model=llm_cfg["model"],
                extra_body=llm_cfg["extra_body"],
                max_tokens=skill.max_tokens,
                temperature=skill.temperature,
                progress=progress,
                ask_user=ask_user,
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

    # Hot-swap: re-render the delegate roster into THIS live session when the
    # registry changes (a delegate added/removed in Settings), so it takes
    # effect on the next turn without a restart. register_function overwrites by
    # name (idempotent), so re-running register_tools just refreshes the handler
    # + schema; we then push the new schema + fleet-block prompt onto the
    # running context. Stored on user_state; called by the /api/delegates
    # endpoints. Mutating context.tools / messages[0] is picked up next turn.
    def _refresh_delegates() -> None:
        fresh = _DELEGATES.filtered(skill.delegates if skill else None)
        new_schema = None
        for m in _llm_members:
            try:
                s = register_tools(
                    m,
                    on_finish=_cancel_progress,
                    delivery=delivery,
                    delegates=fresh,
                    push_notification_url=_push_url,
                    push_notification_token=_push_token,
                    orchestrate_runner=_orch_runner,
                )
                if new_schema is None:
                    new_schema = s
            except Exception as e:  # noqa: BLE001 — never break the live session
                logger.warning(f"[delegates/hot] re-register failed: {e}")
                return
        if new_schema is not None:
            context.set_tools(new_schema)
            context.messages[0]["content"] = _effective_prompt(
                skill, tts_backend,
                verbosity=user_state.filler_settings.verbosity,
                user_id=user_id, delegates=fresh, tools_schema=new_schema,
            )
            logger.info("[delegates/hot] live session refreshed: %s", fresh.names())

    user_state.refresh_delegates = _refresh_delegates

    # Hot-swap: apply a persona switch (epic #611 P2) to THIS live session.
    # Rebinds the closure `skill` — every later read (prompt re-render via
    # _refresh_delegates, tool handlers, watchers) sees the new persona —
    # then retargets the live LLM + TTS voice and drops the cached filler
    # generator so the micro tier follows the persona (known tripwire).
    # Called by /api/personas endpoints via _hot_refresh_persona_sessions.
    def _refresh_persona() -> dict:
        nonlocal skill
        old = skill
        new = _active_skill(user_id)
        skill = new
        notes: list[str] = []
        if new.filler_verbosity:
            try:
                user_state.filler_settings.verbosity = Verbosity(new.filler_verbosity)
            except ValueError:
                pass
        # Prompt + tool schema re-render (reads the rebound `skill`).
        _refresh_delegates()
        # LLM endpoint/model — resolve first so api_key_env and env
        # defaults land the same way they do at pipeline build.
        try:
            res = _reconfigure_live_llm(_resolve_skill_llm(new), user_id=user_id)
            if res.get("needs_restart"):
                notes.append(str(res.get("error")))
        except Exception as e:  # noqa: BLE001 — never break the live session
            logger.warning(f"[personas/hot] llm reconfigure failed: {e}")
        # Voice — live swap within the running TTS backend; an engine
        # change is a pipeline-topology change (binds-once audio socket,
        # #486) and honestly needs a restart.
        new_backend = (new.tts_backend or TTS_BACKEND).lower()
        if new_backend != tts_backend:
            notes.append(
                f"voice engine {tts_backend} → {new_backend} applies on restart"
            )
        elif new.voice and new.voice != old.voice:
            res = _switch_live_voice(new.voice, user_id=user_id)
            if not res.get("ok"):
                notes.append(str(res.get("error") or "voice switch failed"))
        if (new.temperature, new.max_tokens) != (old.temperature, old.max_tokens):
            notes.append("temperature / max tokens apply on restart")
        # Filler micro tier — rebuilt lazily against the new persona.
        user_state.filler_generator = None
        logger.info(
            f"[personas/hot] live session → {new.slug!r} "
            f"voice={new.voice!r} notes={notes}"
        )
        return {"ok": True, "notes": notes}

    user_state.refresh_persona = _refresh_persona

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

        # The switcher does NOT retry the failed generation — it only routes
        # subsequent turns. Budgeted so an all-dead member list can't
        # ping-pong retries forever: members-1 retries per incident window.
        _failover_retry = {"used": 0, "window_start": float("-inf")}

        @pipeline_llm.strategy.event_handler("on_service_switched")
        async def _on_llm_failover(_strategy, service):
            # Surface failover so a dropped primary is visible, not silent.
            idx = _llm_members.index(service) if service in _llm_members else -1
            role = "primary" if idx == 0 else f"backup#{idx}"
            logger.warning("[llm] failover → now using %s (%s)", role, service.name)
            _METRICS["llm_failovers_total"] = _METRICS.get("llm_failovers_total", 0) + 1
            # Reclassify the announcer's pending line: if the retry below
            # can't run (budget spent), "ask me again" beats "check
            # settings"; a second failover in the same window means the
            # backup died too, and the announcer falls back to the error
            # class (#576).
            llm_error_announcer.note_failover()
            # Retry the failed generation on the new active member so the
            # user's question is answered without re-asking: LLMRunFrame
            # re-pushes the aggregated context — which still holds the
            # unanswered turn — through the switcher to the backup. The
            # retry's own output cancels the announcer's debounce, so a
            # successful failover is completely silent (live-soak 07-11).
            now = time.monotonic()
            if now - _failover_retry["window_start"] > 15.0:
                _failover_retry.update(used=0, window_start=now)
            if _failover_retry["used"] < len(_llm_members) - 1:
                _failover_retry["used"] += 1
                logger.warning("[llm] failover retry — re-running the turn on %s", role)
                await task.queue_frame(LLMRunFrame())
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
        # CancelGate — a bare "cancel" / "never mind" / "stop listening"
        # swallows the turn and closes the listening window (CTRL_STOP_LISTENING
        # → Rust mutes; wake mode re-arms). Normal turns pass through.
        CancelGate(transport),
        # AskGate — if a background orchestration run is paused on ask_user,
        # the next user transcript answers it (and is swallowed) instead of
        # starting a fresh turn. No-op when nothing's waiting.
        AskGate(),
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
        # Observability chokepoint — logs every utterance headed into TTS
        # (streamed LLM narration + out-of-band fillers / opening acks /
        # DeliveryController / stall recovery) so no speech path is unlogged.
        # Must sit immediately BEFORE `tts` to see all synthesis input.
        SpokenTextLogger(),
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
            llm_error_announcer,
            *_native_observers,
        ],
    )

    # Wire the delivery + backchannel controllers' out-of-band emit paths
    # now that the task exists. queue_frame is the only safe way to inject
    # frames from a foreign coroutine.
    delivery.set_emitter(task.queue_frame)
    # The announcer's canned line goes through TTS only — no LLM round-trip,
    # since the LLM is exactly what's broken.
    llm_error_announcer.set_emitter(task.queue_frame)
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
        """Sparse 'still working' cadence for any non-fast tool in flight: first
        line at ~progress_first_secs, then one every ~progress_interval_secs until
        the tool finishes — so a slow async delegate never dead-airs past one
        interval. (A delegate's note_progress is VISUAL-only — the StatusPill —
        so without a spoken loop it's silent from the opening ack to the answer:
        the "where'd you go?" gap. See agent/presence.py.) Each line grounds in
        the delegate's latest streamed status when present. Cancelled on tool
        completion or barge-in via `_cancel_progress`."""
        try:
            _fs = user_state.filler_settings
            _fg = _filler_gen_for(user_id)
            await asyncio.sleep(_fs.progress_first_secs)
            tick = 0
            while True:
                # During a HITL ask_user pause (orchestrate parked on the user's
                # answer) the tool is still "in flight" but the agent is waiting on
                # the USER, not working — so a "still working" line would be wrong.
                # Stay quiet this tick and re-check next interval.
                if user_state.has_pending_ask_on_active():
                    await asyncio.sleep(_fs.progress_interval_secs)
                    continue
                with _tracing.span(
                    "filler.progress",
                    input={"tool": tool_name, "tick": tick},
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
                tick += 1
                await asyncio.sleep(_fs.progress_interval_secs)
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
                # Queue the opening ack SYNCHRONOUSLY (canned + instant) so it's
                # in the TTS queue BEFORE the tool runs — hence before the
                # result. The old fire-and-forget create_task could await the
                # micro-LLM (now on the gateway → network latency) and lose the
                # race, so the user heard the answer, THEN a pointless "on it".
                # Variety still comes from the canned pool here + the slow-tool
                # progress loop below. orbis: ack-leads.
                _ack = opening_ack_line(tts_backend, exclude=_opening_ack["last"])
                _opening_ack["last"] = _ack
                logger.info(f"[filler:opening] {_ack!r}")
                await task.queue_frame(TTSSpeakFrame(_ack, append_to_context=False))

            # Any NON-FAST tool gets the spoken presence loop — crucially
            # INCLUDING async delegates. A delegate's note_progress is VISUAL-only
            # (the StatusPill rail; DeliveryController.note_progress does not
            # speak), so the old `tier is SLOW and not any_async` gate left a slow
            # delegate silent from the opening ack to the answer — the "where'd
            # you go?" dead air. The loop grounds each line in that visual status
            # when present. See agent/presence.py + evals/presence.py.
            if presence.should_run_presence_loop(tier):
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
            # Invalidate any in-flight async delegate/orchestrate result so an
            # answer the user just talked over isn't narrated out of context
            # (delegate_dispatch can't be cancelled, but its result is dropped).
            delivery.bump_barge()
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
        state.active_llm = llm  # for runtime model swap via POST /api/config
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
        if state.active_llm is llm:
            state.active_llm = None
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

