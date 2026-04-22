# protoVoice — voice agent container (pipecat pipeline).
#
# Whisper STT + routing vLLM (Qwen 4B) + Kokoro TTS fallback.
# Fish Audio S2-Pro is the DEFAULT TTS backend and runs as a separate
# sidecar container — see docker-compose.yml + Dockerfile.fish.
#
# Build:  docker build -t protovoice .
# Run:    docker compose up -d  (preferred — brings up fish-speech too)

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

# Install deps (layer cached separately from source)
COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir $(python3 -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); print(' '.join(d['project']['dependencies']))")

# Spacy model is required by Kokoro (fallback TTS).
RUN pip install --no-cache-dir \
    https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl

COPY app.py ./
COPY a2a/ ./a2a/
COPY agent/ ./agent/
COPY auth/ ./auth/
COPY config/ ./config/
COPY skills/ ./skills/
COPY static/ ./static/
COPY voice/ ./voice/
# Built SPA from stage 1 — served at / when FRONTEND=react (default once verified).
COPY --from=web /web/dist/ ./web/dist/

ENV PYTHONUNBUFFERED=1
ENV HF_HOME=/models
ENV MODEL_DIR=/models
ENV PORT=7866
ENV VLLM_PORT=8100
ENV TTS_BACKEND=fish

EXPOSE 7866

HEALTHCHECK --interval=30s --timeout=10s --start-period=180s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:7866/healthz')" || exit 1

CMD ["python3", "app.py"]
