# Fish Audio S2-Pro sidecar for protoVoice.
#
# Expects build context = a checkout of fish-speech that already has
# `.venv/` populated and `checkpoints/s2-pro/` present. The context path
# in docker-compose.yml is ../fish-speech by default.
#
# Launch args match ~/dev/lab/experiments/tts-compare — `--half --compile`
# are REQUIRED for acceptable RTF on Blackwell (drops from ~3.0 to ~0.40).
# First call after start triggers ~2min torch.compile codegen.

FROM nvidia/cuda:12.8.0-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip ffmpeg libsndfile1 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy the whole checkout — includes .venv and checkpoints/.
COPY . /app

ENV PATH="/app/.venv/bin:${PATH}"
ENV PYTHONUNBUFFERED=1

EXPOSE 8092

HEALTHCHECK --interval=30s --timeout=10s --start-period=180s --retries=3 \
    CMD curl -fsS http://localhost:8092/v1/health || exit 1

CMD [".venv/bin/python", "-m", "tools.api_server", \
     "--listen", "0.0.0.0:8092", \
     "--llama-checkpoint-path", "checkpoints/s2-pro", \
     "--decoder-checkpoint-path", "checkpoints/s2-pro/codec.pth", \
     "--decoder-config-name", "modded_dac_vq", \
     "--half", "--compile"]
