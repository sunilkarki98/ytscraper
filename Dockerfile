# Youtube Email Scraper Pro — Multi-stage Docker Image
# Targets: "api" (Gunicorn) and "worker" (ARQ)
FROM python:3.12-slim AS base

WORKDIR /app

# System deps for curl_cffi and asyncpg
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ curl libcurl4-openssl-dev libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Python deps (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY . .

# ─── API target ──────────────────────────────────────────────────────
FROM base AS api
ENV PORT=8000
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

CMD ["gunicorn", "app:app", \
     "--workers", "4", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--timeout", "120", \
     "--graceful-timeout", "30", \
     "--keep-alive", "5", \
     "--bind", "0.0.0.0:8000"]

# ─── Worker target ───────────────────────────────────────────────────
FROM base AS worker

# No HTTP port needed — workers only talk to Redis + Supabase
HEALTHCHECK --interval=60s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import redis; r=redis.from_url('${REDIS_URL:-redis://redis:6379/0}'); r.ping()" || exit 1

CMD ["python", "-m", "arq", "worker.WorkerSettings"]
