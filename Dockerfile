# ORBIS — voice agent container (pipecat pipeline).
#
# Whisper STT + routing vLLM (Qwen 4B) + Kokoro TTS (default, CPU-only).
# Fish Audio S2-Pro is opt-in and runs as a separate sidecar container —
# see docker-compose.yml (`fish` profile) + Dockerfile.fish.
#
# Build:  docker build -t orbis .
# Run:    docker compose up -d  (kokoro default; add --profile fish for Fish)

# ---------------------------------------------------------------------------
# Stage 1: build the React SPA (web/).
# Uses bun for install + Vite build. Output lands at /web/dist.
# ---------------------------------------------------------------------------
FROM oven/bun:1 AS web
WORKDIR /web
COPY web/package.json web/bun.lock* ./
RUN bun install --frozen-lockfile
# The orb runtime is consumed as source from OUTSIDE web/ (vite alias +
# tsconfig paths point at ../packages/orb-runtime) — without this copy
# the web build can't resolve @orbis/orb-runtime inside the image.
COPY packages/ /packages/
COPY web/ ./
RUN bun run build

# ---------------------------------------------------------------------------
# Stage 2: runtime (CUDA + Python).
# ---------------------------------------------------------------------------
FROM nvidia/cuda:12.8.0-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv python3-dev \
    ffmpeg espeak-ng \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install deps (layer cached separately from source).
#
# torch is pinned to the CUDA 12.8 wheel to match the container base
# (nvidia/cuda:12.8.0-runtime). The default PyPI torch wheel ships
# against CUDA 13.x which requires a newer driver than the 570 series
# ships. Install torch first from the cu128 index so the subsequent
# pyproject-driven install sees the requirement satisfied and doesn't
# pull the wrong wheel.
COPY pyproject.toml ./
# Extract `[project] dependencies` from pyproject and write each to its
# own line in /tmp/requirements.txt, then `pip install -r`. The earlier
# `' '.join(...)` + shell-glob path fragmented PEP 508 environment
# markers — `mlx-lm>=0.20; sys_platform == 'darwin'` got tokenized into
# eight shell args, with `==` arriving at pip as a standalone arg and
# failing the install. One-line-per-dep keeps markers intact.
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cu128 \
        torch && \
    python3 -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); print('\n'.join(d['project']['dependencies']))" > /tmp/requirements.txt && \
    pip install --no-cache-dir -r /tmp/requirements.txt

# Spacy model is required by Kokoro (fallback TTS).
RUN pip install --no-cache-dir \
    https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl

COPY app.py ./
# A2A 1.0 (#354) replaced the hand-rolled `a2a/` package with flat
# `a2a_*.py` modules (import a2a = the a2a-sdk pip dep). Copy those.
COPY a2a_*.py ./
COPY acp/ ./acp/
COPY agent/ ./agent/
COPY auth/ ./auth/
COPY config/ ./config/
COPY memory/ ./memory/
COPY server/ ./server/
COPY static/ ./static/
COPY voice/ ./voice/
# Built SPA from stage 1 — served at / when FRONTEND=react (default once verified).
COPY --from=web /web/dist/ ./web/dist/

# Prove the final runtime filesystem can import every boot-critical module.
# Keep this after all COPY instructions: BuildKit will refuse to publish an
# image when a source package is missing, and disabling bytecode avoids baking
# build-host .pyc files into the runtime layer.
RUN PYTHONDONTWRITEBYTECODE=1 python3 -c "import acp, app, server, agent.delegate_adapters, agent.tools, agent.filler, agent.delivery, agent.backchannel, voice.stt, voice.tts, a2a.server; print('imports ok')"

ENV PYTHONUNBUFFERED=1
ENV HF_HOME=/models
ENV MODEL_DIR=/models
ENV PORT=7866
ENV VLLM_PORT=8100
ENV TTS_BACKEND=kokoro

EXPOSE 7866

HEALTHCHECK --interval=30s --timeout=10s --start-period=180s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:7866/healthz')" || exit 1

CMD ["python3", "app.py"]
