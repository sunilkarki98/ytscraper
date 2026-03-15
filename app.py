"""
Youtube Email Scraper Pro — SaaS Web App
=========================================
FastAPI server with auth, credits, queue, and database.
Run: python app.py -> open http://localhost:8000

Security:
  - CORS lockdown (explicit origins)
  - WebSocket auth (JWT token in query param)
  - API rate limiting (slowapi)
  - Input validation (capped max_emails, timeouts)
  - Login throttling via Redis (brute force protection)
  - Health endpoint for monitoring
"""
import os
import asyncio
import logging
from contextlib import asynccontextmanager

logger = logging.getLogger("app")

import sentry_sdk
sentry_dsn = os.environ.get("SENTRY_DSN")
if sentry_dsn:
    sentry_sdk.init(
        dsn=sentry_dsn,
        traces_sample_rate=1.0,
        environment=os.environ.get("ENVIRONMENT", "production")
    )

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from database import init_db, get_db
from queue_manager import retention_worker

import shared_state
from arq import create_pool

# Import route modules
from routes import pages, auth_routes, scraper_routes, websocket_routes, job_routes


# ─── Rate Limiting ───────────────────────────────────────────────────
try:
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.util import get_remote_address
    from slowapi.errors import RateLimitExceeded
    from limits.storage import RedisStorage
    
    REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    limiter = Limiter(key_func=get_remote_address, storage_uri=REDIS_URL)
    HAS_RATE_LIMITER = True
except ImportError:
    HAS_RATE_LIMITER = False
    limiter = None


# ─── Login Throttle (Redis-backed, works across workers) ────────────
MAX_LOGIN_ATTEMPTS = 10
LOGIN_WINDOW_SECONDS = 300


async def is_login_throttled(ip: str) -> bool:
    """Check if IP has exceeded login attempt limit using Redis sorted sets."""
    import time
    key = f"login_attempts:{ip}"
    now = time.time()
    try:
        # Remove old entries outside the window
        await shared_state.redis_client.zremrangebyscore(key, 0, now - LOGIN_WINDOW_SECONDS)
        # Count remaining attempts
        count = await shared_state.redis_client.zcard(key)
        return count >= MAX_LOGIN_ATTEMPTS
    except Exception as e:
        logger.error(f"Redis throttle check error: {e}")
        if sentry_dsn:
            sentry_sdk.capture_exception(e)
        return False  # Fail open to avoid locking out users if Redis is down


async def record_login_attempt(ip: str):
    """Record a login attempt from IP in Redis with auto-expiry."""
    import time
    key = f"login_attempts:{ip}"
    now = time.time()
    try:
        await shared_state.redis_client.zadd(key, {str(now): now})
        await shared_state.redis_client.expire(key, LOGIN_WINDOW_SECONDS)
    except Exception as e:
        logger.error(f"Redis throttle record error: {e}")


# ─── Lifespan ────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    await init_db()

    # Instantiate ARQ enqueue pool to talk to worker.py
    shared_state.arq_pool = await create_pool(shared_state.arq_redis_settings)
    
    # Purge stale ARQ jobs so restarting the app doesn't resume aborted tasks
    try:
        redis = shared_state.arq_pool
        stale_keys = await redis.keys("arq:job:*")
        if stale_keys:
            await redis.delete(*stale_keys)
            logger.info(f"🧹 Purged {len(stale_keys)} stale ARQ jobs from Redis to prevent ghost retries.")
    except Exception as e:
        logger.warning(f"Failed to purge stale ARQ jobs: {e}")

    # Recover orphaned jobs from a previous crash
    # Any job stuck in RUNNING was clearly interrupted
    from database import async_session
    from sqlalchemy import select
    from models import Job, JobStatus
    async with async_session() as db:
        result = await db.execute(select(Job).where(Job.status == JobStatus.RUNNING))
        orphaned = result.scalars().all()
        for job in orphaned:
            job.status = JobStatus.QUEUED
            await shared_state.arq_pool.enqueue_job('start_spider_for_job', job.id, _job_id=job.id)
            
        if orphaned:
            await db.commit()
            logger.warning(f"Re-queued {len(orphaned)} orphaned RUNNING jobs back into ARQ: {[j.id for j in orphaned]}")
    
    # Start retention worker
    asyncio.create_task(retention_worker(get_db))
    logger.info("Database initialized, ARQ pool active, retention worker started")
    yield
    logger.info("Shutting down...")


# ─── App ─────────────────────────────────────────────────────────────
app = FastAPI(title="Youtube Email Scraper Pro", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")


# ─── CORS ────────────────────────────────────────────────────────────
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "").split(",")
ALLOWED_ORIGINS = [o.strip() for o in ALLOWED_ORIGINS if o.strip()]
if not ALLOWED_ORIGINS:
    ALLOWED_ORIGINS = ["http://localhost:8000", "http://127.0.0.1:8000", "http://localhost:3000"]
    logger.warning("ALLOWED_ORIGINS not set — restricting to localhost. Set ALLOWED_ORIGINS env var for production.")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Rate Limiter Setup ─────────────────────────────────────────────
if HAS_RATE_LIMITER:
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ─── Security Headers (replaces Caddy headers for Coolify/Traefik) ──
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarletteRequest, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

app.add_middleware(SecurityHeadersMiddleware)

# ─── Register Routers ───────────────────────────────────────────────
app.include_router(pages.router)
app.include_router(auth_routes.router)
app.include_router(scraper_routes.router)
app.include_router(websocket_routes.router)
app.include_router(job_routes.router)


# ─── Main ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    logger.info(f"Youtube Email Scraper Pro — starting on http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
