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


def _hardware_fingerprint() -> str:
    """One-line system summary attached to each bench run.

    Bench numbers are only useful if you know what they were measured
    on; surfacing this at the top of every run means screenshots or
    pasted output are self-describing — no separate "what machine
    did you measure on?" round-trip.
    """
    import platform as _p
    bits = [f"host: {_p.system()} {_p.release()} {_p.machine()}"]
    try:
        if sys.platform == "darwin":
            import subprocess as _sp
            chip = _sp.check_output(
                ["sysctl", "-n", "machdep.cpu.brand_string"], text=True
            ).strip()
            mem_bytes = int(
                _sp.check_output(["sysctl", "-n", "hw.memsize"], text=True).strip()
            )
            ncpu = _sp.check_output(
                ["sysctl", "-n", "hw.ncpu"], text=True
            ).strip()
            try:
                model = _sp.check_output(
                    ["sysctl", "-n", "hw.model"], text=True
                ).strip()
            except _sp.CalledProcessError:
                model = "?"
            bits.append(
                f"  chip: {chip}  cores: {ncpu}  ram: {mem_bytes // (1024**3)} GB"
            )
            bits.append(f"  model: {model}")
        else:
            # Linux: read /proc/cpuinfo + /proc/meminfo. Trimmed to one
            # line so output stays scannable.
            try:
                with open("/proc/cpuinfo") as f:
                    chip = next(
                        (
                            ln.split(":", 1)[1].strip()
                            for ln in f
                            if ln.startswith("model name")
                        ),
                        "?",
                    )
                with open("/proc/meminfo") as f:
                    mem_kb = int(
                        next(
                            ln for ln in f if ln.startswith("MemTotal:")
                        ).split()[1]
                    )
                bits.append(f"  chip: {chip}  ram: {mem_kb // (1024 * 1024)} GB")
            except (OSError, StopIteration):
                pass
    except Exception:
        # Best-effort — never fail the bench because of an info probe.
        pass
    return "\n".join(bits)


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
# MLX — in-process LLM (Apple Silicon native)
# ---------------------------------------------------------------------------

async def bench_mlx(turns: int, model_id: str) -> tuple[list[float], list[float], list[float]]:
    """Returns (ttfb_samples, total_samples, tokens_per_sec_samples)."""
    # Lazy import — MLX is Apple-Silicon only.
    import mlx.core as mx  # type: ignore
    from mlx_lm import load, stream_generate  # type: ignore

    print(f"  loading {model_id}…")
    t0 = time.time()
    model, tokenizer = load(model_id)
    print(f"  loaded in {time.time() - t0:.1f}s")

    ttfbs: list[float] = []
    totals: list[float] = []
    tps: list[float] = []
    for i in range(turns):
        prompt_text = PROMPTS[i % len(PROMPTS)]
        try:
            prompt = tokenizer.apply_chat_template(
                [{"role": "user", "content": prompt_text}],
                tokenize=False, add_generation_prompt=True,
                enable_thinking=False,
            )
        except TypeError:
            prompt = tokenizer.apply_chat_template(
                [{"role": "user", "content": prompt_text}],
                tokenize=False, add_generation_prompt=True,
            )
        t0 = time.time()
        first = None
        n_text_tokens = 0
        with mx.stream(mx.gpu):
            for resp in stream_generate(model, tokenizer, prompt, max_tokens=40):
                if first is None and (getattr(resp, "text", "") or ""):
                    first = time.time() - t0
                n_text_tokens += 1
        total = time.time() - t0
        if first is not None:
            ttfbs.append(first)
            totals.append(total)
            # Decode-only tok/s = (total tokens - 1) / (total - ttfb).
            decode_time = max(total - first, 1e-6)
            tps.append(max(n_text_tokens - 1, 1) / decode_time)
    return ttfbs, totals, tps


# ---------------------------------------------------------------------------
# Kokoro — in-process TTS
# ---------------------------------------------------------------------------

async def bench_kokoro(turns: int) -> tuple[list[float], list[float], list[float]]:
    """Returns (ttfa_samples, total_samples, real_time_factor_samples).

    RTF = synth_time / audio_duration. <1.0 means faster-than-realtime.
    """
    from voice.tts.kokoro import _get_pipe, KOKORO_SR  # type: ignore
    pipe = _get_pipe()  # warm
    SENTS = [
        "Hi there.",
        "What's the capital of France?",
        "I think the capital is Paris.",
        "Here's a fun fact: the orb sees four moods.",
        "Two plus two equals four, of course.",
    ]
    ttfas: list[float] = []
    totals: list[float] = []
    rtfs: list[float] = []
    for i in range(turns):
        text = SENTS[i % len(SENTS)]
        t0 = time.time()
        first = None
        n_samples = 0
        for chunk in pipe(text, voice="af_heart", speed=1.0):
            audio = chunk[2] if len(chunk) >= 3 else chunk
            if audio is None:
                continue
            if first is None:
                first = time.time() - t0
            n_samples += len(audio)
        total = time.time() - t0
        if first is not None:
            ttfas.append(first)
            totals.append(total)
            audio_secs = n_samples / KOKORO_SR
            if audio_secs > 0:
                rtfs.append(total / audio_secs)
    return ttfas, totals, rtfs


# ---------------------------------------------------------------------------
# STT — Whisper on a single audio file
# ---------------------------------------------------------------------------

async def bench_stt(turns: int, audio_path: str | None) -> list[float]:
    # Lazy import so people who don't have torch installed can still bench
    # the HTTP-based services.
    from voice.stt import transcribe_bytes, _get_local_pipe
    _get_local_pipe()  # warm
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
    parser.add_argument("--llm", action="store_true", help="HTTP OpenAI-compat LLM (vLLM/Ollama/OpenAI)")
    parser.add_argument("--mlx", action="store_true", help="In-process MLX-LM (Apple Silicon)")
    parser.add_argument("--mlx-model", type=str,
                        default=os.environ.get("MLX_BENCH_MODEL",
                                              "mlx-community/Qwen3.5-4B-MLX-4bit"))
    parser.add_argument("--kokoro", action="store_true", help="In-process Kokoro TTS")
    parser.add_argument("--fish", action="store_true")
    parser.add_argument("--a2a", action="store_true")
    parser.add_argument("--stt", action="store_true")
    parser.add_argument("--audio", type=str, default=None)
    parser.add_argument("--all", action="store_true",
                        help="Run mlx + kokoro + stt — the desktop-app baseline triple")
    args = parser.parse_args()

    if args.all or not any([args.llm, args.mlx, args.kokoro, args.fish, args.a2a, args.stt]):
        # --all is the desktop-app voice baseline: MLX + Kokoro + STT.
        # The HTTP-LLM / Fish / A2A paths stay opt-in for non-default
        # configurations.
        args.mlx = args.kokoro = args.stt = True

    print(f"=== ORBIS bench — {args.turns} turns ===")
    print(_hardware_fingerprint())
    print()

    if args.llm:
        try:
            print(f"LLM  → {LLM_URL}  model={LLM_MODEL}")
            ttfb, total = await bench_llm_ttfb(args.turns)
            print(stats("LLM TTFB (streaming)", ttfb))
            print(stats("LLM total (40 tokens)", total))
            print()
        except Exception as e:
            print(f"LLM bench failed: {e}\n")

    if args.mlx:
        try:
            print(f"MLX  → in-process  model={args.mlx_model}")
            ttfb, total, tps = await bench_mlx(args.turns, args.mlx_model)
            print(stats("MLX TTFB (streaming)", ttfb))
            print(stats("MLX total (≤40 tokens)", total))
            if tps:
                avg_tps = statistics.mean(tps)
                print(f"{'MLX decode tok/s':28s} n={len(tps):2d}  avg={avg_tps:6.1f} tok/s")
            print()
        except Exception as e:
            print(f"MLX bench failed: {e}\n")

    if args.kokoro:
        try:
            print("TTS  → Kokoro (local)")
            ttfa, total, rtf = await bench_kokoro(args.turns)
            print(stats("Kokoro TTFA (first audio)", ttfa))
            print(stats("Kokoro synth total", total))
            if rtf:
                avg_rtf = statistics.mean(rtf)
                print(f"{'Kokoro RTF':28s} n={len(rtf):2d}  avg={avg_rtf:6.2f}x  (<1.0 = faster than realtime)")
            print()
        except Exception as e:
            print(f"Kokoro bench failed: {e}\n")

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
