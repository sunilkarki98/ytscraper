"""
Configuration — Environment-based config with .env support.
============================================================
All tuning constants are configurable via environment variables.
Proxies load from PROXY_LIST env var or proxies.txt file.
"""
import os
import logging
from typing import List, Optional
from pathlib import Path

logger = logging.getLogger('spider')


def _load_dotenv():
    """Load .env file if present (no dependency on python-dotenv)."""
    env_path = Path(__file__).parent.parent / '.env'
    if not env_path.exists():
        return
    with open(env_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, value = line.partition('=')
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:   # Don't override existing env
                os.environ[key] = value


# Load .env on import
_load_dotenv()


class Config:
    """Centralized configuration with env-var overrides."""

    # ── Proxy ──────────────────────────────────────────────────────
    # PROXY_LIST env: comma-separated "ip:port:user:pass" strings
    # Or provide a proxies.txt file (one proxy per line)
    PROXY_LIST: List[str] = []

    # ── Concurrency ───────────────────────────────────────────────────
    # Tuned for ~400MB Apify containers. InnerTube JSON = tiny, HTML parsed & discarded.
    SEARCH_THREADS: int = int(os.environ.get('SEARCH_THREADS', 20))
    CHANNEL_CONCURRENCY: int = int(os.environ.get('CHANNEL_CONCURRENCY', 50))
    CFFI_MAX_CLIENTS: int = int(os.environ.get('CFFI_MAX_CLIENTS', 50))

    # ── Timeouts (seconds) ────────────────────────────────────────
    CFFI_TIMEOUT: int = 5
    AUTOCOMPLETE_TIMEOUT: float = 3.0
    DEFAULT_TIME_BUDGET: int = 3600  # 60 min — spider stops when maxEmails is hit, not time

    # ── InnerTube ─────────────────────────────────────────────────
    INNERTUBE_API_KEY: str = os.environ.get('INNERTUBE_API_KEY', 'AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8')

    # ── Pipeline ──────────────────────────────────────────────────
    NUM_FETCH_WORKERS: int = int(os.environ.get('NUM_FETCH_WORKERS', 8))
    CHANNEL_QUEUE_SIZE: int = int(os.environ.get('CHANNEL_QUEUE_SIZE', 500))
    MAX_SEARCH_BATCHES: int = int(os.environ.get('MAX_SEARCH_BATCHES', 500))

    # ── Stealth ──────────────────────────────────────────────────
    REQUEST_DELAY_MIN: float = float(os.environ.get('REQUEST_DELAY_MIN', 0.05))
    REQUEST_DELAY_MAX: float = float(os.environ.get('REQUEST_DELAY_MAX', 0.15))

    # ── Memory ───────────────────────────────────────────────────
    # Auto-detect from Apify env, fall back to 4096MB (Apify default)
    MEMORY_LIMIT_MB: int = int(os.environ.get('APIFY_MEMORY_MBYTES', os.environ.get('MEMORY_LIMIT_MB', 4096)))


    # ── Proxy Health ──────────────────────────────────────────────
    PROXY_MAX_CONSECUTIVE_FAILURES: int = 3
    PROXY_COOLDOWN_SECONDS: int = 60
    PROXY_RETRY_COUNT: int = 2        # retries with different proxy on failure

    # ── Logging ───────────────────────────────────────────────────
    LOG_LEVEL: str = 'INFO'

    def __init__(self):
        """Load all config from environment, falling back to defaults."""
        # Proxy list
        proxy_env = os.environ.get('PROXY_LIST', '')
        if proxy_env:
            self.PROXY_LIST = [p.strip() for p in proxy_env.split(',') if p.strip()]
        else:
            # Try proxies.txt
            txt_path = Path(__file__).parent.parent / 'proxies.txt'
            if txt_path.exists():
                with open(txt_path, 'r') as f:
                    self.PROXY_LIST = [l.strip() for l in f if l.strip() and not l.startswith('#')]

        # Numeric overrides
        self.SEARCH_THREADS = int(os.environ.get('SEARCH_THREADS', self.SEARCH_THREADS))
        self.CHANNEL_CONCURRENCY = int(os.environ.get('CHANNEL_CONCURRENCY', self.CHANNEL_CONCURRENCY))
        self.CFFI_TIMEOUT = int(os.environ.get('CFFI_TIMEOUT', self.CFFI_TIMEOUT))
        self.PROXY_MAX_CONSECUTIVE_FAILURES = int(os.environ.get('PROXY_MAX_FAILURES', self.PROXY_MAX_CONSECUTIVE_FAILURES))
        self.PROXY_COOLDOWN_SECONDS = int(os.environ.get('PROXY_COOLDOWN_SECONDS', self.PROXY_COOLDOWN_SECONDS))
        self.PROXY_RETRY_COUNT = int(os.environ.get('PROXY_RETRY_COUNT', self.PROXY_RETRY_COUNT))
        self.LOG_LEVEL = os.environ.get('LOG_LEVEL', self.LOG_LEVEL).upper()

        # Setup logging
        self._setup_logging()

    def _setup_logging(self):
        """Configure structured logging."""
        log_format = '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
        logging.basicConfig(
            level=getattr(logging, self.LOG_LEVEL, logging.INFO),
            format=log_format,
            datefmt='%H:%M:%S',
        )
        # Silence noisy loggers
        logging.getLogger('urllib3').setLevel(logging.WARNING)
        logging.getLogger('aiohttp').setLevel(logging.WARNING)


# Singleton
config = Config()
