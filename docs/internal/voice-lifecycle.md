# Voice round-trip lifecycle

End-to-end audit of how an utterance flows from the user's mic, through the
Pipecat pipeline, to the LLM, back through TTS, and into the user's speakers.
Companion file `voice-lifecycle-risks.md` lists the latent bugs and gaps
surfaced during this audit (most now resolved — see the "Resolved" section
at the top of that file). Companion file `voice-lifecycle-research.md`
maps companion-stack experiments onto the pipe slots below.

Snapshot: 2026-04-26, main at v0.1.32. Anchor file is `app.py`; the
pipeline is built in `run_bot()`.

> 2026-05-29 note: this document is historical for the WebRTC/CPAL
> audit. The current Mac production path is native desktop audio with
> Rust-owned microphone permission, AVAudioEngine voice-processing
> input, CPAL output, and the Python sidecar over the Unix socket. See
> `STATUS.md`, `docs/internal/desktop-dev.md`, and
> `scripts/validate-macos-native-audio.sh` for the current Mac release
> validation path.

## Pipeline spine

```
transport.input()                              LocalAudioTransport over Unix socket
  -> EchoGuardSuppressor(_ECHO_STATE)          drops InputAudioRawFrame during/after bot TTS
  -> SpeakerGate                               #35 PR 1 — owner-vs-stranger via cosine cmp; default owner-trust
  -> RTVIProcessor                             inbound client->server data-channel msgs
  -> stt                                       LocalWhisperSTT (default) | OpenAISTTService | SenseVoiceSTT (#66)
  -> AudioTagsTap                              #66 — per-turn mood writes + [audio] system msg injection
  -> user_agg                                  SileroVADAnalyzer + optional SmartTurn v3
  -> BargeInGate                               swallows VAD spikes that resolve in <350ms
  -> MicroAckInjector                          "mm/hm" ~1.5s after UserStoppedSpeaking
  -> BackchannelController                     "mm-hmm" every ~6s during user utterance
  -> DeliveryController                        out-of-band push messages, bid-then-drain
  -> llm                                       OpenAILLMService | OllamaLLMService | MLXLLMService
  -> tts                                       Kokoro | OpenAI | ElevenLabs | Fish
  -> transport.output()                        emits TTSAudioRawFrame + Bot{Started,Stopped}SpeakingFrame
  -> ProsodyTagStripper                        strips [softly]/[pause:N] from TextFrame for context
  -> assistant_agg                             LLMContextAggregator + auto context summarization
```

**SpeakerGate** placement (#35 PR 1) — between `EchoGuardSuppressor` and
`RTVIProcessor`. Echo-guarded audio is the right input (bot-self bleed
removed). Emits `OwnerVerifiedFrame` / `StrangerDetectedFrame` alongside
the audio (originals always pass through). Default owner-trust until
enrollment lands a voiceprint via the wizard.

**Observers** (top-level, watch every frame, never transform):
- `EchoGuardObserver(_ECHO_STATE)` — flips bot-speaking state from frames produced at `transport.output`.
- Langfuse `turn_tracer` from `_tracing.make_turn_tracer(session_id, user_id)` (`app.py:854-857`).
- `rtvi.create_rtvi_observer(RTVIObserverParams())` — emits structured client events over the data channel.

**Out-of-band emitters** wired post-construction:
- `delivery.set_emitter(task.queue_frame)` (`app.py:877`)
- `backchannel.set_emitter(task.queue_frame)` (`app.py:878`)
- Slow-tool progress loop at `app.py:929-931`

All inject `TTSSpeakFrame(phrase, append_to_context=False)` so they never
pollute LLM history.

---

## Stage 0 — Session bootstrap

1. User double-clicks the orb canvas (`OrbStage.tsx:42-58`); `client.connect()` fires.
2. Pipecat's `SmallWebRTCTransport` POSTs SDP to `/api/offer` with `X-API-Key` from localStorage `orbis.apiKey` (`web/src/voice/client.ts:24-31`, `web/src/auth/apiKey.ts:15-21`).
3. `app.py:1148-1161`: `require_user` resolves user_id from header → on-connect closure schedules `run_bot(conn, user_id=user_id)` as a `BackgroundTask`.
4. `current_user_id.set(user_id)` (`app.py:537`) — context var seen by tracing/session/filler stack.
5. `on_client_connected` (`app.py:958-979`): scope tracer + delivery + session_id; `drain_stashed_deliveries(user_id)` re-feeds anything stashed from a prior disconnect; if non-empty, `delivery.replay_stashed(stashed)` re-enqueues into the new pipeline.
6. `prewarm_all()` ran once at FastAPI startup (`app.py:1077-1093`) — Whisper, TTS model, vLLM probe, Kokoro phoneme cache.

## Stage 1 — Mic acquisition

Production Mac desktop builds use Tauri IPC + native audio, not WebRTC
`getUserMedia`.

- `src-tauri/src/mic_permission.m` requests `AVMediaTypeAudio` through
  `AVCaptureDevice`, reports authorization status, and opens System Settings →
  Microphone when the user needs to grant access.
- `src-tauri/src/lib.rs` gates sidecar startup on authorized microphone access
  before starting the native audio engine.
- `src-tauri/src/audio/voice_processing_input.rs` starts AVAudioEngine
  voice-processing input for AEC + AGC + noise suppression, then sends 20 ms
  16 kHz PCM frames over the local Unix socket.
- `voice/local_transport.py` reads `ORBIS_AUDIO_INPUT_MODE=voice_processing`
  and defaults `MIC_GAIN` to 1.0 for the Mac path. Legacy CPAL builds still use
  the old defensive gain unless explicitly overridden.

The historical WebRTC setup wizard mic test remains useful reference material
for browser-era behavior, but it is not the production Mac audio path.

### Voiceprint enrollment (optional, #35 PR 1.3)

Wizard step `enroll` between `mic` and `done`. Captures ~10s of owner audio via `web/src/shared/audio/recordWav.ts` (raw PCM through `ScriptProcessorNode`, downsampled to 16kHz, RIFF WAV blob — the backend's libsndfile decoder handles WAV but not WebM/Opus, so we encode client-side rather than add an ffmpeg dep).

- `POST /api/voiceprint/enroll` accepts WAV bytes, decodes via soundfile, downmixes stereo, validates duration (3s min, 30s cap with silent truncation), encodes via `ECAPAEmbedder` from `agent/ecapa_embedder.py`, atomic-saves to `get_voiceprint_path()` (default `<data_dir>/voiceprint.npy`).
- `GET /api/voiceprint/status` — `{enrolled, path, embedder_available}`. Wizard branches its UX on `embedder_available` — if speechbrain isn't installed (no `[speaker-id]` extra) it shows an "install to enable" panel instead of the recording flow.
- `DELETE /api/voiceprint` — idempotent removal for re-enroll.
- Skippable; gate stays in owner-trust mode without a voiceprint. Single-owner deployments without the `[speaker-id]` extra continue to work — the speaker_gate runs in passthrough mode.

## Stage 2 — Audio inflow + WebRTC

- `SmallWebRTCTransport(TransportParams(audio_in_enabled, audio_out_enabled, audio_out_10ms_chunks=2, audio_in_filter=rnnoise?))` (`app.py:557-567`).
- Video transceiver is intentionally negotiated even though we send no video — omitting it makes aiortc silently drop DTLS/SCTP (`web/src/voice/client.ts:18-21`).
- Optional rnnoise filter when `NOISE_FILTER=rnnoise` (`app.py:252-266`); default off.
- `transport.input()` produces `InputAudioRawFrame` instances. Inbound chunk size is **not** explicitly set; `audio_out_10ms_chunks=2` only sizes outbound packets (20 ms).

## Stage 3 — Echo guard (input side)

`agent/echo_guard.py`. Single shared `_ECHO_STATE` (`app.py:296`) is written by an observer at the top level and read by the suppressor in the pipeline.

- **Observer** (writer): listens for `BotStartedSpeakingFrame` → `bot_speaking=True` and `BotStoppedSpeakingFrame` → `bot_speaking=False, bot_stopped_at=now()` (`echo_guard.py:67-75`). Top-level so it sees frames produced at `transport.output`.
- **Suppressor** (reader, in pipeline): drops `InputAudioRawFrame` if `(HALF_DUPLEX and bot_speaking)` OR `now - bot_stopped_at < ECHO_GUARD_MS` (`echo_guard.py:106-111`). Defaults: `HALF_DUPLEX=0`, `ECHO_GUARD_MS=300`.
- `HALF_DUPLEX=1` disables real-time barge-in entirely; `ECHO_GUARD_MS` catches the post-tail bleed browser AEC misses.
- Only `InputAudioRawFrame` is dropped — control/transcript frames flow through unchanged.

## Stage 3.5 — Speaker verification (#35 PR 1)

`agent/speaker_gate.py`. Sits between `EchoGuardSuppressor` and `RTVIProcessor`. Three operating modes:

| Mode | Trigger | Output |
|---|---|---|
| **Disabled** | `behavior.speaker_gate.enabled=false` | Passthrough — no verification frame |
| **Owner-trust** | No voiceprint OR no `[speaker-id]` extra OR empty buffer OR encode failure | `OwnerVerifiedFrame(score=1.0)` for every utterance — preserves no-auth single-user deployments |
| **Live gate** | Voiceprint + `ECAPAEmbedder` both available | Cosine vs `threshold` (default 0.62) → owner or stranger frame |

Frame contracts (`agent/speaker_gate.py`):
- `OwnerVerifiedFrame(score)` — owner or trust-fallback
- `StrangerDetectedFrame(score, action: StrangerAction)` — `WARN` | `REFUSE` | `DELEGATE_GUEST`
- Original `InputAudioRawFrame` / VAD frames pass through unchanged so STT/VAD see the same audio.

Embedder: `ECAPAEmbedder` (`agent/ecapa_embedder.py`) wraps `speechbrain/spkrec-ecapa-voxceleb` (~6M params, 192-dim, ~50ms CPU / ~5ms GPU). Lazy load + process-wide cache keyed on `(source, device)`. Resamples to 16kHz via soxr, pads short clips to 1s.

Failure modes all route through owner-trust this session: corrupt voiceprint (raises `VoiceprintCorrupted`, logged ERROR, owner-trust this session — won't silently let strangers through after data loss), embedding shape mismatch, embedder exception. `_build_speaker_gate(sg_cfg)` constructs from `persona.behavior.speaker_gate` — defensive `threshold` parsing handles `null` / non-numeric typos with a logged fallback to 0.62 instead of crashing `run_bot`.

Config schema (`config/orbis.yaml`):

```yaml
behavior:
  speaker_gate:
    enabled: true                              # default true when block exists
    voiceprint_path: ~/.../voiceprint.npy      # platform-aware default via get_voiceprint_path()
    threshold: 0.62                            # ECAPA tuning per #35
    stranger_action: warn                      # warn | refuse | delegate_guest
```

## Stage 4 — STT

`voice/stt.py`. Three backends:

- **`STT_BACKEND=local`** (default) → `LocalWhisperSTT` (HF `automatic-speech-recognition` pipeline). Default model `WHISPER_MODEL=openai/whisper-large-v3-turbo`. CUDA→fp16+SDPA, CPU→fp32. MPS is not in the device branch.
- **`STT_BACKEND=openai`** → `OpenAISTTService` against `STT_URL` (default `https://api.openai.com/v1`), `STT_MODEL=whisper-1`. Tested against OpenAI / LocalAI / OpenRouter / vllm-omni.
- **`STT_BACKEND=sensevoice`** (#66) → `SenseVoiceSTT` via `voice/stt_sensevoice.py`. `FunAudioLLM/SenseVoiceSmall` (234M params, ~70ms Blackwell). One forward pass produces transcription + 7-class emotion + audio events. Requires `[sensevoice]` extra (`pip install -e ".[sensevoice]"`).

All backends are **batch / segmented** — Pipecat aggregates audio between VAD start/end markers and hands a WAV blob in. `run_stt(audio: bytes)` decodes via soundfile, downmixes to mono, soxr-resamples to 16 kHz. **No `InterimTranscriptionFrame`** is emitted by any backend. Empty transcription → silent no-op; decode/inference failure → `ErrorFrame`. Wrapped in span `stt.whisper` (Whisper) / `stt.sensevoice` (SenseVoice).

### SenseVoice frame emission

Per utterance, in this exact order:

1. **`EmotionFrame`** (`agent/frames.py`) — `emotion` (one of `EMOTION_LABELS`: neutral/happy/sad/angry/fearful/disgusted/surprised), `confidence` ("medium" until FunASR exposes calibrated logits), `lang` (en/zh/ja/ko/yue), `speaker_verified` (mirrors most-recent `OwnerVerifiedFrame`/`StrangerDetectedFrame` from upstream gate; defaults `True`), `audio_bytes` (raw WAV carried through for downstream re-inference).
2. **`AudioEventFrame`** — sparse, only when non-Speech events were detected (BGM/Laughter/Applause/Cry/Cough/Sneeze/Breath). `Speech` is filtered out as redundant on a transcription.
3. **`TranscriptionFrame`** — clean text; identical contract to the Whisper backend so existing downstream consumers are unaffected.

`parse_sensevoice_output(raw_text)` is a pure function that maps FunASR's UPPERCASE emotion tags to our lowercase taxonomy and silently drops unknown tags so future FunASR versions don't crash the parse.

**Security**: `trust_remote_code=True` is required to load SenseVoice (FunASR loads custom Python from the model repo at load time). `_TRUSTED_REMOTE_CODE_MODELS` is a curated allow-list (default model + `iic/` mirror); a custom `SENSEVOICE_MODEL` outside the list refuses to load unless the operator opts in via `SENSEVOICE_TRUST_REMOTE_CODE=1`. Without trust, FunASR fails to load — the right failure: surface the RCE risk to the operator instead of silently executing untrusted code.

## Stage 4.5 — Audio-tags tap (#66 Phase 3+4)

`agent/audio_tags.py`. **Wired** between `stt` and `user_agg` (Phase 4). Constructed once per session via `make_audio_tags_tap(mem=get_memory())` in `run_bot`. Subscribes to `EmotionFrame`, `AudioEventFrame`, `OwnerVerifiedFrame`/`StrangerDetectedFrame`, and `TranscriptionFrame`. Two responsibilities:

**1. Per-turn mood writes (R15 fix)** — owner-verified emotion → `mem.personality.drift_mood(*deltas)`. Spec map (#66):

| emotion | Δvalence | Δarousal |
|---|---:|---:|
| `happy` | +0.10 | +0.05 |
| `surprised` | 0.00 | +0.10 |
| `neutral` | 0.00 | 0.00 |
| `sad` | −0.10 | −0.05 |
| `fearful` | −0.05 | +0.10 |
| `angry` | −0.15 | +0.15 |
| `disgusted` | −0.10 | +0.05 |

Stranger audio does NOT nudge the owner's mood — that gating is the load-bearing reason `EmotionFrame.speaker_verified` exists. Failures in `drift_mood` (DB locked, disk full) log loud + don't break the frame loop.

**2. `[audio]` system-message injection** — before each `TranscriptionFrame` the tap pushes an `LLMMessagesAppendFrame` with role=`system`:

```
[audio] emotion=happy lang=en speaker=owner events=Laughter,BGM
```

`run_llm=False` so the annotation alone doesn't fire an LLM run; the `TranscriptionFrame` that follows does. The `audio_context_block` in the persona prompt teaches the LLM what the line means and explicitly forbids parroting it back. The annotation is omitted entirely when no `EmotionFrame` has been seen (e.g. `STT_BACKEND=local` — no SenseVoice → no annotation injection); the tap is safe to leave wired regardless of which STT backend is active.

`AUDIO_TAGS=off` disables (passthrough). Default on. v5 non-emotion heads (SNR / environment / speaking-rate) deferred — the issue's `[audio]` example shows them but the v5 model integration is a separate dep concern.

## Stage 5 — User aggregator (VAD + turn-taking)

`SileroVADAnalyzer()` constructed plain — no threshold overrides — and passed via `LLMUserAggregatorParams(vad_analyzer=…)` (`app.py:752, 770-777`). VAD frames produced: `UserStartedSpeakingFrame` / `UserStoppedSpeakingFrame`.

`_build_user_turn_strategies()` (`app.py:269-291`) is gated on env `SMART_TURN`:
- `off` (default) → `None` → naive VAD endpointing.
- `local` | `v3` → `LocalSmartTurnAnalyzerV3` wrapped in `TurnAnalyzerUserTurnStopStrategy` — distinguishes mid-thought pauses from real turn boundaries.

## Stage 6 — Barge-in gate

`agent/bargein.py`. `grace_ms` default 350, env+persona overridable. State machine in `process_frame`:

- `BotStartedSpeakingFrame` → `_bot_speaking=True`.
- `UserStartedSpeakingFrame` while bot is speaking → **withhold**; stash as `_pending`, kick a `_on_grace_expired` timer, do not forward (`:75-84`).
- `UserStoppedSpeakingFrame` arriving with pending → false positive (cough, "mm-hmm"); swallow both start AND stop, cancel timer, log `[bargein] rejected false positive` (`:87-92`).
- `TranscriptionFrame` arriving with pending → real words; flush the held start, log `transcription confirmed — releasing interrupt` (`:95-100`).
- Timer expires with pending still set → release anyway, log `grace elapsed — releasing interrupt` (`:104-111`).

The gate emits no cancel/stop frames itself — it only chooses whether to release `UserStartedSpeakingFrame`. **Pipecat's own interrupt machinery downstream** flushes the in-flight TTS once the start frame arrives.

**Tool-call cancellation on confirmed barge-in** (`app.py:953-956`): `on_function_calls_cancelled` event handler calls `_cancel_progress()` to kill the SLOW-tool progress narration tasks. Sync tools register with `cancel_on_interruption=True` (auto-cancelled by pipecat); async tools register `False` and survive — their result re-enters via `DeliveryController` later (`agent/tools.py:540-565`).

---

## The five utterance classes the bot can emit

All non-conversational utterances pass `append_to_context=False` so LLM history stays clean.

| # | Utterance | Trigger | Source | Append? |
|---|---|---|---|---|
| 1 | **Backchannel** ("mm-hmm") | DURING user speech, `first_after_secs=5.0` then every `interval_secs=6.0` | `FillerGenerator.backchannel()` → fresh LLM call | False (`backchannel.py:197`) |
| 2 | **Micro-ack** ("mm/hm") | After `UserStoppedSpeaking`, if no agent audio in `trigger_ms=1500` | Hardcoded tuple `_PLAIN_ACKS` / `_FISH_ACKS`; `random.choice` | False (`micro_ack.py:118-119`) |
| 3 | **Inline pre-tool preamble** | LLM emits it itself, in the same response as the tool_call | Main conversational LLM, instructed by `tool_use_block(verbosity, tts_backend)` (`filler.py:200-239`) | True (it's part of the assistant turn) |
| 4 | **Slow-tool progress narration** | `on_function_calls_started` for `Latency.SLOW` sync tool only | `FillerGenerator.progress()` → fresh LLM call; two-tier 2s/6s then silence | False (`app.py:929-931`) |
| 5 | **Push delivery** (a2a result, scheduled, replay) | `delivery.deliver(...)` | Whatever called it (delegate result, A2A push, drain_stashed) | False (`delivery.py:251-264`) |

**Mutual exclusion**:
- Backchannel cancels on `LLMFullResponseStartFrame` (earlier than audio) plus a `COMMIT_GRACE_MS=180` re-check plus an in-flight tag-drop on re-injected frames.
- MicroAck cancels on `BotStartedSpeakingFrame` and re-checks `_bot_speaking` after sleep.
- The progress loop only starts after `on_function_calls_started`, by which time the micro-ack timer is already dead.

**FillerGenerator** (`agent/filler.py:292-411`):
- Hits the routing LLM via `AsyncOpenAI` (`filler.py:303`); when Langfuse env is set, switches to `langfuse.openai` so each call becomes a generation span.
- Uses the same small/fast routing LLM as persona, not the main conversational model.
- `max_tokens=30, temperature=0.9, timeout=2.5s` — no cache, fresh call per emission.
- 6-element `_Recent` deque feeds back as an "AVOID repeating" hint.
- Verbosity tiers: `silent`/`brief`/`narrated`/`chatty`. `progress()` returns None for SILENT and BRIEF; `backchannel()` only for SILENT.
- Fish backend gets the `[softly]/[pause:N]/[hmm]` tag vocabulary in the prompt; non-Fish gets a "plain text only" instruction AND a regex post-strip safety net.

---

## LLM call

### Adapter selection (`voice/llm/__init__.py:73-144`)

Precedence: `mlx://` URL → explicit `provider=` → `_detect_provider` heuristic (port 11434 / hostname `ollama` / `GET /api/version` probe ≤1.5s) → OpenAI fallback.

- **`OllamaLLMService`** subclasses `BaseOpenAILLMService`, overrides `get_chat_completions` to hit `/api/chat` (NOT `/v1/chat/completions`) with `think: False`. Reasoning models (Qwen3, DeepSeek-R1, gemma3) on `/v1/...` emit a separate `reasoning` delta stream that pipecat's sentence aggregator never chunks → TTS waits 6-8s for a sentence break that never comes during reasoning. `/api/chat` honors `think` → first-token latency drops to 100-300ms (`ollama.py:1-44`).
- **`MLXLLMService`** drives `mlx_lm.stream_generate` through a producer thread + `asyncio.Queue`; process-wide model cache `_MODELS` keyed by HF id; applies `enable_thinking=False` via `tokenizer.apply_chat_template`.
- **OpenAI fallback** always flips `svc.supports_developer_role = False` — `role: system` is accepted by every OpenAI-compat endpoint including OpenAI itself, while `developer` is rejected by vLLM and by the protoLabs gateway. There's no upside to `developer`, so it's never sent.
- **`chat_template_kwargs.enable_thinking=False`** is a property of the *endpoint*, resolved by `app.py::_wants_thinking_suppression(url, provider)` off the **resolved URL value** — never off where the URL came from. True for the vLLM/Qwen dialect (protoLabs gateway, `provider: vllm`); False for OpenAI/Anthropic/Groq/… which 400 on unknown body fields. An explicit `persona.llm.extra_body` always wins.

### Provider quirks (`app.py:601-624`)

```python
if "extra_body" in skill_llm:        extra_body = skill_llm["extra_body"] or None
elif using_custom_llm:               extra_body = None
else:                                extra_body = {"chat_template_kwargs": {"enable_thinking": False}}
```

proto-labs.ai's LiteLLM gateway returns 400 on `chat_template_kwargs`/`think`/`enable_thinking`, and even `reasoning_effort: minimal` triggered 403 ("Your request was blocked.") on certain prompt+model combos. So custom URLs ship bare; the bundled vLLM is the only place we inject the field.

### System prompt assembly (`_effective_prompt`, `app.py:330-382`)

`\n\n`-joined in this order:

1. `skill.system_prompt` — persona identity from `config/orbis.yaml`; `SYSTEM_PROMPT` env override applied in persona loader.
2. `tool_use_block(verbosity, tts_backend)` — instructs inline pre-tool preamble (length varies by verbosity).
3. `tool_response_block(verbosity)` — caps spoken-answer length (CHI 2025 18-25-word optimum).
4. `plan_block(verbosity)` — empty at SILENT; otherwise spoken plan preamble for ≥3-step tasks.
5. `repair_block()` — static; acknowledge → reframe → offer pattern.
6. **`audio_context_block()`** (#66 Phase 1) — explains the `[audio]` annotation `AudioTagsTap` injects per-turn (emotion / lang / speaker / events). Tells the LLM how to use the signal AND — load-bearing — never to parrot it back. Static (no verbosity branch). Wired unconditionally; safe-by-default because the block tells the LLM to ignore the line when missing fields.
7. `user_block` — name nudge, only if `skill.user_name` set.
8. `render_personality_block(mem)` (`agent/personality.py:57-106`) — 10 axes, magnitude buckets at `<0.15 neutral / <0.4 slightly / <0.75 plain / strongly`. Mood thresholds `valence>0.15` → bright/low, `arousal>0.15` → energetic/sleepy, `guardedness>0.15` → guarded.
9. `apply_soft_neglect(mem)` (`agent/neglect.py:101-124`) — gap-driven mood targets. `<2d` valence +0.1; `<3d` valence -0.15 + guard 0.15; up to `>8d` capped at valence -0.4 / guardedness 0.7. Computed BEFORE personality block so it's visible from turn one. **Drifts via `drift_mood_toward(step=0.7)` (R15 fix, PR #60)** — composes with per-turn drift from `AudioTagsTap` instead of overwriting accumulated mood.
10. `_recall_block(user_id)` (`app.py:180-225`) — last 3 SQLite session rows via `prior_n(3)`, formatted as `<prior_sessions>` XML, plus `load_last_summary(user_id)` (rolling summary written by pipecat's auto-summarizer on a prior session). Closes with "IF any of this fits naturally, acknowledge it. Otherwise IGNORE this block."

### Mood three-writer pattern (R15 resolution)

`PersonalityDAL` exposes three APIs that compose without overwriting each other:

| API | Caller | Semantic |
|---|---|---|
| `set_mood(*, valence=, arousal=, guardedness=)` | Operator override (drawer UI, tests, boot seed) | Snap-to-value |
| `drift_mood_toward(*, valence=, arousal=, guardedness=, step=0.7)` | Session-open shifts (`apply_soft_neglect`) | Blend `step%` of way toward target |
| `drift_mood(*, valence_delta=, arousal_delta=, guardedness_delta=)` | Per-turn writers (`AudioTagsTap` from #66) | Add delta to current; no-op when all-None |

Without this pattern, neglect's session-open `set_mood` would erase the prior session's per-turn drift.

### Tool registry (`agent/tools.py`)

All decorated tools are `Latency.FAST`, all sync, all return short spoken confirmations:

| Tool | Params | Entitlement-gated? |
|---|---|---|
| `adjust_personality(axis, delta)` | both (req) | no; clamped to `[-0.2, +0.2]`, DAL re-clamps at 0.3 |

Orb visual control (variant, palette, params, presets) is handled outside the LLM tool surface.

Hand-wired (not in `_TOOL_REGISTRY`):
- **`delegate_to`**: per-session schema with `target` enum-restricted to live delegate names (`tools.py:364-393`); description enumerates each delegate's description so the LLM picks. A2A delegates get a `progress_cb` that calls `delivery.speak_now(msg, source=target)` per progress event.

### Delegate dispatch (`agent/delegates.py`)

- **A2A**: `dispatch_message_stream` first (SSE `message/stream`) → falls back to non-streaming on `A2ADispatchError`. `pushNotificationConfig: {url, token}` attached to initial request when `A2A_PUSH_URL` is set; callbacks land on `/a2a/push` (`a2a/server.py:288-376`).
- **OpenAI-compat**: hand-rolled `httpx.post` (NOT the OpenAI SDK — avoids `x-stainless-*` fingerprint headers blocked by certain WAFs). `stream: false` so progress callbacks aren't wired here.

### Function-call lifecycle in stream

- Pipecat's `OpenAILLMService` streams tokens before AND after tool calls. The `tool_use_block` instructs the LLM to emit a one-line preamble *before* the tool call in the same response, so the user hears "checking the weather in Paris" naturally as the model decides to invoke `delegate_to` (`app.py:880-888`: "One LLM, one source of truth, no race conditions.").
- `on_function_calls_started` doesn't block streaming; the progress loop runs as a background `asyncio.Task` in `progress_tasks`.
- `on_function_calls_cancelled` fires on barge-in only for `cancel_on_interruption=True` tools.

### Context summarization (`app.py:738-792`)

Pipecat's built-in `LLMAutoContextSummarizationConfig` wired into `assistant_agg` (NOT a separate node). Defaults: `max_context_tokens=8000`, `max_unsummarized_messages=20`, `target_context_tokens=4000`. On threshold cross, summarizer compresses older history and emits `SummaryAppliedEvent`. `on_summary_applied` handler walks `context.messages` for the first system message whose content is **not** the persona prompt, persists via `save_summary(user_id, content)`.

### Post-session

`on_client_disconnected` (`app.py:981-1049`):

1. `delivery.snapshot_pending()` → stash to `pending.json` per item.
2. Walk `context.messages` for user/assistant turns → `mem.sessions.add(...)` to SQLite.
3. Background task: `analyze_session_drift(turns, …)` runs a bounded LLM call (last 40 turns, ≤800 chars each, `max_tokens=400`) returning JSON `{deltas: [{axis, delta, reason}]}` with `|delta| ∈ [0.01, 0.15]`, ≤3 entries → `apply_drift` updates personality axes (DAL clamps at 0.3).
4. `_tracing.flush()` → `task.cancel()`.

---

## TTS, audio out, delivery

### TTS factory (`voice/tts/__init__.py`)

Default `kokoro`. Each adapter passes `text_filters=[ProsodyTextFilter()]` so the TTS engine never sees `[softly]/[pause:N]` brackets — except Fish, which consumes them natively.

| Backend | Sample rate | Streaming | Notes |
|---|---|---|---|
| **Kokoro** | 24 kHz | Yes (KPipeline generator yields chunks) | `KOKORO_VOICE=af_heart`, `KOKORO_LANG=a`. TTFB measured 294 ms p50, RTF 0.13. Module singleton `_pipe`; prewarm synthesizes "Hello." |
| **OpenAI** | 24 kHz | Pipecat-internal | `TTS_OPENAI_MODEL=tts-1`, `voice=alloy`. Tested against OpenAI/LocalAI/OpenRouter/vllm-omni. |
| **ElevenLabs** | 24 kHz | WebSocket | `eleven_turbo_v2_5`, default Rachel. `prewarm()` is a no-op. |
| **Fish** | 44.1 kHz | Yes (raw int16 PCM, no WAV header despite `format=wav`) | Carry/odd-byte alignment loop required because soxr rejects odd-sized buffers (`fish.py:99-138`). 180s timeout for `torch.compile` cold path. |

### `DeliveryController` (`agent/delivery.py`)

The most-substantial agent module. Manages out-of-band utterances (push messages from async delegates, scheduled, replay-on-reconnect).

- **Two enums classify items, not the controller:**
  - `DeliveryPolicy = NOW | NEXT_SILENCE | WHEN_ASKED`
  - `Priority = CRITICAL | TIME_SENSITIVE | ACTIVE | PASSIVE` (Apple `UNNotificationInterruptionLevel`-shaped)
- **VAD coupling** via `process_frame` — flips `_user_speaking`, kicks `_settle_then_drain` after `_SILENCE_SETTLE_SECS=0.6` post-`UserStoppedSpeakingFrame` so the tail doesn't get stepped on.
- **Bid-then-drain**: when `>=_BID_THRESHOLD=2` `NEXT_SILENCE` items are pending and not high-urgency, controller emits "I've got updates from Alice, Bob and Charlie — want to hear them?" Resolution via substring match against `_BID_NO`/`_BID_YES`.
- **Watchdog** (1s tick, lazy-armed): force-emits stale `NEXT_SILENCE` after `DELIVERY_NEXT_SILENCE_FALLBACK_SECS=10` (mute case); drops `WHEN_ASKED` after `DELIVERY_WHEN_ASKED_TTL_SECS=600`.
- **Backpressure**: `_prune_overflow` keeps top-3 by `(priority, enqueued_at)` past `_MAX_PENDING_AT_DRAIN=3`; TIME_SENSITIVE+ unconditionally retained.
- **Persistence layer** (`agent/session_store.py`): file-backed `pending.json` per user.

### Prosody tags — three-layer defense

`agent/prosody.py`:

1. `FillerGenerator` post-strips tags via regex for non-Fish (`filler.py:408-410`).
2. TTS service `text_filters=[ProsodyTextFilter()]` runs INSIDE the TTS service before synthesis (`kokoro.py:51`, `openai.py:49`, `elevenlabs.py:61`).
3. **`ProsodyTagStripper` placed AFTER `transport.output()`** (`app.py:840-844`) cleans `TextFrame` content before `assistant_agg` records it — so the LLM never sees its own bracket markup re-fed into context history. Regex `\[[a-z][a-z0-9_-]*(?::[^\]]*)?\]` is lowercase-leading so user text like `[Dr. Seuss]` survives.

### Audio out

`transport.output()` consumes `TTSAudioRawFrame(audio, sample_rate, num_channels=1, context_id)` and emits `BotStartedSpeakingFrame`/`BotStoppedSpeakingFrame` (consumed by EchoGuard, BargeInGate, Backchannel, MicroAck, tracing).

`audio_out_10ms_chunks=2` batches outbound audio into 20 ms WebRTC packets — half the packet rate of the 10 ms minimum at the cost of ~20 ms first-packet buffering.

**First-audio-out budget** (STATUS.md and code): MLX Qwen3.5-4B 4-bit TTFB 327 ms p50 + Kokoro TTFA 294 ms p50 + transport batching ~20 ms ≈ **~640 ms TTS-side, ~1.0–1.2s end-to-end** including STT and pipeline-frame hops.

---

## Frontend round-trip

### RTVI events actually consumed (`web/src/voice/VoiceStateBridge.tsx`)

| Event | Effect |
|---|---|
| `BotReady` | `state=idle` + 3s "connected — speak" toast in StatusPill |
| `Error` | 4s "error: <msg>" toast |
| `UserStartedSpeaking` | `state=listening` |
| `UserStoppedSpeaking` | **No-op** ("next event wins") |
| `UserTranscript` | `lastUserTranscript` only when `final===true` |
| `BotLlmStarted` | `state=thinking` |
| `BotStartedSpeaking` | `state=speaking` |
| `BotStoppedSpeaking` | `state=idle` |
| `BotTranscript` | `lastBotText` |
| `LLMFunctionCallStarted` | `activeToolCall = {name, args}` |
| `LLMFunctionCallStopped` | `activeToolCall = null` |

Server events emitted but NOT consumed: `BotTtsStarted` / `BotTtsStopped` / `BotLlmStopped` (`useBotTurnEvents` hook in `hooks.ts:55-60` exposes them, no caller).

### Orb visualization (`OrbStage.tsx`)

- R3F + three.js + postprocessing (`LumaChromaticAberrationEffect`).
- Audio→shader bridge via `usePipecatClientMediaTrack('audio', 'bot' | 'local')` → `useAudioEnvelopes` analyzer. Bot envelope drives `density/scale/asymmetry` in shader uniforms; voice state crossfades between presets (idle/listening/thinking/speaking, `STATE_XFADE_MS=600`).
- Orb visual state changes (variant, palette, params) originate outside the LLM — handled by other processes. The `/api/config` PATCH endpoint is the write path; client re-reads on next load or via direct `setVariant`/`applyPreset` calls.

### Tauri shell + sidecar

- Bundle: `externalBin: ["binaries/orbis"]`; capabilities pin to `binaries/orbis --host 127.0.0.1 --port 0`.
- `entitlements.plist`: microphone audio input, network client/server, and the narrow JIT exception needed by WKWebView. Camera and broad code-signing exceptions are intentionally absent.
- Sidecar spawn (`src-tauri/src/lib.rs`): check/request macOS microphone permission → start the native audio engine (AVAudioEngine voice-processing input on Mac production builds, CPAL output) → bind the Unix audio socket → resolve config path (`$ORBIS_CONFIG` else `<app_data_dir>/orbis.yaml`, no relative-path fallback) → seed example config if missing → spawn with `ORBIS_CONFIG=<path>`, `START_VLLM=<env or "0">`, `AUDIO_TRANSPORT=native`, `ORBIS_AUDIO_SOCK=<path>`, and `ORBIS_AUDIO_INPUT_MODE=voice_processing` → stream stdout for `ORBIS_READY http://...` line → navigate webview.

---

## Observability surface

| Span / event | Layer | Source |
|---|---|---|
| `stt.whisper` | STT | `voice/stt.py:113-131` |
| `backchannel.emit` | Filler | `agent/backchannel.py:170-193` |
| `filler.progress` | Filler | `app.py:912-927` |
| `delivery.speak_now` | Delivery | `agent/delivery.py:166-170` |
| FillerGenerator chat completions | Filler | auto-captured when Langfuse env set (`agent/filler.py:35-41`) |
| Pipeline frame metrics | Pipecat | `enable_metrics=True` (`app.py:861`) |
| Turn-level Langfuse trace | Top-level | `_tracing.make_turn_tracer(session_id, user_id)` (`app.py:854-857`) |
| RTVI data-channel events | Top-level | `rtvi.create_rtvi_observer(RTVIObserverParams())` |

Notable log strings: `[echoguard] suppressing audio` / `resuming audio`; `[bargein] rejected false positive` / `transcription confirmed — releasing interrupt` / `grace elapsed — releasing interrupt`; `[filler:progress] {phrase!r}`; `[filler] tool cancelled (barge-in)`; `[stt.local] whisper {dur}s → {infer}s → text=…`. Counters: `_METRICS["sessions_total"/"sessions_active"/"tool_calls_total"]` + `tool_calls_by_name`.

Telemetry gap: `MicroAckInjector` has no `tracing.span` — only an INFO log. Backchannel and progress have spans. No counters for filler emissions either.

---

## Configuration surface

### Env vars (touched per layer)

- **STT**: `STT_BACKEND`, `WHISPER_MODEL`, `STT_URL`, `STT_MODEL`, `STT_API_KEY`; can also be set per install in `config/orbis.yaml` under `stt.{backend,whisper_model,url,model,api_key}`.
- **VAD/turn**: `SMART_TURN` (off/local/v3)
- **Echo**: `HALF_DUPLEX`, `ECHO_GUARD_MS`, `NOISE_FILTER`
- **LLM**: `LLM_URL`, `LLM_SERVED_NAME`, `LLM_API_KEY`, `LLM_TEMPERATURE`, `LLM_MAX_TOKENS`, `START_VLLM`, `VLLM_PORT`, `LLM_MODEL`, `ORBIS_LLM_DETECT_DISABLE`
- **Memory**: `MEMORY_MAX_CONTEXT_TOKENS`, `MEMORY_MAX_MESSAGES`, `MEMORY_TARGET_CONTEXT_TOKENS`, `MEMORY_SUMMARIZE`
- **Backchannel**: `BACKCHANNEL_FIRST_SECS`, `BACKCHANNEL_INTERVAL_SECS`, `BACKCHANNEL_COMMIT_GRACE_MS`
- **Verbosity**: `VERBOSITY` (silent/brief/narrated/chatty)
- **Delivery**: `DELIVERY_NEXT_SILENCE_FALLBACK_SECS`, `DELIVERY_WHEN_ASKED_TTL_SECS`, `SESSION_STORE_DIR`
- **A2A push**: `A2A_PUSH_URL`, `A2A_PUSH_TOKEN`
- **TTS**: `TTS_BACKEND`; per backend: `KOKORO_VOICE/LANG`, `TTS_OPENAI_*`, `ELEVENLABS_*`, `FISH_URL/TIMEOUT`. OpenAI-compatible TTS can also be set in `config/orbis.yaml` via `voice.{tts_url,tts_model,tts_api_key}`.

### Per-skill `behavior` block

`config/orbis.yaml`, parsed in `_resolve_behavior_block` (`app.py:310-327`):

- `behavior.backchannel`: `false` | `{enabled, first_ms, interval_ms}`
- `behavior.micro_ack`: `false` | `{enabled, first_ms}`
- `behavior.bargein`: `false` | `{enabled, grace_ms}`

Per-skill: `tts_backend`, `voice`, `llm.{url,model,api_key,api_key_env,provider,extra_body}`, `temperature`, `max_tokens`, `delegates`, `tools` (tool allow-list), `filler_verbosity`, `user_name`.

Runtime: `GET/POST /api/verbosity` reads/writes `user_state_for(user.id).filler_settings.verbosity`.
