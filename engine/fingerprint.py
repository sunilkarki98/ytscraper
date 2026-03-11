"""
Fingerprint — Stealth Request Randomization (v24)
==================================================
Advanced fingerprinting to prevent cross-IP correlation and bypass anti-bot protections.
Features:
  • Modern Client Hints (Sec-CH-UA, Sec-CH-UA-Platform, Sec-CH-UA-Mobile)
  • Structured profiles ensuring UA, Client Hints, and TLS impersonation ALIGN
  • Fresh User-Agents (Chrome 120+, Safari 17+, Edge 120+)
  • curl_cffi targets up to chrome124
"""
import random
from typing import Dict, List, NamedTuple, Optional

class BrowserProfile(NamedTuple):
    user_agent: str
    sec_ch_ua: str
    sec_ch_ua_platform: str
    sec_ch_ua_mobile: str = "?0"
    impersonate_target: str = "chrome124"  # Default fallback

# ─── BROWSER PROFILES ────────────────────────────────────────────────
# Each profile MUST link a specific UA to its correct Client Hints and TLS target.
# This prevents "impossible" combinations (e.g. Chrome 124 UA with Chrome 110 TLS).

PROFILES: List[BrowserProfile] = [
    # ── Chrome 131 (Windows) ──
    BrowserProfile(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        sec_ch_ua='"Chromium";v="131", "Google Chrome";v="131", "Not-A.Brand";v="24"',
        sec_ch_ua_platform='"Windows"',
        impersonate_target="chrome124"
    ),
    # ── Chrome 131 (macOS) ──
    BrowserProfile(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        sec_ch_ua='"Chromium";v="131", "Google Chrome";v="131", "Not-A.Brand";v="24"',
        sec_ch_ua_platform='"macOS"',
        impersonate_target="chrome124"
    ),
    # ── Chrome 130 (Windows) ──
    BrowserProfile(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        sec_ch_ua='"Chromium";v="130", "Google Chrome";v="130", "Not-A.Brand";v="24"',
        sec_ch_ua_platform='"Windows"',
        impersonate_target="chrome124"
    ),
    # ── Edge 131 (Windows) ──
    BrowserProfile(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
        sec_ch_ua='"Chromium";v="131", "Microsoft Edge";v="131", "Not-A.Brand";v="24"',
        sec_ch_ua_platform='"Windows"',
        impersonate_target="edge99"  # Best available Edge target
    ),
    # ── Safari 17.4 (macOS) ──
    # Note: Safari does not send Sec-CH-UA headers by default, but we set appropriate TLS
    BrowserProfile(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
        sec_ch_ua="",  # Safari doesn't use Client Hints
        sec_ch_ua_platform='"macOS"', # Sometimes sent, safer to omit if empty
        impersonate_target="safari17_0"
    ),
]

ACCEPT_LANGUAGES = [
    'en-US,en;q=0.9',
    'en-US,en;q=0.9,es;q=0.8',
    'en-GB,en;q=0.9,en-US;q=0.8',
    'en-US,en;q=0.9,fr;q=0.8',
    'en,en-US;q=0.9',
]

ACCEPT_HEADERS_CHROME = 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7'
ACCEPT_HEADERS_SAFARI = 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'

class Fingerprint:
    """Request fingerprint randomizer — prevents cross-IP correlation."""

    @staticmethod
    def get_profile() -> BrowserProfile:
        """Get a random consistent browser profile."""
        return random.choice(PROFILES)

    @staticmethod
    def get_headers(profile: Optional[BrowserProfile] = None) -> Dict[str, str]:
        """Get header dict for a specific profile (or random if None)."""
        if not profile:
            profile = random.choice(PROFILES)
        
        headers = {
            'User-Agent': profile.user_agent,
            'Accept-Language': random.choice(ACCEPT_LANGUAGES),
        }
        
        # Add Client Hints if applicable (Chrome/Edge)
        if profile.sec_ch_ua:
            headers['sec-ch-ua'] = profile.sec_ch_ua
            headers['sec-ch-ua-mobile'] = profile.sec_ch_ua_mobile
            headers['sec-ch-ua-platform'] = profile.sec_ch_ua_platform
            headers['Accept'] = ACCEPT_HEADERS_CHROME
            headers['sec-fetch-dest'] = 'document'
            headers['sec-fetch-mode'] = 'navigate'
            headers['sec-fetch-site'] = 'none'
            headers['sec-fetch-user'] = '?1'
            headers['upgrade-insecure-requests'] = '1'
        else:
            # Safari-style headers
            headers['Accept'] = ACCEPT_HEADERS_SAFARI
            
        return headers

    @staticmethod
    def get_yahoo_headers() -> Dict[str, str]:
        """Get randomized headers for Yahoo search (simpler)."""
        profile = random.choice(PROFILES)
        return {
            'User-Agent': profile.user_agent,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': random.choice(ACCEPT_LANGUAGES),
            'sec-ch-ua': profile.sec_ch_ua,
            'sec-ch-ua-mobile': profile.sec_ch_ua_mobile,
            'sec-ch-ua-platform': profile.sec_ch_ua_platform,
        }

    # Backward compatibility helpers
    @staticmethod
    def get_cffi_impersonate() -> str:
        """Get a random curl_cffi impersonation target (legacy use)."""
        return random.choice(PROFILES).impersonate_target
