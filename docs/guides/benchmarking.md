# Benchmarking

`scripts/bench.py` times each backend component independently — Fish, vLLM, Whisper, A2A round-trip. Run it before/after config changes to see whether your tuning moved the needle.

It doesn't exercise the full WebRTC pipeline (that's hard to script without a browser). Use a live session + the server log for end-to-end times.

## Run

```bash
# defaults: LLM + Fish + A2A, 5 turns each
python scripts/bench.py

# all, with more samples
python scripts/bench.py --turns 20

# one component only
python scripts/bench.py --fish --turns 30
python scripts/bench.py --llm --turns 20

# STT — opt-in because it loads Whisper (heavy on a no-GPU box)
python scripts/bench.py --stt --audio path/to/sample.wav
```

## Sample output

```
=== protoVoice bench — 5 turns ===

LLM  → http://localhost:8000/v1  model=local
LLM TTFB (streaming)       n= 5  avg=    40ms  p50=    23ms  p95=   163ms
LLM total (40 tokens)      n= 5  avg=    99ms  p50=    62ms  p95=   218ms

FISH → http://localhost:8092  ref=josh_sample_1
Fish TTFA (first byte)     n= 5  avg=   648ms  p50=   579ms  p95=  1382ms
Fish synth total           n= 5  avg=   865ms  p50=   800ms  p95=  1603ms
Fish RTF                   n= 5  avg=   712ms  p50=   689ms  p95=  1034ms

A2A  → http://localhost:7867/a2a
A2A round-trip             n= 5  avg=    59ms  p50=    48ms  p95=    96ms
```

## Interpreting

The voice turn-taking budget is roughly:

```
TTFA end-to-end ≈ STT + LLM_TTFB + first_sentence_TTS + transport_and_browser
```

Where (on Blackwell + Qwen 3.6-35B-A3B + Fish S2-Pro):

- **STT** (Whisper large-v3-turbo) ≈ 55 ms per utterance
- **LLM TTFB** ≈ 40 ms with Qwen, 80-300 ms via a gateway
- **Fish TTFA** ≈ 580 ms for the first byte of the first sentence
- **Transport + browser** ≈ 30 ms

**Total ≈ 700-900 ms** with Fish. Kokoro fallback drops TTS TTFA to ~50 ms, bringing total to ~180 ms.

## Env knobs

The bench reads the same env vars the server uses:

| Variable | Default | Purpose |
|:---|:---|:---|
| `LLM_URL` | `http://localhost:8000/v1` | OpenAI-compat endpoint to benchmark |
| `LLM_SERVED_NAME` | `local` | Model name at that endpoint |
| `LLM_API_KEY` | `not-needed` | Bearer for the endpoint |
| `FISH_URL` | `http://localhost:8092` | Fish server |
| `FISH_REFERENCE_ID` | `josh_sample_1` | Voice to synthesize with |
| `PROTOVOICE_URL` | `http://localhost:7867` | protoVoice server for A2A bench |

## Scenarios to test

- **After restarting Fish:** first Fish call takes ~2 min for `torch.compile`; subsequent calls are fast. Bench in two passes.
- **Different LLM:** `LLM_URL=http://gateway:4000/v1 LLM_SERVED_NAME=claude-opus-4-6 python scripts/bench.py --llm`.
- **Different Fish voice:** `FISH_REFERENCE_ID=<slug> python scripts/bench.py --fish`. Voices with longer reference clips can be slightly slower.
- **Load:** run two bench processes in parallel. Fish + vLLM both scale to concurrent requests but with increased TTFA.

## Live session observability

The server logs useful timing info at INFO level per turn:

```
[filler:open] tool=web_search verbosity=brief async=False
OpenAILLMService#0 TTFB: 0.109s
OpenAILLMService#0 processing time: 0.759s
[deep_research] starting: 'hot dogs history'
[deep_research] done (187 chars)
```

Grep / tail for quick spot-checks. For long-term trending, scrape `/api/metrics`.
