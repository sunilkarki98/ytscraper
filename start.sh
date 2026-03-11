#!/bin/bash

# Load environment variables if .env exists
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

echo "🚀 Starting StandaloneYT Email Scraper Distributed Stack..."

# 1. Start multiple ARQ Spider workers for horizontal scaling
echo "🕸️  Starting ARQ background workers..."
arq worker.WorkerSettings &
ARQ_PID1=$!
arq worker.WorkerSettings &
ARQ_PID2=$!

# Function to properly kill child processes on exit
cleanup() {
    echo "🛑 Received stop signal. Shutting down stack..."
    echo "Killing ARQ workers..."
    kill -TERM "$ARQ_PID1" "$ARQ_PID2" 2>/dev/null
    wait "$ARQ_PID1" "$ARQ_PID2" 2>/dev/null
    echo "Done."
    exit 0
}

# Trap termination signals
trap cleanup SIGINT SIGTERM

# 2. Start the FastAPI orchestrator with multiple workers for production scale
echo "🌐 Starting Gunicorn/Uvicorn Web Server (4 workers)..."
WORKERS=${GUNICORN_WORKERS:-4}
gunicorn app:app \
  --workers "$WORKERS" \
  --worker-class uvicorn.workers.UvicornWorker \
  --timeout 120 \
  --bind 0.0.0.0:${PORT:-8000}

# If gunicorn exits naturally, clean up ARQ
cleanup
