"""
ProxyManager — Thread-safe proxy rotation with health tracking.
================================================================
Features:
  • Thread-safe round-robin via Lock (no race conditions)
  • Per-proxy health tracking: success/fail counters, consecutive failures
  • Cooldown: quarantine bad proxies for configurable duration
  • Rate-limit detection: tracks 429s per proxy
  • Retry helper: retries requests with different proxy on failure
  • Weighted selection: healthy proxies preferred
"""
import time
import random
import logging
import threading
from dataclasses import dataclass, field
from typing import List, Optional, Callable, TypeVar, Any

logger = logging.getLogger('spider.proxy')

T = TypeVar('T')


@dataclass
class ProxyStats:
    """Health stats for a single proxy."""
    url: str
    successes: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    rate_limits: int = 0         # 429 / consent page count
    cooldown_until: float = 0.0  # timestamp when cooldown expires
    last_used: float = 0.0
    total_requests: int = 0

    @property
    def is_healthy(self) -> bool:
        """Proxy is available if not in cooldown."""
        return time.time() >= self.cooldown_until

    @property
    def success_rate(self) -> float:
        total = self.successes + self.failures
        return self.successes / total if total > 0 else 1.0

    def record_success(self):
        self.successes += 1
        self.consecutive_failures = 0
        self.total_requests += 1

    def record_failure(self, max_consecutive: int = 3, cooldown_secs: int = 60):
        self.failures += 1
        self.consecutive_failures += 1
        self.total_requests += 1
        if self.consecutive_failures == max_consecutive:
            self.cooldown_until = time.time() + cooldown_secs
            logger.debug(
                f"Proxy {self._masked_url} quarantined for {cooldown_secs}s "
                f"({self.consecutive_failures} consecutive failures)"
            )
        elif self.consecutive_failures > max_consecutive:
            # Refresh cooldown if it keeps failing
            self.cooldown_until = time.time() + cooldown_secs

    def record_rate_limit(self, max_consecutive: int = 3, cooldown_secs: int = 60):
        """Track 429 / consent page as a special failure type."""
        self.rate_limits += 1
        self.record_failure(max_consecutive, cooldown_secs)

    @property
    def _masked_url(self) -> str:
        """Mask credentials in URL for logging."""
        if '@' in self.url:
            # http://user:pass@ip:port → http://***@ip:port
            scheme_rest = self.url.split('@', 1)
            return scheme_rest[0].split('//')[0] + '//***@' + scheme_rest[1]
        return self.url


class ProxyManager:
    """Thread-safe proxy pool with health monitoring and rotation."""

    def __init__(self, proxy_strings: List[str],
                 max_consecutive_failures: int = 3,
                 cooldown_seconds: int = 60):
        self._lock = threading.Lock()
        self._index = 0
        self._max_consec = max_consecutive_failures
        self._cooldown_secs = cooldown_seconds
        self._last_quarantine_warn = 0.0

        # Parse proxy strings and create stats
        self._proxies: List[ProxyStats] = []
        for raw in proxy_strings:
            url = self._parse_proxy_string(raw)
            if url:
                self._proxies.append(ProxyStats(url=url))

        if self._proxies:
            logger.info(f"🌐 ProxyManager: loaded {len(self._proxies)} proxies")

    @staticmethod
    def _parse_proxy_string(raw: str) -> Optional[str]:
        """Parse ip:port:user:pass → http://user:pass@ip:port"""
        raw = raw.strip()
        if not raw:
            return None
        # Already a URL
        if raw.startswith('http://') or raw.startswith('https://') or raw.startswith('socks'):
            return raw
        parts = raw.split(':')
        if len(parts) == 4:
            ip, port, user, pw = parts
            return f"http://{user}:{pw}@{ip}:{port}"
        elif len(parts) == 2:
            return f"http://{parts[0]}:{parts[1]}"
        else:
            logger.warning(f"Cannot parse proxy string: {raw[:30]}...")
            return None

    @property
    def has_proxies(self) -> bool:
        return len(self._proxies) > 0

    @property
    def count(self) -> int:
        return len(self._proxies)

    def get_proxy(self) -> str:
        """Get next healthy proxy (weighted random based on success rate). Thread-safe.
        Returns empty string if no proxies available."""
        if not self._proxies:
            return ''

        with self._lock:
            # Filter for healthy proxies
            healthy = [p for p in self._proxies if p.is_healthy]
            
            if healthy:
                # Weighted selection: favor proxies with high success rates
                # +0.1 base weight ensures new/recovering proxies get a chance
                weights = [p.success_rate + 0.1 for p in healthy]
                selected = random.choices(healthy, weights=weights, k=1)[0]
                selected.last_used = time.time()
                return selected.url

            # All quarantined — return the one with earliest cooldown expiry
            best = min(self._proxies, key=lambda p: p.cooldown_until)
            best.cooldown_until = 0  # Force un-quarantine
            best.consecutive_failures = 0
            
            now = time.time()
            if getattr(self, '_last_quarantine_warn', 0) < now - 10:
                logger.warning("All proxies quarantined — force-releasing oldest (throttling this log for 10s)")
                self._last_quarantine_warn = now
                
            return best.url

    def get_random_proxy(self) -> str:
        """Get a random healthy proxy (for initial setup, not rotation)."""
        if not self._proxies:
            return ''
        healthy = [p for p in self._proxies if p.is_healthy]
        if not healthy:
            healthy = self._proxies
        return random.choice(healthy).url

    def report_success(self, proxy_url: str):
        """Report a successful request through this proxy."""
        if not proxy_url:
            return
        with self._lock:
            for p in self._proxies:
                if p.url == proxy_url:
                    p.record_success()
                    return

    def report_failure(self, proxy_url: str):
        """Report a failed request through this proxy."""
        if not proxy_url:
            return
        with self._lock:
            for p in self._proxies:
                if p.url == proxy_url:
                    p.record_failure(self._max_consec, self._cooldown_secs)
                    return

    def report_rate_limit(self, proxy_url: str):
        """Report a 429 / rate-limit / consent page through this proxy."""
        if not proxy_url:
            return
        with self._lock:
            for p in self._proxies:
                if p.url == proxy_url:
                    p.record_rate_limit(self._max_consec, self._cooldown_secs)
                    return

    def get_health_report(self) -> str:
        """Return a formatted health report of all proxies."""
        if not self._proxies:
            return "  No proxies configured (direct connection)"

        lines = []
        for i, p in enumerate(self._proxies):
            status = "✅" if p.is_healthy else f"🔴 (cooldown {p.cooldown_until - time.time():.0f}s)"
            lines.append(
                f"  Proxy {i+1}: {status} | "
                f"OK: {p.successes} | Fail: {p.failures} | "
                f"429s: {p.rate_limits} | "
                f"Rate: {p.success_rate:.0%}"
            )
        # Summary
        total_ok = sum(p.successes for p in self._proxies)
        total_fail = sum(p.failures for p in self._proxies)
        total_429 = sum(p.rate_limits for p in self._proxies)
        healthy_count = sum(1 for p in self._proxies if p.is_healthy)
        lines.append(f"  ── Total: {total_ok} OK / {total_fail} fail / {total_429} rate-limited | {healthy_count}/{len(self._proxies)} healthy")
        return '\n'.join(lines)

    def get_summary_line(self) -> str:
        """One-line summary for status reporter."""
        if not self._proxies:
            return "no proxies"
        healthy = sum(1 for p in self._proxies if p.is_healthy)
        total_429 = sum(p.rate_limits for p in self._proxies)
        return f"{healthy}/{len(self._proxies)} proxies healthy, {total_429} rate-limits"
