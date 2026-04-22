#!/usr/bin/env python3
"""Benchmark the core backends independently.

Measures the latency of each component in isolation so we can compare
configurations and catch regressions. Does NOT run the full voice
pipeline — for that, record a real session.

Usage:
    python scripts/bench.py --turns 5
    python scripts/bench.py --llm --fish
    python scripts/bench.py --stt --audio /path/to/sample.wav

Outputs p50 / p95 / avg for each component + a one-line summary.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
import time
from pathlib import Path

import httpx

SCRIPT_DIR = Path(__file__).parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(ROOT))

LLM_URL = os.environ.get("LLM_URL", "http://localhost:8000/v1")
LLM_MODEL = os.environ.get("LLM_SERVED_NAME", "local")
LLM_KEY = os.environ.get("LLM_API_KEY", "not-needed")

FISH_URL = os.environ.get("FISH_URL", "http://localhost:8092")
FISH_REF = os.environ.get("FISH_REFERENCE_ID", "josh_sample_1")

PROTOVOICE_URL = os.environ.get("PROTOVOICE_URL", "http://localhost:7867")

PROMPTS = [
    "Say hi.",
    "What's the capital of France?",
    "Name a color.",
    "Give me a one-sentence fun fact.",
    "What's 2 plus 2?",
]


def stats(label: str, samples: list[float]) -> str:
    if not samples:
        return f"{label}: no samples"
    avg = statistics.mean(samples)
    p50 = statistics.median(samples)
    p95 = statistics.quantiles(samples, n=20)[-1] if len(samples) >= 2 else samples[0]
    mn, mx = min(samples), max(samples)
    return (
        f"{label:28s} n={len(samples):2d}  "
        f"avg={avg*1000:6.0f}ms  p50={p50*1000:6.0f}ms  "
        f"p95={p95*1000:6.0f}ms  min={mn*1000:5.0f}ms  max={mx*1000:5.0f}ms"
    )


# ---------------------------------------------------------------------------
# LLM — first-token time (TTFB) + full-response time
# ---------------------------------------------------------------------------

async def bench_llm_ttfb(turns: int) -> tuple[list[float], list[float]]:
    from openai import AsyncOpenAI
    client = AsyncOpenAI(base_url=LLM_URL, api_key=LLM_KEY)
    ttfbs: list[float] = []
    totals: list[float] = []
    for i in range(turns):
        prompt = PROMPTS[i % len(PROMPTS)]
        t0 = time.time()
        first = None
        stream = await client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=40,
            stream=True,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        async for ch in stream:
            if ch.choices and ch.choices[0].delta.content:
                if first is None:
                    first = time.time() - t0
        if first is not None:
            ttfbs.append(first)
            totals.append(time.time() - t0)
    return ttfbs, totals


# ---------------------------------------------------------------------------
# Fish — TTFA (first PCM byte) + full synthesis time + RTF
# ---------------------------------------------------------------------------

async def bench_fish(turns: int) -> tuple[list[float], list[float], list[float]]:
    ttfas: list[float] = []
    totals: list[float] = []
    rtfs: list[float] = []
    async with httpx.AsyncClient(timeout=120) as c:
        for i in range(turns):
            text = PROMPTS[i % len(PROMPTS)]
            t0 = time.time()
            first = None
            total_bytes = 0
            async with c.stream(
                "POST", f"{FISH_URL}/v1/tts",
                json={
                    "text": text,
                    "format": "wav",
                    "streaming": True,
                    "reference_id": FISH_REF,
                },
            ) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes():
                    if not chunk:
                        continue
                    if first is None:
                        first = time.time() - t0
                    total_bytes += len(chunk)
            if first is not None:
                ttfas.append(first)
                duration = total_bytes / 2 / 44100
                elapsed = time.time() - t0
                totals.append(elapsed)
                if duration > 0:
                    rtfs.append(elapsed / duration)
    return ttfas, totals, rtfs


# ---------------------------------------------------------------------------
# A2A — round-trip time through our inbound handler
# ---------------------------------------------------------------------------

async def bench_a2a(turns: int) -> list[float]:
    samples: list[float] = []
    async with httpx.AsyncClient(timeout=60) as c:
        for i in range(turns):
            body = {
                "jsonrpc": "2.0",
                "id": f"bench-{i}",
                "method": "message/send",
                "params": {
                    "contextId": "bench",
                    "message": {
                        "role": "user",
                        "parts": [{"kind": "text", "text": PROMPTS[i % len(PROMPTS)]}],
                    },
                },
            }
            t0 = time.time()
            r = await c.post(f"{PROTOVOICE_URL}/a2a", json=body)
            r.raise_for_status()
            samples.append(time.time() - t0)
    return samples


# ---------------------------------------------------------------------------
# STT — Whisper on a single audio file
# ---------------------------------------------------------------------------

async def bench_stt(turns: int, audio_path: str | None) -> list[float]:
    # Lazy import so people who don't have torch installed can still bench
    # the HTTP-based services.
    from voice.stt import transcribe_bytes, _get_pipe
    _get_pipe()  # warm
    if audio_path:
        raw = Path(audio_path).read_bytes()
    else:
        # Generate ~3s of silence as a no-input control sample.
        import numpy as np
        import soundfile as sf
        import io
        buf = io.BytesIO()
        sf.write(buf, np.zeros(3 * 16000, dtype="float32"), 16000, format="WAV")
        raw = buf.getvalue()
    samples: list[float] = []
    for _ in range(turns):
        t0 = time.time()
        transcribe_bytes(raw)
        samples.append(time.time() - t0)
    return samples


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--turns", type=int, default=5)
    parser.add_argument("--llm", action="store_true")
    parser.add_argument("--fish", action="store_true")
    parser.add_argument("--a2a", action="store_true")
    parser.add_argument("--stt", action="store_true")
    parser.add_argument("--audio", type=str, default=None)
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()

    if args.all or not any([args.llm, args.fish, args.a2a, args.stt]):
        args.llm = args.fish = args.a2a = True
        # STT excluded from --all because loading Whisper on a no-GPU host
        # is painful; opt in explicitly.

    print(f"=== protoVoice bench — {args.turns} turns ===\n")

    if args.llm:
        try:
            print(f"LLM  → {LLM_URL}  model={LLM_MODEL}")
            ttfb, total = await bench_llm_ttfb(args.turns)
            print(stats("LLM TTFB (streaming)", ttfb))
            print(stats("LLM total (40 tokens)", total))
            print()
        except Exception as e:
            print(f"LLM bench failed: {e}\n")

    if args.fish:
        try:
            print(f"FISH → {FISH_URL}  ref={FISH_REF}")
            ttfa, total, rtf = await bench_fish(args.turns)
            print(stats("Fish TTFA (first byte)", ttfa))
            print(stats("Fish synth total", total))
            print(stats("Fish RTF", rtf))
            print()
        except Exception as e:
            print(f"Fish bench failed: {e}\n")

    if args.a2a:
        try:
            print(f"A2A  → {PROTOVOICE_URL}/a2a")
            rt = await bench_a2a(args.turns)
            print(stats("A2A round-trip", rt))
            print()
        except Exception as e:
            print(f"A2A bench failed: {e}\n")

    if args.stt:
        try:
            print(f"STT  → Whisper (local)  audio={args.audio or '3s silence'}")
            samples = await bench_stt(args.turns, args.audio)
            print(stats("Whisper STT", samples))
            print()
        except Exception as e:
            print(f"STT bench failed: {e}\n")


if __name__ == "__main__":
    asyncio.run(main())
