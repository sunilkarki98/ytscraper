"""
Maximum Speed Email Spider — v5.0 ZERO-DEPENDENCY ENGINE
============================================================
ARCHITECTURE: Streaming producer-consumer pipeline
  keyword_expander + search_producer → Queue → desc_fetch_workers → Email Extraction
  All stages run simultaneously via asyncio. Zero threads, zero yt-dlp.
"""
import asyncio
import platform
try:
    import resource  # Linux/Mac only
except ImportError:
    resource = None  # Windows — use ctypes fallback


import sys
import os
import re
import random
import json
import logging
import time
import string
import html as html_lib
import aiohttp
from curl_cffi.requests import AsyncSession as CffiSession
from typing import Any, List, Dict, Set, Optional, Tuple
from collections import deque
from urllib.parse import quote_plus

# ── Industry-grade modules ───────────────────────────────────────────
from engine.proxy_manager import ProxyManager
from engine.fingerprint import Fingerprint, BrowserProfile
from engine.email_validator import EmailValidator, extract_emails, extract_emails_async
from engine.config import config

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

# Note: builtins.print monkey-patch removed (audit finding — unsafe global override)

logger = logging.getLogger('spider')


# ─── Import from sub-modules (extracted from monolith) ──────────────
from engine.constants import SP_PARAMS, CONSENT_COOKIES
from engine.youtube_parser import (
    _fast_extract_yt_data, _parse_subscribers, _parse_view_count,
    _parse_duration_text, _unescape_json_string,
    extract_social_links, _find_keys, _find_first_val,
    _extract_from_ytInitialData, _extract_from_html_fallback,
)
from engine.youtube_search import yt_autocomplete
from engine.email_validator import compute_email_confidence


# ─── The Spider ──────────────────────────────────────────────────────

class MaxSpeedSpider:
    """
    v5.0 ZERO-DEPENDENCY ENGINE — PURE CURL_CFFI.

    FEATURES:
    • Streaming producer-consumer: search + fetch run simultaneously via asyncio.Queue
    • YouTube search: curl_cffi + ytInitialData parsing (zero threads, ~2MB/search)
    • Desc fetch: curl_cffi Chrome TLS (200 concurrent, 5s timeout)
    • Dynamic time budget: auto-detect Apify timeout, scale all timeouts
    • Keyword learning: track successful keywords, expand via autocomplete
    """

    def __init__(self, search_workers: int = None,
                 channel_workers: int = None,
                 filters: Dict = None,
                 min_subs_filter: int = 0, max_subs_filter: int = 0,
                 push_data_fn=None,
                 state: Dict = None,
                 run_id: str = '', seed_keyword: str = '',
                 time_budget: int = None):
        self.search_workers = search_workers or config.SEARCH_THREADS
        self.channel_workers = channel_workers or config.CHANNEL_CONCURRENCY
        self.filters = filters or {}
        self.min_subs_filter = min_subs_filter
        self.max_subs_filter = max_subs_filter
        self.push_data_fn = push_data_fn
        self._run_id = run_id
        self._seed_keyword = seed_keyword
        self._consecutive_innertube_fails = 0
        self._last_email_time = time.time()  # Stall detection
        self._adaptive_delay_base = config.REQUEST_DELAY_MIN  # Dynamic — increases on 429s
        # Per-instance time budget (avoids mutating global config)
        self._time_budget = time_budget or config.DEFAULT_TIME_BUDGET

        state = state or {}
        self.seen_channels: Set[str] = set(state.get('seen_channels', []))
        self.seen_emails: Set[str] = set(state.get('seen_emails', []))
        self.seen_queries: Set[str] = set(state.get('seen_queries', []))
        self._total_scraped = state.get('total_scraped', 0)
        self.results = []

        # Dynamic time budget — detect Apify timeout or use default
        self._deadline = 0.0  # Set in run()

        # Keyword success tracking (learning)
        self._keyword_hits: Dict[str, int] = state.get('keyword_hits', {})
        self._best_keywords: List[str] = state.get('best_keywords', [])[:100]  # Cap size

        # ── Proxy Manager (industry-grade) ──
        raw_proxies = self.filters.get('proxies', []) or config.PROXY_LIST
        single_proxy = self.filters.get('proxy', '')
        proxy_list = list(raw_proxies)
        if not proxy_list and single_proxy:
            proxy_list = [single_proxy]
        self.proxy_manager = ProxyManager(
            proxy_strings=proxy_list,
            max_consecutive_failures=config.PROXY_MAX_CONSECUTIVE_FAILURES,
            cooldown_seconds=config.PROXY_COOLDOWN_SECONDS,
        )

        default_stats = {
            'channels_scanned': 0,
            'desc_hits': 0,
            'desc_misses': 0,
            'yt_search_queries': 0,
            'yt_search_results': 0,
            'yt_search_errors': 0,
            # Error classification
            'errors_429': 0,
            'errors_5xx': 0,
            'errors_timeout': 0,
            'errors_parse': 0,
            # Extraction path tracking
            'extract_ytInitialData': 0,
            'extract_html_fallback': 0,
            'extract_failed': 0,
            # Social link coverage
            'results_with_socials': 0,
            'total_social_links': 0,
        }
        self.stats = state.get('stats', default_stats)

    def get_state(self) -> Dict:
        """Returns a serializable dictionary of the spider's current state."""
        return {
            'seen_channels': list(self.seen_channels),
            'seen_emails': list(self.seen_emails),
            'seen_queries': list(self.seen_queries),
            'total_scraped': self._total_scraped,
            'keyword_hits': self._keyword_hits,
            'best_keywords': self._best_keywords,
            'stats': self.stats,
        }

    def _next_proxy(self) -> str:
        """Thread-safe proxy rotation via ProxyManager. Returns empty string if no proxies."""
        return self.proxy_manager.get_proxy()

    def _time_left(self) -> float:
        """Seconds remaining until deadline. All timeouts use this."""
        return max(0, self._deadline - time.time())

    def _should_continue(self, reserve: float = 10) -> bool:
        """Should we start a new operation? Reserves time for cleanup."""
        return self._time_left() > reserve

    def _get_sp_param(self) -> str:
        """Build sp= parameter from filters.
        
        YouTube's sp param doesn't support combining multiple filters via
        concatenation. We use a priority order: upload_date > sort > duration
        > type > features > exact_match. The highest-priority match wins.
        """
        filters = self.filters

        # Priority order: upload_date filter is most impactful for email discovery
        for filter_key, sp_prefix in [('upload_date', 'date_'), ('duration', 'dur_')]:
            val = filters.get(filter_key)
            if val and val != 'any':
                key = f"{sp_prefix}{val}"
                if key in SP_PARAMS:
                    return SP_PARAMS[key]

        sort_by = filters.get('sort_by', 'relevance')
        sort_map = {
            'upload_date': 'sort_upload_date',
            'view_count': 'sort_view_count',
            'rating': 'sort_rating',
        }
        if sort_by in sort_map:
            return SP_PARAMS[sort_map[sort_by]]

        content_type = filters.get('type') or filters.get('content_type')
        if content_type and content_type != 'any':
            key = f"type_{content_type}"
            if key in SP_PARAMS:
                return SP_PARAMS[key]

        features = filters.get('features', [])
        if isinstance(features, str):
            features = [features] if features else []
        for feat in features:
            key = f"feat_{feat}"
            if key in SP_PARAMS:
                return SP_PARAMS[key]

        if filters.get('exact_match'):
            return SP_PARAMS['exact_match']

        return ''

    def _passes_sub_filter(self, subscribers: int) -> bool:
        if self.min_subs_filter > 0 and subscribers < self.min_subs_filter:
            return False
        if self.max_subs_filter > 0 and subscribers > self.max_subs_filter:
            return False
        return True

    def _passes_search_filters(self, ch: Dict) -> bool:
        """Apply post-search filters (min_views, min/max duration, likes, comments)."""
        filters = self.filters
        min_views = int(filters.get('min_views', 0))
        if min_views > 0 and ch.get('views', 0) < min_views:
            return False
        min_dur = int(filters.get('min_duration', 0))
        if min_dur > 0 and ch.get('duration_secs', 0) < min_dur:
            return False
        max_dur = int(filters.get('max_duration', 0))
        if max_dur > 0 and ch.get('duration_secs', 0) > max_dur:
            return False
        min_likes = int(filters.get('min_likes', 0))
        if min_likes > 0 and ch.get('likes', 0) < min_likes:
            return False
        min_comments = int(filters.get('min_comments', 0))
        if min_comments > 0 and ch.get('comments', 0) < min_comments:
            return False
        return True

    def _get_memory_mb(self) -> float:
        """Get current process memory usage in MB."""
        try:
            if platform.system() == 'Windows':
                import ctypes
                kernel32 = ctypes.windll.kernel32
                class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                    _fields_ = [("cb", ctypes.c_ulong),
                                ("PageFaultCount", ctypes.c_ulong),
                                ("PeakWorkingSetSize", ctypes.c_size_t),
                                ("WorkingSetSize", ctypes.c_size_t),
                                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                                ("PagefileUsage", ctypes.c_size_t),
                                ("PeakPagefileUsage", ctypes.c_size_t)]
                pmc = PROCESS_MEMORY_COUNTERS()
                pmc.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
                handle = kernel32.GetCurrentProcess()
                ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb)
                return pmc.WorkingSetSize / (1024 * 1024)
            else:
                # Linux/Mac — use resource module
                usage_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                if platform.system() == 'Darwin':
                    return usage_kb / (1024 * 1024)  # bytes on macOS
                return usage_kb / 1024  # KB on Linux
        except Exception:
            return 0.0

    def _is_memory_pressure(self) -> bool:
        """Check if memory usage is above 90% of configured limit."""
        mem_mb = self._get_memory_mb()
        threshold = config.MEMORY_LIMIT_MB * 0.90
        return mem_mb > threshold

    def _get_adaptive_delay(self) -> float:
        """Get current adaptive delay with jitter. Increases when YouTube rate-limits."""
        base = self._adaptive_delay_base
        jitter = base * 0.5  # ±50% jitter
        return random.uniform(base, base + jitter)

    def _update_adaptive_delay(self):
        """Adjust delay based on 429 error rate. Called on each 429 response."""
        total_fetches = max(self.stats.get('desc_hits', 0) + self.stats.get('desc_misses', 0), 1)
        error_429 = self.stats.get('errors_429', 0)
        rate_429 = error_429 / total_fetches

        if rate_429 > 0.50:
            self._adaptive_delay_base = 8.0   # Heavy throttle
        elif rate_429 > 0.30:
            self._adaptive_delay_base = 3.0   # Moderate throttle
        elif rate_429 > 0.10:
            self._adaptive_delay_base = 1.0   # Light throttle
        else:
            self._adaptive_delay_base = config.REQUEST_DELAY_MIN  # Full speed

    async def _add_email(self, email: str, result: Dict, channel: Dict):
        ch_id = channel.get('channel_id', result.get('channel_id', ''))
        subs = result.get('subscribers', 0)
        social = result.get('social_links', {})

        # Track social link coverage
        if social:
            self.stats['results_with_socials'] += 1
            self.stats['total_social_links'] += len(social)

        # Compute multi-factor confidence score (MX + pattern + authority + social)
        mx_valid = result.get('mx_verified', True)  # MX was checked during extraction
        confidence = compute_email_confidence(
            email=email, subscribers=subs,
            social_links=social, mx_valid=mx_valid,
        )

        row = {
            'email': email.strip(),
            'channelName': result.get('name', channel.get('channel_name', 'Unknown')).strip(),
            'channelUrl': channel.get('channel_url', f"https://www.youtube.com/channel/{ch_id}"),
            'channelId': ch_id,
            'subscribers': subs,
            'confidence': confidence,
            'source': 'description',
            'extractedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'searchKeyword': self._seed_keyword,
            'instagram': social.get('instagram', ''),
            'twitter': social.get('twitter', ''),
            'tiktok': social.get('tiktok', ''),
            'facebook': social.get('facebook', ''),
            'linkedin': social.get('linkedin', ''),
            'website': social.get('website', ''),
        }
        self._total_scraped += 1
        
        # Push to Apify and track streaming count
        if self.push_data_fn:
            try:
                await self.push_data_fn([row])
                if self._total_scraped % 25 == 0:
                    print(f"  [LAGIC] 📤 Streaming: {self._total_scraped} total emails pushed")
            except Exception:
                pass
        print(f"     📧 [{self._total_scraped}] {email} — "
              f"{result.get('name', '?')} ({subs:,} subs)"
              f"{' 📱' + ','.join(social.keys()) if social else ''}")

    # ─── NATIVE CURL_CFFI SEARCH ENGINE ─────────────────────────────

    def _parse_search_items(self, item_section: list, seen_ch: set) -> Tuple[List[Dict], List[str]]:
        """Parse video/channel items from a search result section.
        Uses recursive schema fallbacks to survive YouTube UI changes."""
        channels = []
        titles = []
        for item in item_section:
            # 1. Resilient ID Extraction
            ch_id = _find_first_val(item, 'browseId') or _find_first_val(item, 'channelId')
            if not ch_id or ch_id in seen_ch or ch_id in self.seen_channels:
                continue
            seen_ch.add(ch_id)

            # 2. Extract all text content heuristically
            all_texts = _find_keys(item, {'text', 'simpleText'})
            valid_texts = [str(t) for t in all_texts if isinstance(t, str) and len(t) > 0]
            
            if not valid_texts:
                continue

            # First large text is usually title, owner is usually in there too
            title = valid_texts[0] if 'videoRenderer' in item else ''
            if title:
                titles.append(title)
                
            views = 0
            duration_secs = 0
            channel_name = valid_texts[1] if len(valid_texts) > 1 else ''

            for t in valid_texts:
                t_lower = t.lower()
                if 'views' in t_lower or 'view' in t_lower:
                    v = _parse_view_count(t)
                    if v > views: views = v
                elif ':' in t and len(t) < 10:
                    d = _parse_duration_text(t)
                    if d > duration_secs: duration_secs = d
                # Heuristically find channel name if it wasn't the second text
                elif 'subscribers' in t_lower:
                    pass

            ch_data = {
                'channel_id': ch_id,
                'channel_name': channel_name,  # Best effort
                'channel_url': f"https://www.youtube.com/channel/{ch_id}",
                'video_title': title,
                'views': views,
                'duration_secs': duration_secs,
                'source': 'youtube',
            }

            if self._passes_search_filters(ch_data):
                channels.append(ch_data)
                
        return channels, titles

    def _extract_continuation_token(self, contents: list) -> Optional[str]:
        """Extract continuation token from search results for pagination."""
        for content in contents:
            cont = content.get('continuationItemRenderer', {})
            endpoint = cont.get('continuationEndpoint', {})
            token = endpoint.get('continuationCommand', {}).get('token', '')
            if token:
                return token
        return None

    async def _fetch_continuation(self, cffi_session, sem: asyncio.Semaphore,
                                   token: str, seen_ch: set) -> Tuple[List[Dict], List[str], Optional[str]]:
        """Fetch next page of search results via YouTube's browse API (100% JSON)."""
        url = f"https://www.youtube.com/youtubei/v1/search?key={config.INNERTUBE_API_KEY}"
        body = {
            "context": {
                "client": {
                    "clientName": "WEB",
                    "clientVersion": "2.20240101.00.00",
                    "hl": self.filters.get('language', 'en'),
                    "gl": self.filters.get('country', 'US'),
                }
            },
            "continuation": token,
        }
        async with sem:
            proxy = self._next_proxy()
            try:
                proxy_kwargs = {'proxy': proxy} if proxy else {}
                resp = await cffi_session.post(
                    url,
                    json=body,
                    timeout=config.CFFI_TIMEOUT,
                    headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'},
                    cookies=CONSENT_COOKIES,
                    **proxy_kwargs,
                )
                if resp.status_code != 200:
                    return [], [], None
                data = resp.json()
            except Exception:
                return [], [], None

        channels = []
        titles = []
        next_token = None
        try:
            # Resilient continuation parsing
            item_sections = _find_keys(data, {'itemSectionRenderer', 'appendContinuationItemsAction'})
            for section in item_sections:
                items = section.get('contents') or section.get('continuationItems') or []
                if items:
                    c, t = self._parse_search_items(items, seen_ch)
                    channels.extend(c)
                    titles.extend(t)

            commands = _find_keys(data, {'continuationCommand'})
            for cmd in commands:
                t = cmd.get('token')
                if t:
                    next_token = t
                    break
        except Exception:
            pass
        return channels, titles, next_token

    async def _html_search_fallback(self, cffi_session, search_sem: asyncio.Semaphore, query: str) -> Tuple[List[Dict], List[str]]:
        """
        HTML-based YouTube search fallback.
        Used when InnerTube API returns empty results (datacenter IP blocking).
        Fetches the full search page HTML and parses ytInitialData.
        """
        from urllib.parse import quote_plus as qp
        url = f"https://www.youtube.com/results?search_query={qp(query)}"
        sp = self._get_sp_param()
        if sp:
            url += f"&sp={sp}"

        max_retries = config.PROXY_RETRY_COUNT + 1
        page_html = None

        for attempt in range(max_retries):
            async with search_sem:
                profile = Fingerprint.get_profile()
                proxy = self._next_proxy()
                try:
                    proxy_kwargs = {'proxy': proxy} if proxy else {}
                    headers = Fingerprint.get_headers(profile)
                    headers['Referer'] = 'https://www.youtube.com/'
                    resp = await cffi_session.get(
                        url,
                        timeout=config.CFFI_TIMEOUT,
                        headers=headers,
                        cookies=CONSENT_COOKIES,
                        allow_redirects=True,
                        max_redirects=3,
                        **proxy_kwargs,
                    )
                    if resp.status_code == 429:
                        if proxy:
                            self.proxy_manager.report_rate_limit(proxy)
                        self.stats.setdefault('diag_search_429', 0)
                        self.stats['diag_search_429'] += 1
                        await asyncio.sleep(0.1 * (2 ** attempt))
                        continue
                    if resp.status_code != 200:
                        if proxy:
                            self.proxy_manager.report_failure(proxy)
                        continue
                    page_html = resp.text
                    if proxy:
                        self.proxy_manager.report_success(proxy)
                    break
                except Exception:
                    if proxy:
                        self.proxy_manager.report_failure(proxy)
                    if attempt < max_retries - 1:
                        await asyncio.sleep(0.1 * (2 ** attempt))
                        continue
                    return [], []

        if not page_html or len(page_html) < 1000:
            return [], []

        # Extract ytInitialData from the HTML
        raw_json = _fast_extract_yt_data(page_html)
        if not raw_json:
            return [], []

        try:
            yt_data = json.loads(raw_json)
        except json.JSONDecodeError:
            return [], []

        channels = []
        titles = []
        seen_ch = set()

        try:
            sections = _find_keys(yt_data, {'itemSectionRenderer'})
            for sec in sections:
                contents = sec.get('contents', [])
                if contents:
                    c, t = self._parse_search_items(contents, seen_ch)
                    channels.extend(c)
                    titles.extend(t)
        except Exception:
            pass

        self.stats.setdefault('diag_html_search_used', 0)
        self.stats['diag_html_search_used'] += 1
        self.stats.setdefault('yt_search_queries', 0)
        self.stats.setdefault('yt_search_results', 0)
        self.stats['yt_search_queries'] += 1
        self.stats['yt_search_results'] += len(channels)

        return channels, titles

    async def _curl_search(self, cffi_session, search_sem: asyncio.Semaphore, query: str) -> Tuple[List[Dict], List[str]]:
        """
        100% GraphQL/InnerTube API YouTube search.
        Fetches pure JSON (kilobytes) instead of full HTML (megabytes).
        Falls back to HTML search if InnerTube returns empty results.
        """
        if self._consecutive_innertube_fails >= 5:
            # InnerTube is persistently blocked (Apify Datacenter IPs).
            # Skip wasting time on retries and go straight to HTML fallback.
            return await self._html_search_fallback(cffi_session, search_sem, query)

        sp = self._get_sp_param()
        url = f"https://www.youtube.com/youtubei/v1/search?key={config.INNERTUBE_API_KEY}"
        body = {
            "context": {
                "client": {
                    "clientName": "WEB",
                    "clientVersion": "2.20240101.00.00",
                    "hl": self.filters.get('language', 'en'),
                    "gl": self.filters.get('country', 'US'),
                }
            },
            "query": query,
        }
        if sp:
            body["params"] = sp

        max_retries = config.PROXY_RETRY_COUNT + 1
        yt_data = None

        for attempt in range(max_retries):
            async with search_sem:
                profile = Fingerprint.get_profile()
                proxy = self._next_proxy()
                try:
                    proxy_kwargs = {'proxy': proxy} if proxy else {}
                    resp = await cffi_session.post(
                        url,
                        json=body,
                        timeout=config.CFFI_TIMEOUT,
                        headers={'Content-Type': 'application/json', 'User-Agent': profile.user_agent},
                        cookies=CONSENT_COOKIES,
                        **proxy_kwargs,
                    )
                    if resp.status_code == 429:
                        if proxy:
                            self.proxy_manager.report_rate_limit(proxy)
                        self.stats.setdefault('diag_search_429', 0)
                        self.stats['diag_search_429'] += 1
                        await asyncio.sleep(0.1 * (2 ** attempt))
                        continue
                    if resp.status_code != 200:
                        if proxy:
                            self.proxy_manager.report_failure(proxy)
                        self.stats.setdefault('diag_search_http_err', 0)
                        self.stats['diag_search_http_err'] += 1
                        continue
                    
                    yt_data = resp.json()
                    if proxy:
                        self.proxy_manager.report_success(proxy)
                    break
                except Exception:
                    if proxy:
                        self.proxy_manager.report_failure(proxy)
                    self.stats.setdefault('diag_search_exception', 0)
                    self.stats['diag_search_exception'] += 1
                    if attempt < max_retries - 1:
                        await asyncio.sleep(0.1 * (2 ** attempt))
                        continue
                    return [], []

        if not yt_data:
            # InnerTube failed completely — try HTML fallback
            self._consecutive_innertube_fails += 1
            self.stats.setdefault('diag_innertube_no_data', 0)
            self.stats['diag_innertube_no_data'] += 1
            return await self._html_search_fallback(cffi_session, search_sem, query)

        channels = []
        titles = []
        seen_ch = set()
        continuation_token = None

        try:
            # 1. Resilient schema fallback for initial page search items
            sections = _find_keys(yt_data, {'itemSectionRenderer'})
            for sec in sections:
                contents = sec.get('contents', [])
                if contents:
                    c, t = self._parse_search_items(contents, seen_ch)
                    channels.extend(c)
                    titles.extend(t)

            # 2. Extract continuation token resiliently
            commands = _find_keys(yt_data, {'continuationCommand'})
            for cmd in commands:
                token = cmd.get('token')
                if token:
                    continuation_token = token
                    break
        except Exception as e:
            self.stats['yt_search_errors'] += 1
            logger.debug(f"Search parse error: {e}")

        # If InnerTube returned data but 0 channels — likely datacenter IP block
        if not channels:
            self._consecutive_innertube_fails += 1
            self.stats.setdefault('diag_innertube_empty', 0)
            self.stats['diag_innertube_empty'] += 1
            # Fall back to HTML search
            return await self._html_search_fallback(cffi_session, search_sem, query)

        # InnerTube succeeded! Reset the failure counter.
        self._consecutive_innertube_fails = 0

        # Follow continuation tokens for pages 2-3 (if time permits)
        MAX_CONTINUATION_PAGES = 2
        for page_num in range(MAX_CONTINUATION_PAGES):
            if not continuation_token or not self._should_continue(reserve=20):
                break
            try:
                cont_channels, cont_titles, next_token = await self._fetch_continuation(
                    cffi_session, search_sem, continuation_token, seen_ch
                )
                channels.extend(cont_channels)
                titles.extend(cont_titles)
                continuation_token = next_token
            except Exception:
                break

        self.stats.setdefault('yt_search_queries', 0)
        self.stats.setdefault('yt_search_results', 0)
        self.stats['yt_search_queries'] += 1
        self.stats['yt_search_results'] += len(channels)
        
        return channels, titles


    # ─── CHANNEL DESCRIPTIONS: InnerTube browse (fast) → HTML page (reliable) ──

    async def _fetch_channel_desc(self, cffi_session,
                                   channel_sem: asyncio.Semaphore,
                                   channel_id: str,
                                   profile: BrowserProfile) -> Optional[Dict]:
        """Fetch channel description.
        
        Strategy: Try InnerTube browse first (fast, lightweight).
        If it fails (datacenter IP blocked), fall back to HTML page load.
        Uses adaptive rate limiting to avoid 429 storms.
        """
        # Stage 1: Try InnerTube browse API (works locally, may be blocked on datacenter)
        if not getattr(self, '_innertube_browse_blocked', False):
            result = await self._try_innertube_browse(cffi_session, channel_sem, channel_id, profile)
            if result:
                result['channel_id'] = channel_id
                self.stats['extract_ytInitialData'] += 1
                return result

        # Stage 2: HTML page load (proven to work on Apify datacenter IPs)
        result = await self._fetch_html_page(cffi_session, channel_sem, channel_id, profile)
        if result:
            result['channel_id'] = channel_id
            self.stats['extract_html_fallback'] += 1
            return result

        self.stats['extract_failed'] += 1
        return None

    async def _try_innertube_browse(self, cffi_session,
                                     channel_sem: asyncio.Semaphore,
                                     channel_id: str,
                                     profile: BrowserProfile) -> Optional[Dict]:
        """Try InnerTube browse API. Auto-disables after 10 consecutive failures."""
        url = f"https://www.youtube.com/youtubei/v1/browse?key={config.INNERTUBE_API_KEY}"
        body = {
            "context": {
                "client": {
                    "clientName": "WEB",
                    "clientVersion": "2.20240101.00.00",
                    "hl": self.filters.get('language', 'en'),
                    "gl": self.filters.get('country', 'US'),
                }
            },
            "browseId": channel_id,
        }

        async with channel_sem:
            proxy = self._next_proxy()
            try:
                proxy_kwargs = {'proxy': proxy} if proxy else {}
                resp = await cffi_session.post(
                    url, json=body, timeout=config.CFFI_TIMEOUT,
                    headers={'Content-Type': 'application/json',
                             'User-Agent': profile.user_agent,
                             'Referer': 'https://www.youtube.com/'},
                    cookies=CONSENT_COOKIES,
                    **proxy_kwargs,
                )
                if resp.status_code != 200:
                    if proxy:
                        self.proxy_manager.report_failure(proxy)
                    self._innertube_browse_fails = getattr(self, '_innertube_browse_fails', 0) + 1
                    if self._innertube_browse_fails >= 10:
                        self._innertube_browse_blocked = True
                        logger.warning(f"  ⚠️ InnerTube browse blocked — switching to HTML page fetch")
                    return None
                yt_data = resp.json()
                if proxy:
                    self.proxy_manager.report_success(proxy)
            except Exception:
                if proxy:
                    self.proxy_manager.report_failure(proxy)
                return None

        try:
            result = _extract_from_ytInitialData(yt_data)
            if result and result.get('description'):
                self._innertube_browse_fails = 0  # Reset on success
                return result
            else:
                # Got 200 but no description — API blocked silently
                self._innertube_browse_fails = getattr(self, '_innertube_browse_fails', 0) + 1
                if self._innertube_browse_fails >= 10:
                    self._innertube_browse_blocked = True
                    logger.warning(f"  ⚠️ InnerTube browse returning empty — switching to HTML page fetch")
                return None
        except Exception:
            self.stats['errors_parse'] += 1
            return None

    async def _fetch_html_page(self, cffi_session,
                                channel_sem: asyncio.Semaphore,
                                channel_id: str,
                                profile: BrowserProfile) -> Optional[Dict]:
        """Fetch full channel HTML page and extract description.
        Proven to work on Apify datacenter IPs with adaptive rate limiting."""
        url = f"https://www.youtube.com/channel/{channel_id}"
        max_retries = config.PROXY_RETRY_COUNT + 1

        page_html = None
        for attempt in range(max_retries):
            delay = self._get_adaptive_delay()
            await asyncio.sleep(delay)
            async with channel_sem:
                proxy = self._next_proxy()
                try:
                    proxy_kwargs = {'proxy': proxy} if proxy else {}
                    headers = Fingerprint.get_headers(profile)
                    headers['Referer'] = 'https://www.youtube.com/'
                    resp = await cffi_session.get(
                        url, timeout=config.CFFI_TIMEOUT,
                        headers=headers, cookies=CONSENT_COOKIES,
                        allow_redirects=True, max_redirects=3,
                        **proxy_kwargs,
                    )
                    if resp.status_code == 429:
                        if proxy:
                            self.proxy_manager.report_rate_limit(proxy)
                        self.stats['errors_429'] += 1
                        self._update_adaptive_delay()
                        await asyncio.sleep(5.0 * (2 ** attempt))
                        continue
                    if resp.status_code >= 500:
                        if proxy:
                            self.proxy_manager.report_failure(proxy)
                        self.stats['errors_5xx'] += 1
                        continue
                    if resp.status_code != 200:
                        if proxy:
                            self.proxy_manager.report_failure(proxy)
                        continue
                    page_html = resp.text
                    if proxy:
                        self.proxy_manager.report_success(proxy)
                except asyncio.TimeoutError:
                    if proxy:
                        self.proxy_manager.report_failure(proxy)
                    self.stats['errors_timeout'] += 1
                    if attempt < max_retries - 1:
                        continue
                    return None
                except Exception:
                    if proxy:
                        self.proxy_manager.report_failure(proxy)
                    if attempt < max_retries - 1:
                        continue
                    return None
            break  # success

        if not page_html or len(page_html) < 1000:
            return None

        # Try structured JSON extraction first (fast path)
        raw_json = _fast_extract_yt_data(page_html)
        if raw_json:
            try:
                yt_data = json.loads(raw_json)
                result = _extract_from_ytInitialData(yt_data)
                if result:
                    return result
            except json.JSONDecodeError:
                self.stats['errors_parse'] += 1

        # Regex fallback for when JSON parse fails
        return _extract_from_html_fallback(page_html)


    # ─── MAIN RUN LOOP — STREAMING PIPELINE ──────────────────────────

    async def run(self, seed_keyword: str, max_emails: int = 500,
                  titles_per_round: int = 30, videos_per_search: int = 100):
        start_time = time.time()

        # ── Dynamic Time Budget ──────────────────────────────────────
        apify_timeout_at = os.environ.get('APIFY_TIMEOUT_AT')
        if apify_timeout_at:
            try:
                self._deadline = float(apify_timeout_at) / 1000  # ms -> s
            except (ValueError, TypeError):
                self._deadline = start_time + self._time_budget
        else:
            self._deadline = start_time + self._time_budget
        total_budget = self._deadline - start_time
        print(f"""
  ██╗      █████╗  ██████╗ ██╗ ██████╗
  ██║     ██╔══██╗██╔════╝ ██║██╔════╝
  ██║     ███████║██║  ███╗██║██║     
  ██║     ██╔══██║██║   ██║██║██║     
  ███████╗██║  ██║╚██████╔╝██║╚██████╗
  ╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚═╝ ╚═════╝
  YouTube Email Spider v5.0
{'='*60}""")
        print(f"  🚀 Keyword: '{seed_keyword}' | Target: {max_emails} emails")
        print(f"  ⏱️  Time budget: {total_budget:.0f}s ({total_budget/60:.1f} min)")
        print(f"  🔧 Search: {self.search_workers} concurrent | Desc: {self.channel_workers} concurrent")
        print(f"  🔧 curl_cffi: {config.CFFI_TIMEOUT}s timeout | CFFI clients: {config.CFFI_MAX_CLIENTS}")
        print(f"{'='*60}")

        # ── Shared state for streaming pipeline ──
        channel_queue = asyncio.Queue(maxsize=config.CHANNEL_QUEUE_SIZE)  # Backpressure prevents memory bloat
        stop_event = asyncio.Event()     # Signal all producers to stop

        # Keyword management
        keyword_queue = deque()        # Keywords ready to search
        autocomplete_pending = deque() # Keywords to expand via autocomplete

        # ── US-focused seed keywords for maximum coverage ──
        us_niches = [
            f"{seed_keyword} tutorial", f"{seed_keyword} tips",
            f"{seed_keyword} for beginners", f"{seed_keyword} 2024",
            f"{seed_keyword} strategies", f"how to {seed_keyword}",
            f"best {seed_keyword}", f"{seed_keyword} online",
            f"{seed_keyword} business", f"{seed_keyword} course",
        ]

        # Seed the queue IMMEDIATELY — search starts before autocomplete finishes
        keyword_queue.append(seed_keyword)
        for niche in us_niches:
            keyword_queue.append(niche)
        print(f"  Seeded {len(keyword_queue)} keywords — searching starts NOW")

        # ── Autocomplete expansion runs IN PARALLEL with searching ──
        conn = aiohttp.TCPConnector(limit=20, ttl_dns_cache=120, force_close=False)
        async with aiohttp.ClientSession(connector=conn) as session:
            search_sem = asyncio.Semaphore(self.search_workers)  # Separate for search
            fetch_sem = asyncio.Semaphore(self.channel_workers)   # Separate for desc fetch

            async def keyword_expander():
                """Runs in parallel with search — continuously expands keywords via autocomplete."""
                # Phase 1: Expand seed keyword with all 26 letters
                ac_tasks = [yt_autocomplete(session, seed_keyword)]
                for c in 'abcdefghijklmnopqrstuvwxyz':
                    ac_tasks.append(yt_autocomplete(session, seed_keyword + ' ' + c))
                # Also expand US niches
                for niche in us_niches:
                    ac_tasks.append(yt_autocomplete(session, niche))

                ac_results = await asyncio.gather(*ac_tasks, return_exceptions=True)
                added = 0
                for result in ac_results:
                    if isinstance(result, list):
                        for suggestion in result:
                            if suggestion not in self.seen_queries:
                                keyword_queue.append(suggestion)
                                autocomplete_pending.append(suggestion)
                                added += 1
                print(f"  Autocomplete expanded: +{added} keywords (total: {len(keyword_queue)})")

                # Phase 2: Continuous expansion — keep feeding keywords as long as pipeline runs
                while not stop_event.is_set():
                    await asyncio.sleep(2)
                    if stop_event.is_set():
                        break
                    # Expand best-performing and pending keywords
                    expand_batch = []
                    while autocomplete_pending and len(expand_batch) < 10:
                        expand_batch.append(autocomplete_pending.popleft())
                    while self._best_keywords and len(expand_batch) < 15:
                        best = self._best_keywords.pop(0)
                        if best not in self.seen_queries:
                            expand_batch.append(best)
                    if not expand_batch:
                        continue
                    tasks = [yt_autocomplete(session, kw) for kw in expand_batch]
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    for r in results:
                        if isinstance(r, list):
                            for s in r:
                                if s not in self.seen_queries:
                                    keyword_queue.append(s)
                                    autocomplete_pending.append(s)

            async def yt_search_producer():
                """Continuously search YouTube via native curl_cffi and feed channels to queue."""
                profile = Fingerprint.get_profile()
                async with CffiSession(impersonate=profile.impersonate_target, max_clients=config.CFFI_MAX_CLIENTS) as cffi:
                    batch_num = 0
                    while not stop_event.is_set():
                        if self._total_scraped >= max_emails:
                            stop_event.set()
                            break
                        if not self._should_continue(reserve=15):
                            print(f"  Time's up, {self._time_left():.0f}s left")
                            stop_event.set()
                            break
                        if batch_num >= config.MAX_SEARCH_BATCHES:
                            print(f"  Max search batches ({config.MAX_SEARCH_BATCHES}) reached")
                            stop_event.set()
                            break
                        # Memory-aware throttling
                        if self._is_memory_pressure():
                            mem_mb = self._get_memory_mb()
                            print(f"  ⚠️ Memory pressure: {mem_mb:.0f}MB / {config.MEMORY_LIMIT_MB}MB — throttling")
                            await asyncio.sleep(2)  # Let fetch workers drain the queue

                        # Queue backpressure — don't pump more channels if fetch can't keep up
                        if channel_queue.qsize() >= int(config.CHANNEL_QUEUE_SIZE * 0.8):
                            await asyncio.sleep(3)  # Let fetch workers drain
                            continue

                        # Stall detection — if no new emails for 90s, YouTube is blocking desc fetches
                        time_since_last_email = time.time() - self._last_email_time
                        if time_since_last_email > 90 and self._total_scraped > 0:
                            print(f"  ⚠️ No new emails for {time_since_last_email:.0f}s — desc fetch likely rate-limited, stopping search")
                            stop_event.set()
                            break

                        # Gather a batch of keywords
                        batch = []
                        while keyword_queue and len(batch) < self.search_workers:
                            kw = keyword_queue.popleft()
                            if kw not in self.seen_queries:
                                self.seen_queries.add(kw)
                                batch.append(kw)

                        if not batch:
                            # Inline refill — multi-letter prefix cycling
                            for a in string.ascii_lowercase:
                                combo = f"{seed_keyword} {a}"
                                if combo not in self.seen_queries:
                                    keyword_queue.append(combo)
                            # Also try title combos
                            seen_list = list(self.seen_queries)
                            for i in range(min(20, len(seen_list))):
                                kw = seen_list[-(i+1)]
                                for c in 'abcde':
                                    combo = f"{kw} {c}"
                                    if combo not in self.seen_queries:
                                        keyword_queue.append(combo)
                                        if len(keyword_queue) >= 20:
                                            break
                                if len(keyword_queue) >= 20:
                                    break
                            if keyword_queue:
                                continue
                            await asyncio.sleep(0.3)
                            continue

                        batch_num += 1
                        t0 = time.time()

                        # Fire all searches and STREAM results as they arrive
                        search_tasks = [
                            self._curl_search(cffi, search_sem, q)
                            for q in batch
                        ]

                        new_channels = 0
                        new_titles = []
                        for coro in asyncio.as_completed(search_tasks):
                            try:
                                r = await coro
                            except Exception:
                                continue
                            channels, titles = r
                            for ch in channels:
                                if ch['channel_id'] not in self.seen_channels:
                                    self.seen_channels.add(ch['channel_id'])
                                    await channel_queue.put(ch)
                                    new_channels += 1
                            new_titles.extend(titles[:titles_per_round])

                        elapsed_batch = time.time() - t0
                        print(f"  🔍 YT batch {batch_num}: {len(batch)} queries → {new_channels} channels in {elapsed_batch:.1f}s ({self._time_left():.0f}s left)")

                        # Warn if getting zero results (datacenter block detection)
                        if new_channels == 0:
                            empty_count = self.stats.get('diag_innertube_empty', 0) + self.stats.get('diag_innertube_no_data', 0)
                            html_used = self.stats.get('diag_html_search_used', 0)
                            print(f"  ⚠️ Batch {batch_num} returned 0 channels! "
                                  f"(InnerTube empty: {self.stats.get('diag_innertube_empty', 0)}, "
                                  f"HTML fallback: {html_used})")

                        # Feed new titles back as keywords
                        for title in new_titles:
                            if title not in self.seen_queries:
                                keyword_queue.append(title)

                        # Track keyword success
                        if new_channels:
                            for kw in batch:
                                self._keyword_hits[kw] = self._keyword_hits.get(kw, 0) + new_channels

                # Signal we're done producing YT channels
                print(f"  🔍 YT search complete: {self.stats['yt_search_queries']} queries, "
                      f"{self.stats['yt_search_results']} channels")


            async def desc_fetch_worker(worker_id: int, shared_cffi):
                """Pull channels from queue, fetch descriptions via curl_cffi, extract emails."""
                profile = Fingerprint.get_profile()
                cffi = shared_cffi
                while not stop_event.is_set():
                    if self._total_scraped >= max_emails:
                        stop_event.set()
                        break

                    # Get a batch of channels from the queue
                    batch = []
                    try:
                        # Wait for first channel (with timeout to check stop condition)
                        ch = await asyncio.wait_for(channel_queue.get(), timeout=1)
                        batch.append(ch)
                        # Grab more if available (up to 10 at a time)
                        while len(batch) < 10:
                            try:
                                ch = channel_queue.get_nowait()
                                batch.append(ch)
                            except asyncio.QueueEmpty:
                                break
                    except asyncio.TimeoutError:
                        continue

                    if not batch:
                        continue

                    # Fetch descriptions for this batch
                    async def fetch_one(ch):
                        result = await self._fetch_channel_desc(cffi, fetch_sem, ch['channel_id'], profile)
                        return (ch, result)

                    tasks = [fetch_one(ch) for ch in batch]
                    raw = await asyncio.gather(*tasks, return_exceptions=True)

                    for item in raw:
                        if isinstance(item, Exception):
                            self.stats['channels_scanned'] += 1
                            self.stats['desc_misses'] += 1
                            continue
                        ch, result = item
                        self.stats['channels_scanned'] += 1
                        if result is None:
                            self.stats['desc_misses'] += 1
                            continue

                        self.stats['desc_hits'] += 1
                        # Extract emails immediately
                        subs = result.get('subscribers', 0)
                        if not self._passes_sub_filter(subs):
                            continue
                        emails_found = await extract_emails_async(result['description'])
                        for email in emails_found:
                            if email not in self.seen_emails and self._total_scraped < max_emails:
                                self.seen_emails.add(email)
                                await self._add_email(email, result, ch)
                                self._last_email_time = time.time()  # Update stall tracker
                                # Track keyword success
                                title = ch.get('video_title', '')
                                if title in self._keyword_hits:
                                    self._keyword_hits[title] += 1
                                    if self._keyword_hits[title] >= 2 and title not in self._best_keywords:
                                        self._best_keywords.append(title)

                    del raw


            async def status_reporter():
                """Periodically print status while pipeline is running."""
                report_interval = 10  # seconds
                while not stop_event.is_set() and self._total_scraped < max_emails:
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=report_interval)
                        return  # stop_event was set
                    except asyncio.TimeoutError:
                        pass  # Time to report
                    elapsed = time.time() - start_time
                    rate = self._total_scraped / (elapsed / 60) if elapsed > 0 else 0
                    remaining = self._time_left()
                    print(f"\n  [LAGIC] ── Status ({elapsed:.0f}s | {remaining:.0f}s left) ──")
                    print(f"  [LAGIC] 📧 {self._total_scraped}/{max_emails} | {rate:.1f}/min | "
                          f"Ch: {self.stats['channels_scanned']} | "
                          f"Q: {channel_queue.qsize()} | "
                          f"KW: {len(keyword_queue)}")
                    hit_pct = self.stats['desc_hits'] / max(self.stats['channels_scanned'], 1)
                    print(f"  [LAGIC] 📊 Desc hit: {self.stats['desc_hits']}/"
                          f"{self.stats['channels_scanned']} ({hit_pct:.0%}) | "
                          f"{self.proxy_manager.get_summary_line()}")

            print(f"  Pipeline: search({self.search_workers}) + expand + fetch(x{config.NUM_FETCH_WORKERS}) + status")

            # Shared CffiSession for all desc fetch workers
            desc_profile = Fingerprint.get_profile()
            async with CffiSession(impersonate=desc_profile.impersonate_target, max_clients=config.CFFI_MAX_CLIENTS) as shared_cffi:
                try:
                    pipeline_tasks = [
                        asyncio.create_task(keyword_expander()),
                        asyncio.create_task(yt_search_producer()),
                        asyncio.create_task(status_reporter()),
                    ]
                    for i in range(config.NUM_FETCH_WORKERS):
                        pipeline_tasks.append(asyncio.create_task(desc_fetch_worker(i, shared_cffi)))

                    # Poll until target reached, time runs out, or all tasks finish
                    deadline = start_time + total_budget - 5
                    while time.time() < deadline:
                        if stop_event.is_set():
                            # Give workers 3s to finish current batch after stop
                            await asyncio.sleep(3)
                            break
                        if len(self.results) >= max_emails:
                            stop_event.set()
                            await asyncio.sleep(2)
                            break
                        if all(t.done() for t in pipeline_tasks):
                            break
                        await asyncio.sleep(1)

                except asyncio.CancelledError:
                    # External cancellation (stop button) — kill everything NOW
                    print("  🛑 External cancel received — shutting down all pipeline tasks...")
                    stop_event.set()
                    for task in pipeline_tasks:
                        if not task.done():
                            task.cancel()
                    try:
                        await asyncio.wait_for(
                            asyncio.gather(*pipeline_tasks, return_exceptions=True),
                            timeout=3
                        )
                    except (asyncio.TimeoutError, Exception):
                        pass
                    raise  # Re-raise so the worker catches CancelledError

                except Exception as e:
                    stop_event.set()
                    print(f"  ⚠️ Pipeline error: {e}")
                    for task in pipeline_tasks:
                        if not task.done():
                            task.cancel()
                    try:
                        await asyncio.wait_for(
                            asyncio.gather(*pipeline_tasks, return_exceptions=True),
                            timeout=5
                        )
                    except (asyncio.TimeoutError, Exception):
                        pass

                finally:
                    # Signal everything to stop
                    stop_event.set()

                    # Cancel all tasks
                    for task in pipeline_tasks:
                        if not task.done():
                            task.cancel()

                    # Wait briefly for cancellation (suppress CancelledError)
                    try:
                        await asyncio.wait_for(
                            asyncio.gather(*pipeline_tasks, return_exceptions=True),
                            timeout=5
                        )
                    except (asyncio.TimeoutError, Exception):
                        pass  # Don't hang on unkillable thread pool tasks

        # ── Final Stats ──────────────────────────────────────────────────────
        total_time = time.time() - start_time
        yt_q = self.stats['yt_search_queries']
        yt_r = self.stats['yt_search_results']
        ch_scanned = max(self.stats['channels_scanned'], 1)
        n_results = len(self.results)
        push_count = getattr(self, '_push_count', 0)
        rate = n_results / (total_time / 60) if total_time > 0 else 0

        # Final push count
        if push_count > 0:
            print(f"\n  [LAGIC] 📤 Final push: {push_count} total items pushed")

        print(f"\n{'='*70}")
        print(f"  [LAGIC] FINAL RESULTS — {n_results} emails in {total_time:.0f}s")
        print(f"{'='*70}")
        print(f"  📧 Emails:       {n_results}")
        print(f"  📺 Channels:     {self.stats['channels_scanned']}")
        print(f"  🔍 YT Search:    {yt_q} queries → {yt_r} channels")
        print(f"  🌐 Desc hit:     {self.stats['desc_hits']}/{self.stats['channels_scanned']}")
        if total_time > 0:
            print(f"  ⚡ Rate:         {rate:.1f} emails/min")
            print(f"  ⚡ Ch/sec:       {self.stats['channels_scanned']/total_time:.1f}")
        print(f"  📊 Yield:        {n_results/ch_scanned:.1%}")

        # Data completeness metrics
        social_pct = self.stats['results_with_socials'] / max(n_results, 1)
        avg_subs = sum(r.get('subscribers', 0) for r in self.results) / max(n_results, 1)
        print(f"  ── Data Completeness ──")
        print(f"    Social links:  {self.stats['results_with_socials']}/{n_results} ({social_pct:.0%})")
        print(f"    Total socials: {self.stats['total_social_links']}")
        print(f"    Avg subs:      {avg_subs:,.0f}")
        print(f"    Memory:        {self._get_memory_mb():.0f}MB / {config.MEMORY_LIMIT_MB}MB")

        # Extraction path breakdown
        yt_init = self.stats.get('extract_ytInitialData', 0)
        html_fb = self.stats.get('extract_html_fallback', 0)
        ext_fail = self.stats.get('extract_failed', 0)
        print(f"  ── Extraction Paths ──")
        print(f"    ytInitialData: {yt_init}")
        print(f"    HTML fallback: {html_fb}")
        print(f"    Failed:        {ext_fail}")

        # Error classification
        e429 = self.stats.get('errors_429', 0)
        e5xx = self.stats.get('errors_5xx', 0)
        etimeout = self.stats.get('errors_timeout', 0)
        eparse = self.stats.get('errors_parse', 0)
        if e429 or e5xx or etimeout or eparse:
            print(f"  ── Errors ──")
            if e429: print(f"    429 (rate limit): {e429}")
            if e5xx: print(f"    5xx (server):     {e5xx}")
            if etimeout: print(f"    Timeout:          {etimeout}")
            if eparse: print(f"    Parse:            {eparse}")

        # Diagnostic breakdown of desc fetch failures
        diag_keys = [k for k in self.stats if k.startswith('diag_')]
        if diag_keys:
            print(f"  ── Desc Fetch Diagnostics ──")
            for k in sorted(diag_keys):
                print(f"    {k.replace('diag_', '')}: {self.stats[k]}")
        # Proxy health report
        if self.proxy_manager.has_proxies:
            print(f"  ── Proxy Health ──")
            print(self.proxy_manager.get_health_report())
        print(f"{'='*70}")
        print(f"  [LAGIC] Complete: {n_results} results in {total_time:.0f}s ({rate:.0f}/min)")


        return self.results
