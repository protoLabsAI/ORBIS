"""vLLM subprocess lifecycle — start, wait-for-ready, stop.

Mirrors the pattern in pre-pipecat app.py. The small routing LLM runs as
a child OpenAI-compatible server on VLLM_PORT; Pipecat's OpenAILLMService
points at it via base_url.
"""

import json
import logging
import os
import subprocess
import sys
import time

import httpx

logger = logging.getLogger(__name__)

VLLM_PORT = int(os.environ.get("VLLM_PORT", "8100"))
LLM_MODEL = os.environ.get("LLM_MODEL", "Qwen/Qwen3.5-4B")
START_VLLM = os.environ.get("START_VLLM", "1") == "1"

_proc: subprocess.Popen | None = None


def start() -> None:
    global _proc
    if not _proc and not START_VLLM:
        logger.info("START_VLLM=0 — assuming external LLM")
        return
    if _proc:
        return
    logger.info(f"Starting vLLM {LLM_MODEL} on :{VLLM_PORT}")
    _proc = subprocess.Popen(
        [
            sys.executable, "-m", "vllm.entrypoints.openai.api_server",
            "--model", LLM_MODEL,
            "--host", "127.0.0.1",
            "--port", str(VLLM_PORT),
            "--served-model-name", "local",
            "--max-model-len", "32768",
            "--gpu-memory-utilization", "0.40",
            "--enable-prefix-caching",
            "--enable-chunked-prefill",
            "--chat-template-kwargs", json.dumps({"enable_thinking": False}),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    for _ in range(120):
        try:
            r = httpx.get(f"http://localhost:{VLLM_PORT}/v1/models", timeout=2.0)
            if r.status_code == 200:
                logger.info(f"vLLM ready on :{VLLM_PORT}")
                return
        except Exception:
            pass
        time.sleep(1)
    logger.error("vLLM failed to start within 120s")


def stop() -> None:
    global _proc
    if _proc:
        _proc.terminate()
        try:
            _proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _proc.kill()
        _proc = None
