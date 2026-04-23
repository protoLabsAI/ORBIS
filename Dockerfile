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
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cu128 \
        torch && \
    pip install --no-cache-dir $(python3 -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); print(' '.join(d['project']['dependencies']))")

# Spacy model is required by Kokoro (fallback TTS).
RUN pip install --no-cache-dir \
    https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl

COPY app.py ./
COPY a2a/ ./a2a/
COPY agent/ ./agent/
COPY auth/ ./auth/
COPY config/ ./config/
COPY memory/ ./memory/
COPY static/ ./static/
COPY voice/ ./voice/
# Built SPA from stage 1 — served at / when FRONTEND=react (default once verified).
COPY --from=web /web/dist/ ./web/dist/

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
